import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__)))) #solve all the import issue I had, shoutout github :)

import asyncio
import threading
from server import MasterServer
from master_ui import MasterUI


def main() -> None:
    server = MasterServer()

    def run_backend():
        #another thread to run, using Tkinter force you to run it on other thread.
        asyncio.run(server.start())

    backend_thread = threading.Thread(target=run_backend, daemon=True)
    backend_thread.start()

    ui = MasterUI(server.lb)
    ui.run()


if __name__ == "__main__":
    main()