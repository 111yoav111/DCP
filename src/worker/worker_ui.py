import os
import tkinter as tk
from tkinter import messagebox
from PIL import Image, ImageTk

APP_TITLE   = "DCP - Dynamic Compute Power"
WIN_WIDTH   = 1080
WIN_HEIGHT  = 700

FONT_TITLE  = ("Bahnschrift", 20, "bold")
FONT_HEADER = ("Bahnschrift", 14, "bold")
FONT_BODY   = ("Bahnschrift", 12)
FONT_SMALL  = ("Bahnschrift", 10)

COL_HEADER  = "#d0d0d0"
COL_ROW_ODD = "#f9f9f9"
COL_ROW_EVN = "#ffffff"
BTN_REMOVE  = "#e05555"
BTN_CONNECT = "#4a7cff"

ASSETS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "assets")

class LoginFrame(tk.Frame):

    def __init__(self, parent, on_connect):
        super().__init__(parent)
        self.on_connect_callback = on_connect
        self.bg_image = None
        self.logo_image = None
        self.build()

    def load_logo(self, width=140, height=140):
        logo_path = os.path.join(ASSETS_DIR, "logo1.png")
        if not os.path.exists(logo_path):
            print(f"Logo not found at {logo_path}")
            return None
        img = Image.open(logo_path).resize((width, height))
        self.logo_image = ImageTk.PhotoImage(img)
        return self.logo_image

    def build(self):
        bg_path = os.path.join(ASSETS_DIR, "worker_background.png")
        if os.path.exists(bg_path):
            img = Image.open(bg_path).resize((WIN_WIDTH, WIN_HEIGHT))
            self.bg_image = ImageTk.PhotoImage(img)
            tk.Label(self, image=self.bg_image).place(x=0, y=0, relwidth=1, relheight=1)

        tk.Label(self, text=APP_TITLE, font=("Bahnschrift", 26, "bold")).pack(pady=(30, 10))

        logo = self.load_logo()
        if logo:
            tk.Label(self, image=logo, bd=0).pack(pady=10)

        mode_frame = tk.Frame(self)
        mode_frame.pack(pady=5)
        tk.Label(mode_frame, text="Mode:", font=FONT_BODY).pack(side="left", padx=5)
        tk.Label(mode_frame, text="Worker", font=FONT_BODY, relief="sunken", width=12).pack(side="left")

        # empty default — user must type the real master IP
        vcmd_ip   = (self.register(lambda P: len(P) <= 15), "%P")
        vcmd_port = (self.register(lambda P: len(P) <= 5),  "%P")

        self.ip = tk.StringVar(value="")
        ip_frame = tk.Frame(self)
        ip_frame.pack(pady=5)
        tk.Label(ip_frame, text="IP:", font=FONT_BODY, width=6).pack(side="left")
        tk.Entry(ip_frame, textvariable=self.ip, width=18, font=FONT_BODY,
                 validate="key", validatecommand=vcmd_ip).pack(side="left")

        self.port = tk.StringVar(value="9000")
        port_frame = tk.Frame(self)
        port_frame.pack(pady=5)
        tk.Label(port_frame, text="PORT:", font=FONT_BODY, width=6).pack(side="left")
        tk.Entry(port_frame, textvariable=self.port, width=18, font=FONT_BODY,
                 validate="key", validatecommand=vcmd_port).pack(side="left")

        tk.Button(self, text="Connect", font=FONT_HEADER, bg=BTN_CONNECT,
                  fg="white", width=14, command=self.connect).pack(pady=20)

    def connect(self):
        ip   = self.ip.get().strip()
        port = self.port.get().strip()
        if len(ip) > 15 or len(port) > 5:
            messagebox.showwarning("Input too long", "IP or PORT input is too long.")
            return
        if not ip or not port:
            messagebox.showwarning("Missing info", "Please fill in IP and PORT.")
            return
        if not port.isdigit():
            messagebox.showwarning("Bad PORT", "PORT must be a number.")
            return
        self.on_connect_callback(ip, int(port))


