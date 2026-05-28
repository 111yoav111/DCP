import os
import asyncio
import numpy as np
import tkinter as tk
from tkinter import messagebox
from PIL import Image, ImageTk

from src.loadbalancer.load_balancer import LoadBalancer, WorkerState, LBMetrics, TaskRecord

APP_TITLE  = "DCP - Dynamic Compute Power"
WIN_WIDTH  = 1080
WIN_HEIGHT = 700

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

ASSETS_DIR  = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "assets")
RENDERS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "renders")


class LoginFrame(tk.Frame):

    def __init__(self, parent, on_connect):
        super().__init__(parent)
        self.on_connect_callback = on_connect
        self.bg_image   = None
        self.logo_image = None
        self.build()

    def load_logo(self, width=200, height=140):
        path = os.path.join(ASSETS_DIR, "logo.png")
        if not os.path.exists(path):
            return None
        img = Image.open(path).resize((width, height))
        self.logo_image = ImageTk.PhotoImage(img)
        return self.logo_image

    def build(self):
        bg = os.path.join(ASSETS_DIR, "master_background.png")
        if os.path.exists(bg):
            img = Image.open(bg).resize((WIN_WIDTH, WIN_HEIGHT))
            self.bg_image = ImageTk.PhotoImage(img)
            tk.Label(self, image=self.bg_image).place(x=0, y=0, relwidth=1, relheight=1)

        tk.Label(self, text=APP_TITLE, font=("Bahnschrift", 32, "bold")).pack(pady=(30, 10))
        logo = self.load_logo()
        if logo:
            tk.Label(self, image=logo, bd=0).pack(pady=10)

        mf = tk.Frame(self); mf.pack(pady=5)
        tk.Label(mf, text="Mode:", font=FONT_BODY).pack(side="left", padx=5)
        tk.Label(mf, text="Master", font=FONT_BODY, relief="sunken", width=12).pack(side="left")

        vcmd_ip   = (self.register(lambda P: len(P) <= 15), "%P")
        vcmd_port = (self.register(lambda P: len(P) <= 5),  "%P")

        self.ip = tk.StringVar()
        ipf = tk.Frame(self); ipf.pack(pady=5)
        tk.Label(ipf, text="IP:", font=FONT_BODY, width=6).pack(side="left")
        tk.Entry(ipf, textvariable=self.ip, width=18, font=FONT_BODY,
                 validate="key", validatecommand=vcmd_ip).pack(side="left")

        self.port = tk.StringVar(value="9000")
        pf = tk.Frame(self); pf.pack(pady=5)
        tk.Label(pf, text="PORT:", font=FONT_BODY, width=6).pack(side="left")
        tk.Entry(pf, textvariable=self.port, width=18, font=FONT_BODY,
                 validate="key", validatecommand=vcmd_port).pack(side="left")

        tk.Button(self, text="Connect", font=FONT_HEADER, bg=BTN_CONNECT,
                  fg="white", width=14, command=self.connect).pack(pady=20)

    def connect(self):
        ip = self.ip.get().strip()
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


