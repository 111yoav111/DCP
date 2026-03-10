import asyncio
import logging

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

HOST = "0.0.0.0"
PORT = 9000


class WorkerConnection:
    """
    Represents a single connected worker.
    """

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self.reader = reader
        self.writer = writer
        addr = writer.get_extra_info("peername")
        self.address: tuple = addr
        self.worker_id: str = f"{addr[0]}:{addr[1]}"

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

    async def _handle_worker(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """
        Called automatically for every new worker that connects.
        Creates a WorkerConnection and starts a session loop for it.
        """
        worker = WorkerConnection(reader, writer)
        self.workers[worker.worker_id] = worker
        logger.info(f"Worker connected: {worker.worker_id} , total workers = {len(self.workers)}")

        try:
            await self._session(worker)
        finally:
            self._disconnect(worker)

    async def _session(self, worker: WorkerConnection) -> None:
        """Keep the connection alive and log anything the worker sends."""
        while True:
            message = await worker.receive()
            if message is None:
                logger.info(f"Worker {worker.worker_id} disconnected.")
                break
            logger.info(f"received from {worker.worker_id}: {message}")

    def _disconnect(self, worker: WorkerConnection) -> None:
        """Clean up after a worker disconnects."""
        self.workers.pop(worker.worker_id, None)
        worker.close()
        logger.info(f"Worker removed: {worker.worker_id} , total workers = {len(self.workers)}")

    async def start(self) -> None:
        server = await asyncio.start_server(
            self._handle_worker, self.host, self.port
        )
        addr = server.sockets[0].getsockname()
        logger.info(f"Master listening on {addr[0]}:{addr[1]}")

        async with server:
            await server.serve_forever()
