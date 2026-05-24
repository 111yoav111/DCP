import asyncio
import logging
import random

from loadbalancer.load_balancer import LoadBalancer, TaskRecord, WorkerStatus
from loadbalancer.task_pool import task_pool_loop
from networking.network_io import (
    read_one_packet,
    net_send_task,
    net_send_subtask,
    net_send_welcome,
    net_send_disconnect,
)
from networking.packets import (
    ControlPacket, TaskPacket,
    PACKET_FLAGS, DISCONNECT_FLAGS,
    build_subtask_packet, next_id,
)
from networking.encrypt_layer import SessionCrypto, generate_rsa_keypair
from tasks.divisible_task import DivisibleTask

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

HOST = "0.0.0.0"  # doesnt really matter, at school test just put ipv4 here
PORT = 9000
CPU_MAX_USAGE = 80.0


class WorkerConnection:
    """
    Represents a single connected worker.
    """
    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, worker_id: str, worker_id_int: int, crypto: SessionCrypto):
        self.reader = reader
        self.writer = writer
        addr = writer.get_extra_info("peername")
        self.address: tuple = addr
        self.worker_id: str = worker_id
        self.worker_id_int: int = worker_id_int
        self._lock = asyncio.Lock()
        self.crypto: SessionCrypto = crypto  # AES session key for this worker

    def close(self) -> None:
        self.writer.close()


