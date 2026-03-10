import asyncio
from client import WorkerClient


def main() -> None:
    client = WorkerClient(host="127.0.0.1", port=9000, worker_id="worker-1")
    asyncio.run(client.run())

if __name__ == "__main__":
    main()