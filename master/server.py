import asyncio
import logging

from loadbalancer.load_balancer import LoadBalancer, TaskRecord
from loadbalancer.task_pool import task_pool_loop
from networking.network_io import (
    read_one_packet,
    net_send_task,
    net_send_welcome,
    net_send_disconnect,
)
from networking.packets import (
    ControlPacket, TaskPacket,
    PACKET_FLAGS, DISCONNECT_FLAGS,
)

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

HOST = "0.0.0.0"
PORT = 9000
CPU_MAX_USAGE = 80.0


class WorkerConnection:
    """
    Represents a single connected worker.

    Iteration-6 additions vs the stub:
      - worker_id_int  : integer assigned from next_worker_id counter,
                         used inside ControlPacket headers on the wire.
      - _lock          : asyncio.Lock so concurrent sends never interleave.
    """

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                 worker_id: str, worker_id_int: int):
        self.reader = reader
        self.writer = writer
        addr = writer.get_extra_info("peername")
        self.address: tuple = addr
        self.worker_id: str = worker_id
        self.worker_id_int: int = worker_id_int
        self._lock = asyncio.Lock()

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


    def _assign_id(self) -> tuple[str, int]:
        n = self.next_worker_id
        self.next_worker_id += 1
        return f"worker-{n}", n

    async def _lb_send(self, worker_id: str, task: TaskRecord) -> bool:
        """
        Called by the LB dispatch loop to send a task to a worker.
        Returns True on success (LB marks worker BUSY).
        Returns False on failure (LB re-queues the task).
        """
        conn = self.workers.get(worker_id)
        if conn is None:
            logger.warning("[lb_send] worker %s not found", worker_id)
            return False
        try:
            await net_send_task(conn.writer, conn._lock,
                                task.payload, priority=task.priority)
            # map payload uuid → TaskRecord uuid so result lookup works
            if hasattr(task.payload, "task_id"):
                self.payload_record[task.payload.task_id] = task.task_id
            logger.info("[lb_send] task %.8s → %s", task.task_id, worker_id)
            return True
        except (OSError, BrokenPipeError, ConnectionResetError) as exc:
            logger.error("[lb_send] failed  task=%.8s  worker=%s  err=%s",
                         task.task_id, worker_id, exc)
            return False

    async def _force_disconnect(self, worker_id: str) -> None:
        """
        Called by LB kick_worker.
        Sends ctrl_disconnect to the worker FIRST so it clears its tasks/UI,
        then closes the TCP connection on our side.
        """
        conn = self.workers.get(worker_id)
        if conn:
            try:
                await net_send_disconnect(conn.writer, conn._lock, conn.worker_id_int, DISCONNECT_FLAGS.unknown)
            except OSError:
                pass  # already gone
            self._disconnect(conn)

    # --------per connection handler -------------------------------------------------------

    async def _handle_worker(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """
        Called automatically for every new TCP connection.
        Performs the HELLO/WELCOME handshake, registers with LB, runs read loop.
        """
        addr = writer.get_extra_info("peername")

        # handshake: expect ctrl_hello from worker
        try:
            pkt = await read_one_packet(reader)
        except (OSError, ValueError) as exc:
            logger.warning("Handshake went wrong from %s: %s", addr, exc)
            writer.close()
            return

        if (not isinstance(pkt, ControlPacket) or pkt.packet_type != PACKET_FLAGS.ctrl_hello):
            logger.warning("Expected ctrl_hello from %s, got %s — closing", addr, pkt)
            writer.close()
            return 

        worker_id_str, worker_id_int = self._assign_id()
        conn = WorkerConnection(reader, writer, worker_id_str, worker_id_int)
        self.workers[worker_id_str] = conn

        # reply with ctrl_welcome carrying the assigned integer id
        await net_send_welcome(conn.writer, conn._lock, worker_id_int)

        host, port = conn.address
        logger.info(f"Worker connected: {conn.worker_id} , total workers = {len(self.workers)}")
        await self.lb.register_worker(worker_id_str, host, port)

        try:
            await self._session(conn)
        finally:
            self._disconnect(conn)

    async def _session(self, conn: WorkerConnection) -> None:
        """
        Read loop for one worker.
        Handles ctrl_status (CPU%), ctrl_disconnect, task_result, subtask_result.
        """
        while True:
            try:
                pkt = await read_one_packet(conn.reader)
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
        Unpacks the full task object from the payload — the real uuid (lb key) and result
        are both inside the pickle, not in the header.
        """
        if pkt.packet_type not in (PACKET_FLAGS.task_result, PACKET_FLAGS.subtask_result):
            logger.warning("[%s] got wrong task packet type: %s", conn.worker_id, pkt.packet_type.name)
            return

        try:
            task_obj = pkt.unpack_payload()
        except Exception as exc:
            logger.error("[%s] unpack_payload failed: %s", conn.worker_id, exc)
            await self.lb.task_failed(conn.worker_id, str(pkt.task_id), f"unpack_error: {exc}") #let the lb know this task unpacking failed, so it can re-queue or mark failed as needed.
            return

        payload_uuid = task_obj.task_id  # uuid from the task object itself
        result       = task_obj.result

        # translate payload uuid → TaskRecord uuid that the LB uses as key
        lb_task_id = self.payload_record.pop(payload_uuid, None)
        if lb_task_id is None:
            logger.error("[%s] no TaskRecord mapping for payload uuid %.8s — result dropped", conn.worker_id, payload_uuid)
            return

        logger.info("[%s] task_result  payload=%.8s  record=%.8s", conn.worker_id, payload_uuid, lb_task_id)

        if task_obj.status in ("FAILD", "FAILED"):
            logger.warning("[%s] task %.8s reported failed", conn.worker_id, payload_uuid)
            err = getattr(task_obj, "error", "task_failed")
            await self.lb.task_failed(conn.worker_id, lb_task_id, err or "task_failed")
            return

        await self.lb.task_completed(conn.worker_id, lb_task_id, result)

    #-------say good bye ----------------------------------------------------------------
    def _disconnect(self, conn: WorkerConnection) -> None:
        """
        Clean up after a worker disconnects.
        Sets the worker OFFLINE in the LB and fires _fire_worker_change
        BEFORE popping from the LB, so the master UI gets the OFFLINE event
        and removes the row. (unregister_worker pops without firing the callback.)
        """
        # mark OFFLINE in LB so UI removes the row
        worker_state = self.lb.workers.get(conn.worker_id)
        if worker_state:
            from loadbalancer.load_balancer import WorkerStatus
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
            