import asyncio
import logging

from loadbalancer.load_balancer import *
from loadbalancer.task_pool import task_pool_loop

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
    """

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, worker_id : str):
        self.reader = reader
        self.writer = writer
        addr = writer.get_extra_info("peername") 
        self.address: tuple = addr # ^^ both of these lines are not strictly necessary, but they may help for debugging.
        self.worker_id: str = worker_id

    async def receive(self) -> str | None:
        try:
            data = await self.reader.readline()
            if not data:
                return None
            return data.decode("utf-8").strip()
        except (asyncio.IncompleteReadError, ConnectionResetError):
            return None

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
        self.workers: dict[str, WorkerConnection] = {}  # worker_id : connection
        self.next_worker_id = 1
        self.lb = LoadBalancer() #lb instance - have task queue + workermatching ability

    def _assign_id(self) -> str:
        worker_id = (f"worker-{self.next_worker_id}")
        self.next_worker_id += 1
        return worker_id

    async def _handle_worker(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """
        Called automatically for every new worker that connects.
        Creates a WorkerConnection and starts a session loop for it.
        """
        worker = WorkerConnection(reader, writer, self._assign_id())
        self.workers[worker.worker_id] = worker
        logger.info(f"Worker connected: {worker.worker_id} , total workers = {len(self.workers)}")
        host, port  = worker.address
        await self.lb.register_worker(worker.worker_id, host, port) #register with lb so it know worker exists

        try:
            await self._session(worker)
        finally:
            self._disconnect(worker)

    async def _session(self, worker: WorkerConnection) -> None:
        """Keep the connection alive and handle CPU updates from the worker."""
        while True:
            message = await worker.receive()
            if message is None:
                logger.info(f"{worker.worker_id} disconnected.")
                break
            try:
                worker_cpu = float(message)
            except ValueError:
                logger.warning(f"[{worker.worker_id}] bad CPU value - not a number: {message}")
                continue

            if worker_cpu >= CPU_MAX_USAGE:
                self._disconnect(worker)
            else:
                logger.info(f"{worker.worker_id} CPU: {worker_cpu} %")
                await self.lb.update_worker_stats(worker.worker_id, worker_cpu)


    def _disconnect(self, worker: WorkerConnection) -> None:
        """
        Clean up after a worker disconnects.
        """
        self.workers.pop(worker.worker_id, None)
        worker.close()
        logger.info(f"Worker removed: {worker.worker_id} , total workers = {len(self.workers)}")

    async def _force_disconnect(self, worker_id : str) -> None:
        """
        called by LB kick_worker - close the TCP connection
        """
        worker = self.workers.get(worker_id)
        if worker:
            self._disconnect(worker)

    async def start(self) -> None:
        self.lb.set_kick_callback(self._force_disconnect) #register the kick so LB can close TCP connection.

        # ── stub send callback (iteration 5 placeholder) ──────────────────────
        # real packet sending is built in iteration 6.
        # for now just log the dispatch and pretend it was sent successfully.
        async def _stub_send(worker_id: str, task) -> bool:
            logger.info(f"[STUB] would send task {task.task_id[:8]}... to {worker_id} (real send in iteration 6)")
            return True   # True = success, stops the infinite requeue loop

        self.lb.set_send_callback(_stub_send)

        server = await asyncio.start_server(
            self._handle_worker, self.host, self.port
        )
        addr = server.sockets[0].getsockname()
        logger.info(f"Master listening on {addr[0]}:{addr[1]}")

        async with server:
            #run TCP server, LB, and task pool all together
            await asyncio.gather(
                server.serve_forever(),
                self.lb.start(),
                task_pool_loop(self.lb)
            )
            