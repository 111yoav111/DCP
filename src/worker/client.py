import asyncio
import logging
import os
import psutil
from dotenv import load_dotenv
from concurrent.futures import ProcessPoolExecutor

from src.networking.network_io import (
    read_one_packet,
    net_send_result,
    net_send_status,
    net_send_hello,
    net_send_disconnect,
)
from src.networking.packets import (
    ControlPacket, TaskPacket,
    PACKET_FLAGS, DISCONNECT_FLAGS,
)
from src.networking.encrypt_layer import SessionCrypto, send_token

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

RECONNECT_DELAY = 5  # seconds before retrying a lost connection
CPU_UPDATE_INTERVAL = 4  # seconds between CPU usage updates
LISTEN_PORT = 0  # placeholder sent in ctrl_hello (master never calls back)
MAX_TASKS_TO_HANDLE = 15  # max tasks running at the same time — prevents UI/memory overload

load_dotenv()
DCP_TOKEN = os.getenv("DCP_TOKEN")

class WorkerClient:
    """
    Async TCP client that connects to the master server.
    """
    def __init__(self, host: str, port: int, worker_id: str = "worker-1", ui=None):
        self.host = host
        self.port = port
        self.worker_id = worker_id
        self.ui = ui                         

        self.reader: asyncio.StreamReader | None = None
        self.writer: asyncio.StreamWriter | None = None
        self.lock:   asyncio.Lock = asyncio.Lock()
        self.worker_id_int: int = 0 #will be assigned by master after hello handshake
        self.task_to_handle = asyncio.Semaphore(MAX_TASKS_TO_HANDLE)  # cap concurrent task execution
        self.crypto: SessionCrypto | None = None  # AES session key — established during handshake
        self.task_futures: dict[str, asyncio.Future] = {}  # task_uuid : future(result), for canceling the task.

        cpu_count = os.cpu_count() or 2
        self.executor = ProcessPoolExecutor(max_workers=max(2, cpu_count - 1))

    #-------connection management ------------------------------------------------------------
    async def _connect(self) -> bool:
        """
        Open TCP connection to master (using the real host IP), send ctrl_hello, send the auth token.

        If token is right, continue, if not - bye

        wait for ctrl_welcome.  Returns True on success.
        """
        self.reader, self.writer = await asyncio.open_connection(
            self.host, self.port
        )
        self.lock = asyncio.Lock()   # fresh lock per connection

        # crypto handshake before anything else — establishes the shared AES key
        try:
            self.crypto = await SessionCrypto.worker_handshake(self.reader, self.writer, None)
        except Exception as exc:
            logger.error("[%s] crypto handshake failed: %s", self.worker_id, exc)
            return False

        await net_send_hello(self.writer, self.lock, LISTEN_PORT) # plaintext — no secrets, happens right after handshake

        if DCP_TOKEN is None:
            logger.error("[%s] DCP_TOKEN missing, not in .env", self.worker_id)
            return False  # _connect will not work - worker will get disconnect.
        await send_token(self.writer, DCP_TOKEN, self.crypto)  # send token to master.

        pkt = await read_one_packet(self.reader) # plaintext — ctrl_welcome must be readable before crypto is established - also nothing to hide there.
        if (not isinstance(pkt, ControlPacket)
                or pkt.packet_type != PACKET_FLAGS.ctrl_welcome):
            logger.error("[%s] expected ctrl_welcome, got %s", self.worker_id, pkt)
            return False

        self.worker_id_int = pkt.worker_id
        logger.info(f"[{self.worker_id}] Connected to master at {self.host}:{self.port} (assigned id={self.worker_id_int})")
        if self.ui:
            self.ui.on_connection_change("Connected", color="blue")
        return True

    def _close(self) -> None:
        if self.writer:
            self.writer.close()
            self.writer = None
            self.reader = None
        self.crypto = None
        # shut down the old executor to avoid leaking processes on reconnect, then create a fresh one
        self.executor.shutdown(wait=False)
        cpu_count = os.cpu_count() or 2  #how many cpu cores
        self.executor = ProcessPoolExecutor(max_workers=max(2, cpu_count - 1))  #how many process will run, based on how many cpu cores, always leave 1 core free.

    # -------CPU heartbeat loop ------------------------------------------------------------
    async def _cpu_heartbeat_loop(self) -> None:
        """
        Send ctrl_status (CPU%) every CPU_UPDATE_INTERVAL seconds - 4sec.
        Replaces the old plain-text send — same loop structure, binary protocol.
        """
        psutil.cpu_percent(interval=None)  #use of cpu%
        await asyncio.sleep(0.1)

        while True:
            cpu = psutil.cpu_percent(interval=None)
            if self.writer:  # connection may have dropped
                await net_send_status(self.writer, self.lock, self.worker_id_int, int(cpu), crypto=self.crypto)
            logger.info(f"[{self.worker_id}] CPU heartbeat sent — CPU: {cpu:.2f}%")
            await asyncio.sleep(CPU_UPDATE_INTERVAL)

    # -----task execution loop ------------------------------------------------------------
    async def _execute_task(self, pkt: TaskPacket) -> None:
        """
        Execute a task packet.
        Deserialise -> execute -> send result.

        runs on "run_in_executor" to avoid blocking the receive loop — allows running the heavy use tasks without freezing the UI or missing incoming packets from master.

        can run up to MAX_TASKS_TO_HANDLE tasks concurrently — had to do it bc UI froze (prob memory explode).

        futures are tracked in task_futures so cancel_task() can cancel them when the user hits remove task (UI) - bascily delete task future.
        """
        async with self.task_to_handle:
            # Deserialize first - beofre any UI update - so we have the real task_uuid.
            # Using pkt.task_id (wire int) for LOADING and then task.task_id (payload UUID)
            try:
                task = pkt.unpack_payload()
            except Exception as exc:
                logger.error("[%s] deserialise failed wire_id=%d: %s", self.worker_id, pkt.task_id, exc)
                return  # no task objec - nothing to show or send back

            task_uuid = task.task_id           # single key used for every UI call below
            base_name = type(task).__name__
            if getattr(task, "is_subtask", False) and task.subtask_index is not None:
                task_name = f"{base_name} [subtask {task.subtask_index + 1}]"
            else:
                task_name = base_name
            logger.info("[%s] executing %s  uuid=%.8s", self.worker_id, task_name, task_uuid)

            if self.ui:
                self.ui.on_task_update(task_uuid, task_name, "RUNNING")

            execute_loop = asyncio.get_running_loop()  # better than get_event_loop (more updated)
            task_future = execute_loop.run_in_executor(self.executor, task.execute)
            self.task_futures[task_uuid] = task_future  # register so it will be cancelable.

            try:
                result = await task_future
                task.result = result
                task.status = "DONE"
            except asyncio.CancelledError:
                # user hit remove — clean up time.
                logger.info("[%s] task %.8s cancelled by user", self.worker_id, task_uuid)
                task.status = "FAILED"
                return
            except Exception as exc:
                logger.error("[%s] task %.8s raised: %s", self.worker_id, task_uuid, exc)
                task.status = "FAILED"
                if self.ui:
                    self.ui.on_task_update(task_uuid, task_name, "FAILED")
                    self.ui.root.after(3000, self.ui.on_task_remove, task_uuid)
                if self.writer:
                    await net_send_result(self.writer, self.lock, task, crypto=self.crypto)
                return
            finally:
                self.task_futures.pop(task_uuid, None)  # always clean up the future slot

            logger.info("[%s] task %.8s done", self.worker_id, task_uuid)
            if self.ui:
                self.ui.on_task_update(task_uuid, task_name, "DONE")
                self.ui.root.after(2000, self.ui.on_task_remove, task_uuid) ## schedule removal with tkinter timer
            if self.writer:  # connection may have dropped while task was running in executor
                await net_send_result(self.writer, self.lock, task, crypto=self.crypto)

    def cancel_task(self, task_uuid: str) -> None:
        """
        Cancel a running task by uuid. Called when the user hit remove in the UI.

        Cancels the asyncio future — which raises CancelledError inside _execute_task, which then clean up without sending a result back to master.

        *ProcessPoolExecutor cant really kill the process mid-run, but for not awaiting for result - cancel() prevents it.
        """
        task_future = self.task_futures.get(task_uuid)
        if task_future:
            task_future.cancel()  # cancelt the future all back.
            logger.info("[%s] canceled task %.8s", self.worker_id, task_uuid)
        else:
            logger.debug("[%s] cancel_task: %.8s not found (either done or bugged)", self.worker_id, task_uuid)

    # -----recive loop ----------------------------------------------------------------
    async def _receive_loop(self) -> None:
        """
        Always wait for incoming packets from master.
        Each task_request fires its own asyncio Task — receive loop stays free.
        """
        while True:
            try:
                pkt = await read_one_packet(self.reader, crypto=self.crypto) #read one packet at time.
            except Exception as exc:
                logger.warning(f"[{self.worker_id}] Connection to master lost: {exc}")
                break

            if pkt is None:
                logger.warning(f"[{self.worker_id}] Connection to master lost.")
                break

            if isinstance(pkt, TaskPacket):
                if pkt.packet_type in (PACKET_FLAGS.task_request, PACKET_FLAGS.subtask_request): 
                    asyncio.create_task(self._execute_task(pkt)) 
                else:
                    logger.warning("[%s] unexpected TaskPacket type: %s", self.worker_id, pkt.packet_type.name)

            elif isinstance(pkt, ControlPacket):
                if pkt.packet_type == PACKET_FLAGS.ctrl_disconnect:
                    logger.info("[%s] master requested disconnect", self.worker_id)
                    if self.ui:
                        self.ui.on_clear_all_tasks() #clear UI tasks immediately since master is kicking worker.
                    break
                logger.debug("[%s] ctrl from master: %s", self.worker_id, pkt.packet_type.name)

    #-----main run loop-----------
    async def run(self) -> None:
        """
        Connect to master and keep retrying if connection drops.
        """
        while True:
            try:
                connected = await self._connect()
                if not connected:
                    raise ConnectionError("Handshake failed")

                tasks = [
                    asyncio.create_task(self._receive_loop()),
                    asyncio.create_task(self._cpu_heartbeat_loop()),
                ]

                finished_tasks, unfinished_tasks = await asyncio.wait( tasks, return_when=asyncio.FIRST_COMPLETED)

                for task in unfinished_tasks:
                    task.cancel()

                if self.writer:
                    try:
                        await net_send_disconnect(self.writer, self.lock, self.worker_id_int, DISCONNECT_FLAGS.clean, crypto=self.crypto) #disconnect from master
                    except Exception as exc:
                        logger.error(f"[{self.worker_id}] Error while sending disconnect: {exc}")
                        pass

            except Exception as e:
                logger.error(f"[{self.worker_id}] Could not connect: {e}. Retrying in {RECONNECT_DELAY}s...")
            finally:
                self._close()
                if self.ui:
                    self.ui.on_connection_change("Reconnecting…", color="black")

            await asyncio.sleep(RECONNECT_DELAY)
            