class MasterServer:
    """
    Async TCP server that accepts multiple worker connections.
    Each worker is handled in its own asyncio Task.
    """
    def __init__(self, host: str = HOST, port: int = PORT):
        self.host = host
        self.port = port
        self.workers: dict[str, WorkerConnection] = {}  # worker_id : conn
        self.next_worker_id = 1
        self.lb = LoadBalancer()
        self.payload_record: dict[str, str] = {} #mapping of payload uuid to TaskRecord uuid - the LB own key.
        self.rsa_private_key = generate_rsa_keypair()  # one RSA key pair for all connections (every master-worker), generated once at startup
        logger.info("RSA key pair generated — ready for worker handshakes.")


    def _assign_id(self) -> tuple[str, int]:
        n = self.next_worker_id
        self.next_worker_id += 1
        return f"worker-{n}", n

    async def _lb_send(self, worker_id: str, task: TaskRecord) -> bool:
        """
        Called by the LB dispatch loop to send a task to a worker.

        If the task payload is a DivisibleTask AND at least MIN_WORKERS_TO_SPLIT available workers exist, task split to workers.
        
        The LB is informed via register_subtask_group so it can collect results and merge them.

        Returns True on success, False on failure (LB re-queues the task).
        """
        connection = self.workers.get(worker_id)
        if connection is None:
            logger.warning("[lb_send] worker %s not found", worker_id)
            return False

        payload = task.payload

        # --- split path ---
        if isinstance(payload, DivisibleTask) and random.random() < 0.45:
            available = [
                wid for wid, ws in self.lb.workers.items()
                if ws.is_available
            ]
            num_workers = len(available)

            if num_workers >= self.lb.MIN_WORKERS_TO_SPLIT:
                return await self._lb_send_split(task, available, payload)

        # --- normal single-worker path ---
        try:
            await net_send_task(
                connection.writer, connection._lock,
                payload, priority=task.priority, crypto=connection.crypto,
            )
            if hasattr(payload, "task_id"):
                self.payload_record[payload.task_id] = task.task_id
            logger.info("[lb_send] task %.8s → %s", task.task_id, worker_id)
            return True
        except (OSError, BrokenPipeError, ConnectionResetError) as exc:
            logger.error("[lb_send] failed  task=%.8s  worker=%s  err=%s",
                         task.task_id, worker_id, exc)
            return False

    async def _lb_send_split(self, task: TaskRecord, available_worker_ids: list[str], payload: DivisibleTask) -> bool:
        """
        Split a DivisibleTask across all available workers and dispatch each subtask.

        On any send failure the whole group is aborted and False is returned, so the LB re-queues the original task as a single unit.
        """
        num_workers = len(available_worker_ids)
        subtasks = payload.split_into_subtasks(num_workers)

        # split_into_subtasks may return [self] when the task isn't splittable
        if len(subtasks) == 1 and subtasks[0] is payload:
            # fall back to normal single send on the first available worker
            conn = self.workers.get(available_worker_ids[0])
            if conn is None:
                return False
            try:
                await net_send_task(conn.writer, conn._lock, payload,
                                    priority=task.priority, crypto=conn.crypto)
                self.payload_record[payload.task_id] = task.task_id
                return True
            except (OSError, BrokenPipeError, ConnectionResetError) as exc:
                logger.error("[lb_send_split] fallback send failed: %s", exc)
                return False

        # assign one worker per subtask (round-robin if fewer workers than subtasks)
        subtask_registry: list[tuple[str, int]] = []  # (payload_uuid, index)

        for idx, subtask in enumerate(subtasks):
            target_id = available_worker_ids[idx % num_workers]
            conn = self.workers.get(target_id)
            if conn is None:
                logger.error("[lb_send_split] worker %s vanished during split dispatch", target_id)
                # clean up already-sent subtasks from payload_record
                for uuid, _ in subtask_registry:
                    self.payload_record.pop(uuid, None)
                return False

            # assign a fresh wire-level integer id to the subtask
            subtask.task_id = str(subtask.task_id)  # keep payload uuid intact
            try:
                pkt_bytes = build_subtask_packet(
                    subtask,
                    parent_id=0,  #wire parent_id, LB knows via uuid
                    subtask_index=idx,
                    total_subtasks=len(subtasks),
                    priority=task.priority.value if hasattr(task.priority, "value") else int(task.priority),
                )
                await net_send_subtask(conn.writer, conn._lock, pkt_bytes, crypto=conn.crypto)
            except (OSError, BrokenPipeError, ConnectionResetError) as exc:
                logger.error("[lb_send_split] subtask %d send failed: %s", idx, exc)
                for uuid, _ in subtask_registry:
                    self.payload_record.pop(uuid, None)
                return False

            # map subtask payload uuid -> parent TaskRecord uuid for result 
            self.payload_record[subtask.task_id] = task.task_id
            subtask_registry.append((subtask.task_id, idx))

            # mark worker as busy with this subtask
            async with self.lb.workers_lock:
                ws = self.lb.workers.get(target_id)
                if ws:
                    ws.active_task_ids.add(subtask.task_id)
                    ws.status = WorkerStatus.BUSY

            logger.info("[lb_send_split] subtask %d/%d → %s  uuid=%.8s", idx + 1, len(subtasks), target_id, subtask.task_id)

        # register the group with the LB so it can merge on completion
        self.lb.register_subtask_group(task, payload, subtask_registry)
        logger.info("[lb_send_split] dispatched %d subtasks for parent %.8s", len(subtasks), task.task_id)

        return True


    async def _force_disconnect(self, worker_id: str) -> None:
        """
        Called by LB kick_worker.
        Sends ctrl_disconnect to the worker FIRST so it clears its tasks/UI,
        then closes the TCP connection on our side.
        """
        connection = self.workers.get(worker_id)
        if connection:
            try:
                await net_send_disconnect(connection.writer, connection._lock, connection.worker_id_int, DISCONNECT_FLAGS.unknown, crypto=connection.crypto)
            except OSError:
                pass  # already gone
            self._disconnect(connection)

    # --------per connection handler -------------------------------------------------------

    async def _handle_worker(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """
        Called automatically for every new TCP connection.
        Performs the crypto handshake, then HELLO/WELCOME, registers with LB, runs read loop.
        """
        addr = writer.get_extra_info("peername")

        # crypto handshake before anything else — establishes the shared AES key
        try:
            crypto = await SessionCrypto.master_handshake(reader, writer, self.rsa_private_key)
        except Exception as exc:
            logger.warning("Crypto handshake failed from %s: %s", addr, exc)
            writer.close()
            return

        # handshake: expect ctrl_hello from worker — plaintext, no reason to encrypt...
        try:
            pkt = await read_one_packet(reader) # plaintext — ctrl_hello carries nothing to encrypt and must be readable before crypto is established.
        except (OSError, ValueError) as exc:
            logger.warning("Handshake went wrong from %s: %s", addr, exc)
            writer.close()
            return

        if (not isinstance(pkt, ControlPacket) or pkt.packet_type != PACKET_FLAGS.ctrl_hello):
            logger.warning("Expected ctrl_hello from %s, got %s — closing", addr, pkt)
            writer.close()
            return 

        worker_id_str, worker_id_int = self._assign_id()
        connection = WorkerConnection(reader, writer, worker_id_str, worker_id_int, crypto)
        self.workers[worker_id_str] = connection

        # reply with ctrl_welcome carrying the assigned integer id — plaintext, no reason to encrypt...
        await net_send_welcome(connection.writer, connection._lock, worker_id_int)

        host, port = connection.address
        logger.info(f"Worker connected: {connection.worker_id} , total workers = {len(self.workers)}")
        await self.lb.register_worker(worker_id_str, host, port)

        try:
            await self._session(connection)
        finally:
            self._disconnect(connection)

    async def _session(self, conn: WorkerConnection) -> None:
        """
        Read loop for one worker.
        Handles ctrl_status (CPU%), ctrl_disconnect, task_result, subtask_result.
        """
        while True:
            try:
                pkt = await read_one_packet(conn.reader, crypto=conn.crypto)
            except Exception as exc:
                logger.warning("[%s] cat read - error: %s", conn.worker_id, exc)
                break

            if pkt is None:
                logger.info(f"{conn.worker_id} disconnected.")
                break

            if isinstance(pkt, ControlPacket):
                stop = await self._on_ctrl_packet(conn, pkt)
                if stop:
                    break
            elif isinstance(pkt, TaskPacket):
                await self._on_task_result(conn, pkt)

    #------pack handlers --------------------------------------------------------------

    async def _on_ctrl_packet(self, conn: WorkerConnection, pkt: ControlPacket) -> bool:
        """
        Handle a ControlPacket. Returns True if the session should end.
        """
        flag = pkt.packet_type

        if flag == PACKET_FLAGS.ctrl_status:
            cpu = float(pkt.cpu_precent)
            if cpu >= CPU_MAX_USAGE:
                logger.warning("[%s] CPU %.1f%% >= limit, disconnecting", conn.worker_id, cpu)
                return True
            logger.info(f"{conn.worker_id} CPU: {cpu} %")
            await self.lb.update_worker_stats(conn.worker_id, cpu)
            return False

        if flag == PACKET_FLAGS.ctrl_disconnect:
            logger.info(f"{conn.worker_id} disconnected.")
            return True

        if flag == PACKET_FLAGS.ctrl_heartbeat:
            logger.debug("[%s] heartbeat", conn.worker_id)
            return False

        logger.warning("[%s] unexpected ctrl: %s", conn.worker_id, flag.name)
        return False

    async def _on_task_result(self, conn: WorkerConnection, pkt: TaskPacket) -> None:
        """
        Handle task_result or subtask_result from a worker.

        - task_result    → routed to lb.task_completed / lb.task_failed  (normal path)
        - subtask_result → routed to lb.subtask_completed / lb.subtask_failed (merge path)

        The subtask_index comes from the packet header so the LB knows which slot to fill.
        """
        if pkt.packet_type not in (PACKET_FLAGS.task_result, PACKET_FLAGS.subtask_result):
            logger.warning("[%s] got wrong task packet type: %s", conn.worker_id, pkt.packet_type.name)
            return

        try:
            task_obj = pkt.unpack_payload()
        except Exception as exc:
            logger.error("[%s] unpack_payload failed: %s", conn.worker_id, exc)
            # we don't know if it was a subtask — use task_failed as safe fallback
            await self.lb.task_failed(conn.worker_id, str(pkt.task_id), f"unpack_error: {exc}")
            return

        payload_uuid  = task_obj.task_id
        is_subtask    = pkt.packet_type == PACKET_FLAGS.subtask_result
        subtask_index = pkt.subtask_index if pkt.subtask_index is not None else 0
        is_failed     = task_obj.status in ("FAILD", "FAILED")
        result        = getattr(task_obj, "result", None)

        if result is None and not is_failed:
            logger.error("[%s] task %.8s has no result — treating as failed", conn.worker_id, payload_uuid)
            if is_subtask:
                await self.lb.subtask_failed(conn.worker_id, payload_uuid, subtask_index, "missing_result")
            else:
                lb_task_id = self.payload_record.pop(payload_uuid, None)
                if lb_task_id:
                    await self.lb.task_failed(conn.worker_id, lb_task_id, "missing_result")
            return

        if is_subtask:
            # subtask path — LB looks up the parent via _subtask_to_parent
            logger.info("[%s] subtask_result  uuid=%.8s  idx=%d  failed=%s",
                        conn.worker_id, payload_uuid, subtask_index, is_failed)
            if is_failed:
                err = getattr(task_obj, "error", "subtask_failed") or "subtask_failed"
                await self.lb.subtask_failed(conn.worker_id, payload_uuid, subtask_index, err)
            else:
                # remove from payload_record since subtask_completed handles the parent mapping
                self.payload_record.pop(payload_uuid, None)
                await self.lb.subtask_completed(conn.worker_id, payload_uuid, subtask_index, result)
            return

        # normal task path — translate payload uuid → LB TaskRecord uuid
        lb_task_id = self.payload_record.pop(payload_uuid, None)
        if lb_task_id is None:
            logger.error("[%s] no TaskRecord mapping for payload uuid %.8s — dropped",
                         conn.worker_id, payload_uuid)
            return

        logger.info("[%s] task_result  payload=%.8s  record=%.8s", conn.worker_id, payload_uuid, lb_task_id)

        if is_failed:
            err = getattr(task_obj, "error", "task_failed") or "task_failed"
            await self.lb.task_failed(conn.worker_id, lb_task_id, err)
        else:
            await self.lb.task_completed(conn.worker_id, lb_task_id, result)


    #-------say good bye ----------------------------------------------------------------
    def _disconnect(self, conn: WorkerConnection) -> None:
        """
        Clean up after a worker disconnects.

        Sets the worker OFFLINE in the LB and fires _fire_worker_change
        BEFORE popping from the LB, so the master UI gets the OFFLINE event and removes the row. (unregister_worker pops without firing the callback.)
        """
        # mark OFFLINE in LB so UI removes the row — WorkerStatus already imported at top
        worker_state = self.lb.workers.get(conn.worker_id)
        if worker_state:
            worker_state.status = WorkerStatus.OFFLINE
            self.lb._fire_worker_change(worker_state)

        self.workers.pop(conn.worker_id, None)
        conn.close()
        logger.info(f"Worker removed: {conn.worker_id} , total workers = {len(self.workers)}")

    #-------start the server -------------------------------------------------------------------   
    async def start(self) -> None:
        self.lb.set_kick_callback(self._force_disconnect)
        self.lb.set_send_callback(self._lb_send)

        server = await asyncio.start_server(
            self._handle_worker, self.host, self.port
        )
        addr = server.sockets[0].getsockname()
        logger.info(f"Master listening on {addr[0]}:{addr[1]}")

        async with server:
            # run TCP server, LB, and task pool all together - gather.
            await asyncio.gather(
                server.serve_forever(),
                self.lb.start(),
                task_pool_loop(self.lb)
            )