class ControlPanel(tk.Frame):
    """
    Represnt the main control panel UI for the master, showing connected workers and popups for events.
    """
    COLUMNS = [("Identifier", 120), ("IP", 150), ("Status", 150), ("Remove", 80)]

    def __init__(self, parent, on_kick):
        super().__init__(parent)
        self.on_kick_callback = on_kick
        self.rows: dict = {}
        self.logo_image = None
        self.build()

    def load_logo(self, width=200, height=140):
        path = os.path.join(ASSETS_DIR, "logo.png")
        if not os.path.exists(path):
            return None
        img = Image.open(path).resize((width, height))
        self.logo_image = ImageTk.PhotoImage(img)
        return self.logo_image

    def build(self):
        tb = tk.Frame(self); tb.pack(fill="x", padx=10, pady=(10, 5))
        logo = self.load_logo()
        if logo:
            tk.Label(tb, image=logo, bd=0).pack(side="left", padx=5)
        tk.Label(tb, text="Control Panel", font=FONT_TITLE).pack(side="left", padx=15)

        body = tk.Frame(self); body.pack(fill="both", expand=True, padx=10, pady=5)

        # worker table (left)
        table_outer = tk.Frame(body, relief="groove", bd=1)
        table_outer.pack(side="left", fill="both", expand=True)

        hdr = tk.Frame(table_outer, bg=COL_HEADER); hdr.pack(fill="x")
        for text, width in self.COLUMNS:
            tk.Label(hdr, text=text, font=FONT_HEADER, bg=COL_HEADER,
                     width=width // 8, anchor="w", relief="groove", bd=1
                     ).pack(side="left", ipadx=4, ipady=3)

        self._canvas = tk.Canvas(table_outer, bg=COL_ROW_ODD, highlightthickness=0)
        sb = tk.Scrollbar(table_outer, orient="vertical", command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self._canvas.pack(side="left", fill="both", expand=True)

        self._rows_frame = tk.Frame(self._canvas, bg=COL_ROW_ODD)
        self._cwin = self._canvas.create_window((0, 0), window=self._rows_frame, anchor="nw")
        self._rows_frame.bind("<Configure>", lambda e: self._canvas.configure(scrollregion=self._canvas.bbox("all")))
        self._canvas.bind("<Configure>", lambda e: self._canvas.itemconfig(self._cwin, width=e.width))

        # Popups log (right) — scrollable Text widget with timed fade per entry
        popup = tk.Frame(body, relief="groove", bd=1, bg=COL_POPUP, width=220)
        popup.pack(side="right", fill="y", padx=(8, 0))
        popup.pack_propagate(False)
        tk.Label(popup, text="Popups", font=FONT_HEADER, bg=COL_POPUP).pack(pady=5)
        # scrollbar for the popup text
        popup_sb = tk.Scrollbar(popup, orient="vertical")
        self.popup_text = tk.Text(popup, font=FONT_SMALL, bg=COL_POPUP,
                                  state="disabled", wrap="word", relief="flat", bd=0,
                                  yscrollcommand=popup_sb.set)
        popup_sb.config(command=self.popup_text.yview)
        popup_sb.pack(side="right", fill="y")
        self.popup_text.pack(side="left", fill="both", expand=True, padx=4, pady=4)
        # track line numbers for scheduled fade-out {line_number: after_id}
        self._popup_line = 0       # current line count
        self._popup_fades: dict = {}

    def add_or_update_worker(self, worker_id, ip, status_text):
        if worker_id in self.rows:
            self.rows[worker_id]["ip_lbl"].config(text=ip)
            self.rows[worker_id]["status_lbl"].config(text=status_text)
            return

        bg = COL_ROW_ODD if len(self.rows) % 2 == 0 else COL_ROW_EVN
        rf = tk.Frame(self._rows_frame, bg=bg); rf.pack(fill="x")

        id_lbl     = tk.Label(rf, text=worker_id,   font=FONT_BODY, bg=bg, anchor="w", width=15)
        ip_lbl     = tk.Label(rf, text=ip,          font=FONT_BODY, bg=bg, anchor="w", width=19)
        status_lbl = tk.Label(rf, text=status_text, font=FONT_BODY, bg=bg, anchor="w", width=19)
        remove_btn = tk.Button(rf, text="Remove", font=FONT_SMALL, bg=BTN_REMOVE, fg="white",
                               command=lambda wid=worker_id: self.on_kick_callback(wid))

        id_lbl.pack(side="left", padx=4, pady=2)
        ip_lbl.pack(side="left", padx=4)
        status_lbl.pack(side="left", padx=4)
        remove_btn.pack(side="left", padx=4)

        self.rows[worker_id] = {"frame": rf, "ip_lbl": ip_lbl, "status_lbl": status_lbl}

    def remove_worker(self, worker_id):
        if worker_id not in self.rows:
            return
        self.rows[worker_id]["frame"].destroy()
        del self.rows[worker_id]

    def add_popup(self, message: str):
        self.popup_text.config(state="normal")
        self._popup_line += 1
        line_num = self._popup_line
        tag = f"line_{line_num}"
        self.popup_text.insert("end", f"• {message}\n", tag)
        self.popup_text.see("end")
        self.popup_text.config(state="disabled")
        # schedule fade: after 10 s grey out the text, after 11 s delete it
        after_id = self.popup_text.after(
            10_000, self._fade_popup_line, tag
        )
        self._popup_fades[tag] = after_id

    def _fade_popup_line(self, tag: str):
        """Grey out one popup line then delete it a second later."""
        try:
            self.popup_text.tag_config(tag, foreground="#bbbbbb")
            self.popup_text.after(1000, self._delete_popup_line, tag)
        except tk.TclError:
            pass  # widget already destroyed

    def _delete_popup_line(self, tag: str):
        """Delete the tagged line from the popup Text widget."""
        try:
            self.popup_text.config(state="normal")
            ranges = self.popup_text.tag_ranges(tag)
            if ranges:
                self.popup_text.delete(ranges[0], ranges[1])
            self.popup_text.config(state="disabled")
            self._popup_fades.pop(tag, None)
        except tk.TclError:
            pass  # widget already destroyed


class MasterUI:
    """
    Convert the LoadBalancer state and events the UI.
    """
    def __init__(self, lb: LoadBalancer):
        self.lb = lb
        self.root = tk.Tk()
        self.root.title(APP_TITLE)
        self.root.geometry(f"{WIN_WIDTH}x{WIN_HEIGHT}")
        self.root.resizable(True, True)
        self.control_panel = None
        self._render_counter = 0
        self.show_login()
        self.wire_lb_callbacks()

    def show_login(self):
        self.login = LoginFrame(self.root, on_connect=self.on_connect)
        self.login.pack(fill="both", expand=True)

    def show_control_panel(self):
        self.login.destroy()
        self.control_panel = ControlPanel(self.root, on_kick=self.on_kick)
        self.control_panel.pack(fill="both", expand=True)

    def on_connect(self, ip, port):
        print(f"[MasterUI] Connect clicked , ip={ip} port={port}")
        self.show_control_panel()

    def on_kick(self, worker_id):
            asyncio.get_event_loop().call_soon_threadsafe(
                asyncio.ensure_future,
                self.lb.kick_worker(worker_id, reason="manual_ui")
            )

    def wire_lb_callbacks(self):
        self.lb.on_worker_status_change(self.on_worker_change)
        self.lb.on_metrics_update(self.on_metrics)
        self.lb.on_task_done_register(self.on_task_done)

    # LB callback (asyncio thread -> root.after)

    def on_worker_change(self, ws: WorkerState):
        self.root.after(0, self._apply_worker_change, ws)

    def _apply_worker_change(self, ws: WorkerState):
        if self.control_panel is None:
            return
        # WorkerStatus uses OFFLINE (from heartbeat timeout) and also
        # unregister_worker fires this with status still at last known value —
        # we treat any status that is not READY/BUSY/OVERLOADED as removal.
        if ws.status.name == "OFFLINE":
            self.control_panel.remove_worker(ws.worker_id)
            self.control_panel.add_popup(f"{ws.worker_id} went offline.")
        else:
            ip_str     = f"{ws.address[0]}:{ws.address[1]}"
            status_txt = f"{ws.status.name}  {ws.cpu_usage:.0f}%"
            self.control_panel.add_or_update_worker(ws.worker_id, ip_str, status_txt)

    def on_task_done(self, task: TaskRecord):
        """Called by LB when a task finishes (DONE or permanently FAILED)."""
        self.root.after(0, self._apply_task_done, task)

    def _apply_task_done(self, task: TaskRecord):
        if self.control_panel is None:
            return

        short_id = task.task_id[:8]
        result   = task.result

        # permanently failed (exhausted retries)
        if task.status.name == "FAILED":
            self.control_panel.add_popup(f"FAILED  {short_id}  err: {task.error}")
            return

        if result is None:
            self.control_panel.add_popup(f"DONE  {short_id}  (no result)")
            return

        # SimulationTask results
        if isinstance(result, dict) and result.get("type") == "primes":
            self.control_panel.add_popup(
                f"Primes {short_id}: {result['count']} primes  range={result['range']}"
            )
        elif isinstance(result, dict) and result.get("type") == "matrix":
            self.control_panel.add_popup(
                f"Matrix {short_id}: sum={result['sum']:.4f}  size={result['matrix_size']}"
            )
        elif isinstance(result, dict) and result.get("type") == "monte_carlo":
            self.control_panel.add_popup(
                f"MonteCarlo {short_id}: pi~{result['pi_estimate']:.6f}  n={result['arrows_thrown']:,}"
            )
        # RenderTask result — save PNG to renders/ folder
        elif isinstance(result, dict) and "image_data" in result:
            self._save_render(short_id, result)
        else:
            self.control_panel.add_popup(f"DONE  {short_id}")

    def _save_render(self, short_id: str, result: dict):
        try:
            os.makedirs(RENDERS_DIR, exist_ok=True)
            self._render_counter += 1
            filename = f"render_{self._render_counter:04d}_{short_id}.png"
            filepath = os.path.join(RENDERS_DIR, filename)
            arr = np.frombuffer(result["image_data"], dtype=np.uint8).reshape(result["shape"])
            Image.fromarray(arr, "RGB").save(filepath)
            self.control_panel.add_popup(
                f"Render {short_id}: {result['shape'][1]}x{result['shape'][0]}px"
                f" saved -> renders/{filename}"
            )
        except Exception as exc:
            self.control_panel.add_popup(f"Render {short_id}: save failed — {exc}")

    def on_metrics(self, m: LBMetrics):
        self.root.after(0, self._apply_metrics, m)

    def _apply_metrics(self, m: LBMetrics):
        pass  # hook ready — add a status bar here if needed

    def run(self):
        self.root.mainloop()
        