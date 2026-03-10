import asyncio
from server import MasterServer


def main() -> None:
    server = MasterServer()
    asyncio.run(server.start())

if __name__ == "__main__":
    main()