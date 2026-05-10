import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__)))) #solve all the import issue I had, shoutout github :)


import asyncio
import threading
from client import WorkerClient
from worker_ui import WorkerUI


def main() -> None:
    ui = WorkerUI()

    def on_connect(ip: str, port: int):
        # pass ui so the client can push task/connection updates to the UI
        client = WorkerClient(host=ip, port=port)

        def run_backend():
            asyncio.run(client.run())

        backend_thread = threading.Thread(target=run_backend, daemon=True)
        backend_thread.start()

        ui.on_connection_change("Connecting...", color="orange")

    ui.set_connect_callback(on_connect)

    # UI runs on main thread — blocks until window is closed
    ui.run()


if __name__ == "__main__":
    main()