import os
import tkinter as tk
from tkinter import messagebox
from PIL import Image, ImageTk

from loadbalancer.load_balancer import *

APP_TITLE   = "AtlasCore"
WIN_WIDTH   = 1080
WIN_HEIGHT  = 700

FONT_TITLE  = ("Bahnschrift", 20, "bold")
FONT_HEADER = ("Bahnschrift", 14, "bold")
FONT_BODY   = ("Bahnschrift", 12)
FONT_SMALL  = ("Bahnschrift", 10)

COL_HEADER  = "#d0d0d0"
COL_ROW_ODD = "#f9f9f9"
COL_ROW_EVN = "#ffffff"

COL_POPUP   = "#fffbe6"

BTN_REMOVE  = "#e05555"
BTN_CONNECT = "#4a7cff"

ASSETS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),"assets")


class LoginFrame(tk.Frame):

    def __init__(self, parent, on_connect):
        super().__init__(parent)
        self.on_connect_callback = on_connect
        self.bg_image = None
        self.logo_image = None
        self.build()

    def load_logo(self, width=180, height=140):
        logo_path = os.path.join(ASSETS_DIR, "logo.png")
        if not os.path.exists(logo_path):
            print(f"Logo not found at {logo_path}")
            return None

        img = Image.open(logo_path).resize((width, height))

        self.logo_image = ImageTk.PhotoImage(img)

        return self.logo_image

    def build(self):

        bg_path = os.path.join(
            ASSETS_DIR,
            "login_background1.jpg"
        )

        if os.path.exists(bg_path):

            img = Image.open(bg_path).resize(
                (WIN_WIDTH, WIN_HEIGHT)
            )

            self.bg_image = ImageTk.PhotoImage(img)

            tk.Label(
                self,
                image=self.bg_image
            ).place(
                x=0,
                y=0,
                relwidth=1,
                relheight=1
            )

        tk.Label(
            self,
            text=APP_TITLE,
            font=("Bahnschrift", 32, "bold")
        ).pack(pady=(30, 10))

        logo = self.load_logo()

        if logo:

            tk.Label(
                self,
                image=logo,
                bd=0
            ).pack(pady=10)

        mode_frame = tk.Frame(self)
        mode_frame.pack(pady=5)

        tk.Label(
            mode_frame,
            text="Mode:",
            font=FONT_BODY
        ).pack(side="left", padx=5)

        tk.Label(
            mode_frame,
            text="Master",
            font=FONT_BODY,
            relief="sunken",
            width=12
        ).pack(side="left")

        self.ip = tk.StringVar(value="0.0.0.0")

        ip_frame = tk.Frame(self)
        ip_frame.pack(pady=5)

        tk.Label(
            ip_frame,
            text="IP:",
            font=FONT_BODY,
            width=6
        ).pack(side="left")

        tk.Entry(
            ip_frame,
            textvariable=self.ip,
            width=18,
            font=FONT_BODY
        ).pack(side="left")

        self.port = tk.StringVar(value="9000")

        port_frame = tk.Frame(self)
        port_frame.pack(pady=5)

        tk.Label(
            port_frame,
            text="PORT:",
            font=FONT_BODY,
            width=6
        ).pack(side="left")

        tk.Entry(
            port_frame,
            textvariable=self.port,
            width=18,
            font=FONT_BODY
        ).pack(side="left")

        tk.Button(
            self,
            text="Connect",
            font=FONT_HEADER,
            bg=BTN_CONNECT,
            fg="white",
            width=14,
            command=self.connect
        ).pack(pady=20)

    def connect(self):

        ip = self.ip.get().strip()
        port = self.port.get().strip()

        if not ip or not port:

            messagebox.showwarning(
                "Missing info",
                "Please fill in IP and PORT."
            )

            return

        if not port.isdigit():

            messagebox.showwarning(
                "Bad PORT",
                "PORT must be a number."
            )

            return

        self.on_connect_callback(ip, int(port))