class ComputePanel(tk.Frame):
    """
    Main worker screen Shows a list of assigned tasks and their status, and allows remove them.
    """

    COLUMNS = [("Mission", 200), ("Status - CPU%", 160), ("Remove", 80)]

    def __init__(self, parent, on_remove_task):
        super().__init__(parent)
        self.on_remove_task_callback = on_remove_task
        self.rows: dict = {}
        self.logo_image = None
        self.build()

    def load_logo(self, width=200, height=140):
        logo_path = os.path.join(ASSETS_DIR, "logo1.png")
        if not os.path.exists(logo_path):
            return None
        img = Image.open(logo_path).resize((width, height))
        self.logo_image = ImageTk.PhotoImage(img)
        return self.logo_image

    def build(self):
        # title bar
        title_bar = tk.Frame(self)
        title_bar.pack(fill="x", padx=10, pady=(10, 5))
        logo = self.load_logo()
        if logo:
            tk.Label(title_bar, image=logo, bd=0).pack(side="left", padx=5)
        tk.Label(title_bar, text="Compute Panel", font=FONT_TITLE).pack(side="left", padx=15)
        self.worker_id_label = tk.Label(title_bar, text="", font=FONT_TITLE, fg="#3B4226")
        self.worker_id_label.pack(side="left", padx=5)
        self.conn_label = tk.Label(title_bar, text="Connected", font=FONT_TITLE, fg="green")
        self.conn_label.pack(side="right", padx=10)

        # static column headers (not scrolled)
        header = tk.Frame(self, bg=COL_HEADER)
        header.pack(fill="x", padx=10)
        for text, width in self.COLUMNS:
            tk.Label(header, text=text, font=FONT_HEADER, bg=COL_HEADER,
                     width=width // 8, anchor="w", relief="groove", bd=1
                     ).pack(side="left", ipadx=4, ipady=3)

        # scrollable canvas for rows — this is what prevents the crash
        container = tk.Frame(self, relief="groove", bd=1)
        container.pack(fill="both", expand=True, padx=10, pady=5)

        self._canvas = tk.Canvas(container, bg=COL_ROW_ODD, highlightthickness=0)
        sb = tk.Scrollbar(container, orient="vertical", command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self._canvas.pack(side="left", fill="both", expand=True)

        # inner frame that holds the actual row widgets
        self._rows_frame = tk.Frame(self._canvas, bg=COL_ROW_ODD)
        self._canvas_win = self._canvas.create_window((0, 0), window=self._rows_frame, anchor="nw")

        self._rows_frame.bind("<Configure>", lambda e: self._canvas.configure(scrollregion=self._canvas.bbox("all")))
        self._canvas.bind("<Configure>", lambda e: self._canvas.itemconfig(self._canvas_win, width=e.width))

    def add_or_update_task(self, task_id: str, mission_name: str, status_text: str):
        if task_id in self.rows:
            self.rows[task_id]["mission_lbl"].config(text=mission_name)
            self.rows[task_id]["status_lbl"].config(text=status_text)
            return

        bg = COL_ROW_ODD if len(self.rows) % 2 == 0 else COL_ROW_EVN
        row_frame = tk.Frame(self._rows_frame, bg=bg)
        row_frame.pack(fill="x")

        mission_lbl = tk.Label(row_frame, text=mission_name, font=FONT_BODY, bg=bg, anchor="w", width=27)
        status_lbl  = tk.Label(row_frame, text=status_text,  font=FONT_BODY, bg=bg, anchor="w", width=20)
        remove_btn  = tk.Button(row_frame, text="Remove", font=FONT_SMALL,
                                bg=BTN_REMOVE, fg="white",
                                command=lambda tid=task_id: self.on_remove_task_callback(tid))

        mission_lbl.pack(side="left", padx=4, pady=2)
        status_lbl.pack(side="left", padx=4)
        remove_btn.pack(side="left", padx=4)

        self.rows[task_id] = {"frame": row_frame, "mission_lbl": mission_lbl, "status_lbl": status_lbl}

    def remove_task(self, task_id: str):
        if task_id not in self.rows:
            return
        self.rows[task_id]["frame"].destroy()
        del self.rows[task_id]

    def set_connection_status(self, text: str, color: str = "green"):
        self.conn_label.config(text=f"{text}", fg=color)

    def set_worker_id(self, worker_id: str):
        self.worker_id_label.config(text=f"-> {worker_id}")


class WorkerUI:

    def __init__(self):
        self.root = tk.Tk()
        self.root.title(APP_TITLE)
        self.root.geometry(f"{WIN_WIDTH}x{WIN_HEIGHT}")
        self.root.resizable(True, True)
        self.compute_panel = None
        self.connect_callback = None
        self.cancel_callback = None  # set by main.py to client.cancel_task
        self.show_login()

    def show_login(self):
        self.login = LoginFrame(self.root, on_connect=self.on_connect)
        self.login.pack(fill="both", expand=True)

    def show_compute_panel(self):
        self.login.destroy()
        self.compute_panel = ComputePanel(self.root, on_remove_task=self.on_remove_task)
        self.compute_panel.pack(fill="both", expand=True)

    def on_connect(self, ip: str, port: int):
        print(f"[WorkerUI] Connect clicked — master={ip}:{port}")
        self.show_compute_panel()
        if self.connect_callback:
            self.connect_callback(ip, port)

    def set_connect_callback(self, fn):
        self.connect_callback = fn

    def set_cancel_callback(self, fn):
        self.cancel_callback = fn

    def on_remove_task(self, task_id: str):
        confirmed = messagebox.askyesno("Remove task", f"Remove task {task_id[:8]}...?")
        if confirmed and self.compute_panel:
            if self.cancel_callback:
                self.cancel_callback(task_id)  # cancel the running future in the client
            self.compute_panel.remove_task(task_id)

    def on_task_update(self, task_id: str, mission_name: str, status_text: str):
        """Add or update a task row — safe to call from any thread."""
        self.root.after(0, self._apply_task_update, task_id, mission_name, status_text)

    def _apply_task_update(self, task_id: str, mission_name: str, status_text: str):
        if self.compute_panel:
            self.compute_panel.add_or_update_task(task_id, mission_name, status_text)

    def on_task_remove(self, task_id: str):
        """Remove a task row — safe to call from any thread."""
        self.root.after(0, self._apply_task_remove, task_id)

    def _apply_task_remove(self, task_id: str):
        if self.compute_panel:
            self.compute_panel.remove_task(task_id)

    def on_clear_all_tasks(self):
        """Remove ALL task rows at once — called when master kicks this worker."""
        self.root.after(0, self._apply_clear_all_tasks)

    def _apply_clear_all_tasks(self):
        if self.compute_panel:
            # copy keys first since remove_task mutates the dict
            for task_id in list(self.compute_panel.rows.keys()):
                self.compute_panel.remove_task(task_id)

    def on_connection_change(self, text: str, color: str = "green"):
        """Update connection status label — safe to call from any thread."""
        self.root.after(0, self._apply_connection_change, text, color)

    def _apply_connection_change(self, text: str, color: str):
        if self.compute_panel:
            self.compute_panel.set_connection_status(text, color)

    def on_worker_id_assigned(self, worker_id: str):
        """Update the worker ID label once master assigns an ID — safe to call from any thread."""
        self.root.after(0, self._apply_worker_id, worker_id)

    def _apply_worker_id(self, worker_id: str):
        if self.compute_panel:
            self.compute_panel.set_worker_id(worker_id)

    def run(self):
        self.root.mainloop()
