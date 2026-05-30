import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))  # shoutout github :)

import asyncio
import threading
from src.master.server import MasterServer
from src.master.master_ui import MasterUI


def main() -> None:
    server = MasterServer()
    backend_loop = asyncio.new_event_loop()  # create the loop that runs the backend thread 

    def run_backend(host : str, port : int):
        # another thread to run, using Tkinter force you to run it on other thread.
        asyncio.set_event_loop(backend_loop)  # bind the loop to his parent - the thread. 
        backend_loop.run_until_complete(server.start(host, port))

    ui = MasterUI(server.lb, backend_loop, on_start=run_backend)  # pass lb ,backend loop and callback to start - to UI.
    ui.run()


if __name__ == "__main__":
    main()