class ControlPanel(tk.Frame):

    COLUMNS = [
        ("Identifier", 120),
        ("IP", 130),
        ("Status", 120),
        ("Remove", 80),
    ]

    def __init__(self, parent, on_kick):
        super().__init__(parent)

        self.on_kick_callback = on_kick

        self.rows = {}

        self.logo_image = None

        self.build()

    def load_logo(self, width=120, height=70):

        logo_path = os.path.join(ASSETS_DIR, "logo.png")

        if not os.path.exists(logo_path):
            return None

        img = Image.open(logo_path).resize((width, height))

        self.logo_image = ImageTk.PhotoImage(img)

        return self.logo_image

    def build(self):

        title_bar = tk.Frame(self)

        title_bar.pack(
            fill="x",
            padx=10,
            pady=(10, 5)
        )

        logo = self.load_logo()

        if logo:

            tk.Label(
                title_bar,
                image=logo,
                bd=0
            ).pack(side="left", padx=5)

        tk.Label(
            title_bar,
            text="Control Panel",
            font=FONT_TITLE
        ).pack(side="left", padx=15)

        body = tk.Frame(self)

        body.pack(
            fill="both",
            expand=True,
            padx=10,
            pady=5
        )

        self.table_frame = tk.Frame(
            body,
            relief="groove",
            bd=1
        )

        self.table_frame.pack(
            side="left",
            fill="both",
            expand=True
        )

        self.popup_frame = tk.Frame(
            body,
            relief="groove",
            bd=1,
            bg=COL_POPUP,
            width=160
        )

        self.popup_frame.pack(
            side="right",
            fill="y",
            padx=(8, 0)
        )

        self.popup_frame.pack_propagate(False)

        tk.Label(
            self.popup_frame,
            text="Popups",
            font=FONT_HEADER,
            bg=COL_POPUP
        ).pack(pady=5)

        self.popup_text = tk.Text(
            self.popup_frame,
            font=FONT_SMALL,
            bg=COL_POPUP,
            state="disabled",
            wrap="word",
            relief="flat",
            bd=0
        )

        self.popup_text.pack(
            fill="both",
            expand=True,
            padx=4,
            pady=4
        )

        self.build_table_header()

    def build_table_header(self):

        header = tk.Frame(
            self.table_frame,
            bg=COL_HEADER
        )

        header.pack(fill="x")

        for text, width in self.COLUMNS:

            tk.Label(
                header,
                text=text,
                font=FONT_HEADER,
                bg=COL_HEADER,
                width=width // 8,
                anchor="w",
                relief="groove",
                bd=1
            ).pack(side="left", ipadx=4, ipady=3)

    def add_or_update_worker(
        self,
        worker_id,
        ip,
        status_text
    ):

        if worker_id in self.rows:

            self.rows[worker_id]["ip_lbl"].config(
                text=ip
            )

            self.rows[worker_id]["status_lbl"].config(
                text=status_text
            )

            return

        bg = (
            COL_ROW_ODD
            if len(self.rows) % 2 == 0
            else COL_ROW_EVN
        )

        row_frame = tk.Frame(
            self.table_frame,
            bg=bg
        )

        row_frame.pack(fill="x")

        id_lbl = tk.Label(
            row_frame,
            text=worker_id,
            font=FONT_BODY,
            bg=bg,
            anchor="w",
            width=15
        )

        ip_lbl = tk.Label(
            row_frame,
            text=ip,
            font=FONT_BODY,
            bg=bg,
            anchor="w",
            width=16
        )

        status_lbl = tk.Label(
            row_frame,
            text=status_text,
            font=FONT_BODY,
            bg=bg,
            anchor="w",
            width=15
        )

        remove_btn = tk.Button(
            row_frame,
            text="Remove",
            font=FONT_SMALL,
            bg=BTN_REMOVE,
            fg="white",
            command=lambda wid=worker_id:
            self.on_kick_callback(wid)
        )

        id_lbl.pack(side="left", padx=4, pady=2)
        ip_lbl.pack(side="left", padx=4)
        status_lbl.pack(side="left", padx=4)
        remove_btn.pack(side="left", padx=4)

        self.rows[worker_id] = {
            "frame": row_frame,
            "ip_lbl": ip_lbl,
            "status_lbl": status_lbl,
        }

    def remove_worker(self, worker_id):

        if worker_id not in self.rows:
            return

        self.rows[worker_id]["frame"].destroy()

        del self.rows[worker_id]

    def add_popup(self, message):

        self.popup_text.config(state="normal")

        self.popup_text.insert(
            "end",
            f"• {message}\n"
        )

        self.popup_text.see("end")

        self.popup_text.config(state="disabled")


class MasterUI:

    def __init__(self, lb: LoadBalancer):

        self.lb = lb

        self.root = tk.Tk()

        self.root.title(APP_TITLE)

        self.root.geometry(
            f"{WIN_WIDTH}x{WIN_HEIGHT}"
        )

        self.root.resizable(True, True)

        self.control_panel = None

        self.show_login()

        self.wire_lb_callbacks()

    def show_login(self):

        self.login = LoginFrame(
            self.root,
            on_connect=self.on_connect
        )

        self.login.pack(
            fill="both",
            expand=True
        )

    def show_control_panel(self):

        self.login.destroy()

        self.control_panel = ControlPanel(
            self.root,
            on_kick=self.on_kick
        )

        self.control_panel.pack(
            fill="both",
            expand=True
        )

    def on_connect(self, ip, port):

        print(
            f"[MasterUI] Connect clicked , ip={ip} port={port}"
        )

        self.show_control_panel()

    def on_kick(self, worker_id):

        confirmed = messagebox.askyesno(
            "Remove worker",
            f"Are you sure you want to remove {worker_id}?"
        )

        if confirmed:

            import asyncio

            asyncio.get_event_loop().call_soon_threadsafe(
                asyncio.ensure_future,
                self.lb.kick_worker(
                    worker_id,
                    reason="manual_ui"
                )
            )

    def wire_lb_callbacks(self):

        self.lb.on_worker_status_change(
            self.on_worker_change
        )

        self.lb.on_metrics_update(
            self.on_metrics
        )

    def on_worker_change(self, ws: WorkerState):

        self.root.after(
            0,
            self.apply_worker_change,
            ws
        )

    def apply_worker_change(self, ws: WorkerState):

        if self.control_panel is None:
            return

        if ws.status.name == "OFFLINE":

            self.control_panel.remove_worker(
                ws.worker_id
            )

            self.control_panel.add_popup(
                f"{ws.worker_id} went offline."
            )

        else:

            ip_str = (
                f"{ws.address[0]}:{ws.address[1]}"
            )

            status_txt = (
                f"{ws.status.name}  "
                f"{ws.cpu_usage:.0f}%"
            )

            self.control_panel.add_or_update_worker(
                ws.worker_id,
                ip_str,
                status_txt
            )

    def on_metrics(self, metrics: LBMetrics):

        self.root.after(
            0,
            self.apply_metrics,
            metrics
        )

    def apply_metrics(self, metrics: LBMetrics):

        pass

    def run(self):

        self.root.mainloop()
