import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))  # shoutout github :)

import asyncio
import threading
from src.worker.client import WorkerClient
from src.worker.worker_ui import WorkerUI


def main() -> None:
    ui = WorkerUI()

    def on_connect(ip: str, port: int):
        client = WorkerClient(host=ip, port=port, ui=ui)
        ui.set_cancel_callback(client.cancel_task)  # wire Remove button to actually cancel the future
        #run the client on second thread
        def run_backend():
            asyncio.run(client.run())

        backend_thread = threading.Thread(target=run_backend, daemon=True)
        backend_thread.start()

        ui.on_connection_change("Connecting...", color="turquoise")

    ui.set_connect_callback(on_connect)

    # UI runs on main thread 
    ui.run()


if __name__ == "__main__":
    main()
    