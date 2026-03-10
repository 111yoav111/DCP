import asyncio
import logging

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

BUFFER_SIZE = 4096
RECONNECT_DELAY = 5  # seconds before retrying a lost connection


class WorkerClient:
    """
    Async TCP client that connects to the master server.
    Automatically attempts to reconnect on connection loss.
    """

    def __init__(self, host: str, port: int, worker_id: str = "worker-1"):
        self.host = host
        self.port = port
        self.worker_id = worker_id
        self._reader = None #private attributes for WorkerClient
        self._writer = None

    async def _connect(self) -> None:
        """
        Open the TCP connection to master.
        """
        self._reader, self._writer = await asyncio.open_connection(self.host, self.port)
        logger.info(f"[{self.worker_id}] Connected to master at {self.host}:{self.port}")

    async def _send(self, message: str) -> None:
        """
        Send a UTF-8 line to master.
        """
        if self._writer is None:
            raise RuntimeError("Not connected.")
        self._writer.write((message + "\n").encode("utf-8"))
        await self._writer.drain()

    async def _receive(self) -> str | None:
        """'
        Read one line from master. Returns None on closed connection.
        """
        if self._reader is None:
            return None
        try:
            data = await self._reader.readline()
            if not data:
                return None
            return data.decode("utf-8").strip()
        except (asyncio.IncompleteReadError, ConnectionResetError):
            return None

    def _close(self) -> None:
        if self._writer:
            self._writer.close()
            self._writer = None
            self._reader = None

    async def _session(self) -> None:
        """
        Keep connection alive and log anything master sends.
        """
        while True:
            message = await self._receive()
            if message is None:
                logger.warning(f"[{self.worker_id}] Connection to master lost.")
                break
            logger.info(f"[{self.worker_id}] received: {message}")

    async def run(self) -> None:
        """
        Connect to master and keep retrying if connection drops.
        """
        while True:
            try:
                await self._connect()
                await self._session()
            except (ConnectionRefusedError, OSError) as e:
                logger.error(
                    f"[{self.worker_id}] Could not connect: {e}. "
                    f"Retrying in {RECONNECT_DELAY}s..."
                )
            finally:
                self._close()

            await asyncio.sleep(RECONNECT_DELAY)
