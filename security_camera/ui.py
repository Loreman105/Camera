from __future__ import annotations

import time
import tkinter as tk
from tkinter import filedialog, ttk

import cv2
import psutil
from PIL import Image, ImageTk

BG, SURFACE, CARD, EDGE = "#0b1018", "#121b28", "#172231", "#29384c"
TEXT, MUTED, BLUE, GREEN, AMBER, RED = "#f2f6fc", "#93a4b8", "#35a9ff", "#38d996", "#ffb648", "#ff6270"


class SecurityUI:
    """A dependency-free dark control-room dashboard."""
    def __init__(self, root, app):
        self.root, self.app, self.cards = root, app, []
        self.process_history = {name: [] for name in ("CPU", "RAM", "GPU")}
        self.process_started_at = None
        root.title("Sentinel · Local Security Camera")
        screen_height = root.winfo_screenheight()
        root.geometry(f"1440x{min(900, max(620, screen_height - 120))}"); root.minsize(1050, 620); root.configure(bg=BG)
        self._style(); self._header()
        if app.mode == "Process":
            self._process_panel()
        else:
            self._cameras(); self._footer()
        root.bind("<Escape>", lambda _e: root.attributes("-fullscreen", False))
        root.bind("<F11>", lambda _e: self.fullscreen())
        root.protocol("WM_DELETE_WINDOW", app.close); self.refresh()

    def _style(self):
        style = ttk.Style(self.root); style.theme_use("clam")
        style.configure("Storage.Horizontal.TProgressbar", troughcolor="#263548", background=BLUE, bordercolor="#263548", lightcolor=BLUE, darkcolor=BLUE)

    def _header(self):
        header = tk.Frame(self.root, bg=BG); header.pack(fill="x", padx=32, pady=(14, 8))
        tk.Label(header, text="SENTINEL", font=("Segoe UI", 22, "bold"), bg=BG, fg=TEXT).pack(side="left")
        tk.Label(header, text="LOCAL SECURITY CAMERA", font=("Segoe UI", 10, "bold"), bg=BG, fg=BLUE).pack(side="left", padx=12, pady=(7, 0))
        self.clock = tk.Label(header, font=("Segoe UI", 10), bg=BG, fg=MUTED); self.clock.pack(side="right", pady=(7, 0))
        self.processor_status = None
        if self.app.mode == "Record":
            self.processor_status = tk.Label(header, text="PROCESSORS 0 CONNECTED", font=("Segoe UI", 9, "bold"), bg="#3d1720", fg=RED, padx=12, pady=5)
            self.processor_status.pack(side="right", padx=(0, 10))
        self.system = tk.Label(header, text=self.app.mode.upper(), font=("Segoe UI", 9, "bold"), bg="#123829", fg=GREEN, padx=12, pady=5); self.system.pack(side="right", padx=(0, 15))

    def _cameras(self):
        grid = tk.Frame(self.root, bg=BG); grid.pack(fill="both", expand=True, padx=28)
        grid.columnconfigure((0, 1), weight=1, uniform="camera"); grid.rowconfigure(0, weight=1)
        for col, worker in enumerate(self.app.workers):
            card = tk.Frame(grid, bg=CARD, highlightthickness=1, highlightbackground=EDGE); card.grid(row=0, column=col, sticky="nsew", padx=7)
            top = tk.Frame(card, bg=CARD); top.pack(fill="x", padx=18, pady=(16, 10))
            dot = tk.Label(top, text="●", font=("Segoe UI", 16), bg=CARD, fg=MUTED); dot.pack(side="left")
            tk.Label(top, text=worker.camera.label.upper(), font=("Segoe UI", 13, "bold"), bg=CARD, fg=TEXT).pack(side="left", padx=7)
            state = tk.Label(top, font=("Segoe UI", 8, "bold"), bg="#263548", fg=MUTED, padx=8, pady=3); state.pack(side="right")
            preview_box = tk.Frame(card, bg="#05080d", highlightthickness=1, highlightbackground="#314157"); preview_box.pack(fill="both", expand=True, padx=18)
            image = tk.Label(preview_box, text="WAITING FOR CAMERA", font=("Segoe UI", 11, "bold"), bg="#05080d", fg="#56677d"); image.pack(fill="both", expand=True)
            metrics = tk.Frame(card, bg=CARD); metrics.pack(fill="x", padx=18, pady=14)
            metric_labels = []
            for title in ("RECORDING", "PERSON", "FPS"):
                tile = tk.Frame(metrics, bg=SURFACE); tile.pack(side="left", fill="both", expand=True, padx=(0, 6) if title != "FPS" else 0)
                tk.Label(tile, text=title, font=("Segoe UI", 7, "bold"), bg=SURFACE, fg=MUTED).pack(anchor="w", padx=10, pady=(7, 0))
                label = tk.Label(tile, text="—", font=("Segoe UI", 10, "bold"), bg=SURFACE, fg=TEXT); label.pack(anchor="w", padx=10, pady=(0, 7)); metric_labels.append(label)
            detail = tk.Label(card, justify="left", anchor="w", font=("Segoe UI", 9), bg=CARD, fg=MUTED, wraplength=610); detail.pack(fill="x", padx=18, pady=(0, 15))
            self.cards.append((worker, dot, state, image, *metric_labels, detail))

    def _footer(self):
        bottom = tk.Frame(self.root, bg=BG); bottom.pack(fill="x", padx=32, pady=(8, 12))
        info = tk.Frame(bottom, bg=BG)
        if self.app.mode != "Record":
            info.pack(fill="x", pady=(0, 5))
        storage = tk.Frame(info, bg=SURFACE, highlightthickness=1, highlightbackground=EDGE)
        if self.app.mode != "Record":
            storage.pack(fill="x", expand=True)
        row = tk.Frame(storage, bg=SURFACE); row.pack(fill="x", padx=14, pady=(5, 2))
        tk.Label(row, text="STORAGE", font=("Segoe UI", 8, "bold"), bg=SURFACE, fg=MUTED).pack(side="left")
        self.storage_text = tk.Label(row, font=("Segoe UI", 9, "bold"), bg=SURFACE, fg=TEXT); self.storage_text.pack(side="right")
        self.storage_bar = ttk.Progressbar(storage, style="Storage.Horizontal.TProgressbar", maximum=100); self.storage_bar.pack(fill="x", padx=14, pady=(0, 6))
        check_status = tk.Frame(info, bg=SURFACE, highlightthickness=1, highlightbackground=EDGE)
        if self.app.mode != "Record":
            check_status.pack(fill="x", expand=True, pady=(5, 0))
        self.resource_bars = {}
        for name in ("CPU", "RAM", "DISK", "GPU"):
            cell = tk.Frame(check_status, bg=SURFACE); cell.pack(side="left", fill="x", expand=True, padx=(10, 4))
            label = tk.Label(cell, text=f"{name} 0%", font=("Segoe UI", 8, "bold"), bg=SURFACE, fg=MUTED, anchor="w")
            label.pack(fill="x")
            bar = ttk.Progressbar(cell, mode="determinate", maximum=100)
            bar.pack(fill="x", pady=(2, 6))
            self.resource_bars[name] = (label, bar)
        self.check_eta = tk.Label(check_status, text="Idle", font=("Segoe UI", 9, "bold"), bg=SURFACE, fg=TEXT, anchor="w")
        self.check_eta.pack(side="left", padx=(8, 14), pady=6)
        actions = tk.Frame(bottom, bg=BG); actions.pack(fill="x")
        self.record_button = self._button(actions, "● START RECORDING", self.app.toggle_recording, GREEN, "#061b12")
        self.record_button.pack(side="right", padx=3)
        if self.app.mode != "Record":
            self.check_button = self._button(actions, "DOUBLE-CHECK RECORDINGS", self.app.check_active_recordings, AMBER, "#3a2a12")
            self.check_button.pack(side="right", padx=3)
        for label, command, fg, bg in (("DETECTION", self.app.toggle_detection, BLUE, "#102a40"), ("SETTINGS", self.settings, TEXT, "#263548"), ("⛶", self.fullscreen, TEXT, "#263548"), ("EXIT", self.app.close, MUTED, "#263548")):
            if self.app.mode == "Record" and label == "DETECTION":
                continue
            self._button(actions, label, command, fg, bg).pack(side="right", padx=3)

    def _process_panel(self):
        panel = tk.Frame(self.root, bg=BG); panel.pack(fill="both", expand=True, padx=32, pady=24)
        tk.Label(panel, text="REMOTE RECORDING PROCESSOR", font=("Segoe UI", 18, "bold"), bg=BG, fg=TEXT).pack(anchor="w")
        tk.Label(panel, text=f"Recorder: {self.app.config.processor_server_url}  ·  Devices: {self.app.config.processing_devices}", font=("Segoe UI", 10), bg=BG, fg=MUTED).pack(anchor="w", pady=(6, 16))
        self.process_status = tk.Label(panel, text="Ready", font=("Segoe UI", 11, "bold"), bg=SURFACE, fg=TEXT, anchor="w", padx=16, pady=14)
        self.process_status.pack(fill="x", pady=(0, 14))
        self.process_elapsed = tk.Label(panel, text="Elapsed 00:00:00", font=("Segoe UI", 9, "bold"), bg=BG, fg=MUTED, anchor="w")
        self.process_elapsed.pack(fill="x", pady=(0, 10))
        metrics = tk.Frame(panel, bg=BG); metrics.pack(fill="x", pady=(0, 14))
        self.process_metric_labels = {}
        for name in ("CPU", "RAM", "GPU", "DISK"):
            card = tk.Frame(metrics, bg=SURFACE, highlightthickness=1, highlightbackground=EDGE)
            card.pack(side="left", fill="both", expand=True, padx=(0, 8) if name != "DISK" else 0)
            tk.Label(card, text=name, font=("Segoe UI", 8, "bold"), bg=SURFACE, fg=MUTED).pack(anchor="w", padx=12, pady=(8, 0))
            value = tk.Label(card, text="--", font=("Segoe UI", 16, "bold"), bg=SURFACE, fg=TEXT)
            value.pack(anchor="w", padx=12, pady=(0, 8)); self.process_metric_labels[name] = value
        graphs = tk.Frame(panel, bg=BG); graphs.pack(fill="both", expand=True)
        self.process_graphs = {}
        for column, name in enumerate(("CPU", "RAM", "GPU")):
            graphs.columnconfigure(column, weight=1)
            box = tk.Frame(graphs, bg=SURFACE, highlightthickness=1, highlightbackground=EDGE)
            box.grid(row=0, column=column, sticky="nsew", padx=(0, 8) if column < 2 else 0)
            tk.Label(box, text=f"{name} HISTORY", font=("Segoe UI", 8, "bold"), bg=SURFACE, fg=MUTED).pack(anchor="w", padx=10, pady=(8, 2))
            graph = tk.Canvas(box, height=150, bg="#0a111b", highlightthickness=0)
            graph.pack(fill="both", expand=True, padx=8, pady=(0, 8)); self.process_graphs[name] = graph
        self.process_button = self._button(panel, "DOUBLE-CHECK RECORDINGS", self.app.check_active_recordings, AMBER, "#3a2a12")
        self.process_button.pack(anchor="w")
        tk.Label(panel, text="Use Settings to change the recorder URL or processing GPUs.", font=("Segoe UI", 9), bg=BG, fg=MUTED).pack(anchor="w", pady=(12, 0))

    def sync_recording_button(self):
        if self.app.recording_enabled:
            self.record_button.config(text="■ STOP RECORDING", command=self.app.toggle_recording, fg=RED, bg="#2a1117")
        else:
            self.record_button.config(text="● START RECORDING", command=self.app.toggle_recording, fg=GREEN, bg="#061b12")

    @staticmethod
    def _button(parent, label, command, fg, bg):
        return tk.Button(parent, text=label, command=command, font=("Segoe UI", 9, "bold"), bd=0, padx=12, pady=6, bg=bg, fg=fg, activebackground="#344963", activeforeground=TEXT, cursor="hand2")

    @staticmethod
    def _duration_text(seconds):
        hours, remainder = divmod(max(0, int(seconds)), 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    def fullscreen(self): self.root.attributes("-fullscreen", not self.root.attributes("-fullscreen"))

    def settings(self):
        box = tk.Toplevel(self.root); box.title("Camera Settings"); box.configure(bg=SURFACE); box.resizable(False, False)
        tk.Label(box, text="CAMERA ASSIGNMENTS", font=("Segoe UI", 12, "bold"), bg=SURFACE, fg=TEXT).grid(row=0, column=0, columnspan=2, sticky="w", padx=22, pady=(20, 13))
        values = []
        for index, worker in enumerate(self.app.workers):
            tk.Label(box, text=worker.camera.label, font=("Segoe UI", 10), bg=SURFACE, fg=TEXT).grid(row=index + 1, column=0, sticky="w", padx=22, pady=7)
            value = tk.StringVar(value="" if worker.camera.device_index is None else str(worker.camera.device_index)); ttk.Entry(box, textvariable=value, width=12).grid(row=index + 1, column=1, padx=(15, 22), pady=7); values.append(value)
        tk.Label(box, text="Recording folder", font=("Segoe UI", 10), bg=SURFACE, fg=TEXT).grid(row=3, column=0, sticky="w", padx=22, pady=7)
        recordings_dir = tk.StringVar(value=self.app.config.recordings_dir)
        ttk.Entry(box, textvariable=recordings_dir, width=34).grid(row=3, column=1, padx=(15, 4), pady=7)
        self._button(box, "BROWSE", lambda: self._browse_recordings_dir(recordings_dir), TEXT, "#263548").grid(row=3, column=2, padx=(0, 22), pady=7)
        next_row = 4
        tk.Label(box, text="Recorder URL", font=("Segoe UI", 10), bg=SURFACE, fg=TEXT).grid(row=next_row, column=0, sticky="w", padx=22, pady=7)
        processor_url = tk.StringVar(value=self.app.config.processor_server_url)
        ttk.Entry(box, textvariable=processor_url, width=34).grid(row=next_row, column=1, columnspan=2, padx=(15, 22), pady=7)
        next_row += 1
        tk.Label(box, text="Processing GPUs", font=("Segoe UI", 10), bg=SURFACE, fg=TEXT).grid(row=next_row, column=0, sticky="w", padx=22, pady=7)
        processing_devices = tk.StringVar(value=self.app.config.processing_devices)
        ttk.Entry(box, textvariable=processing_devices, width=34).grid(row=next_row, column=1, columnspan=2, padx=(15, 22), pady=7)
        next_row += 1
        tk.Label(box, text="Use auto, all, or a list such as 0,1", font=("Segoe UI", 8), bg=SURFACE, fg=MUTED).grid(row=next_row, column=1, columnspan=2, sticky="w", padx=(15, 22))
        next_row += 1
        command = f"python process_recordings.py --server {processor_url.get() or '<recorder-url>'}"
        command_value = tk.StringVar(value=command)
        tk.Label(box, text="Processor command", font=("Segoe UI", 10), bg=SURFACE, fg=TEXT).grid(row=next_row, column=0, sticky="w", padx=22, pady=7)
        ttk.Entry(box, textvariable=command_value, width=34).grid(row=next_row, column=1, padx=(15, 4), pady=7)
        self._button(box, "COPY", lambda: self._copy_text(command_value.get()), TEXT, "#263548").grid(row=next_row, column=2, padx=(0, 22), pady=7)
        processor_url.trace_add("write", lambda *_: command_value.set(
            f"python process_recordings.py --server {processor_url.get() or '<recorder-url>'}"
        ))
        next_row += 1
        def save():
            for worker, value in zip(self.app.workers, values): worker.camera.device_index = int(value.get()) if value.get().strip() else None
            if recordings_dir.get().strip(): self.app.config.recordings_dir = recordings_dir.get().strip()
            self.app.config.processor_server_url = processor_url.get().strip()
            self.app.config.processing_devices = processing_devices.get().strip() or "auto"
            self.app.save_settings(); box.destroy()
        self._button(box, "SAVE · RESTART TO APPLY", save, TEXT, "#263548").grid(row=next_row, column=0, columnspan=3, pady=(16, 20))

    def _copy_text(self, text):
        self.root.clipboard_clear(); self.root.clipboard_append(text); self.root.update()

    @staticmethod
    def _browse_recordings_dir(value):
        selected = filedialog.askdirectory(title="Choose where to save recordings", mustexist=False)
        if selected:
            value.set(selected)

    def refresh(self):
        if self.app.mode == "Process":
            self.process_status.config(text=getattr(self.app, "processing_status", "Ready"))
            self._refresh_process_metrics()
            self.root.after(500, self.refresh)
            return
        online = 0
        for worker, dot, state, image, recording, person, fps, detail in self.cards:
            status = worker.status
            if status.connected:
                online += 1; dot.config(fg=GREEN); state.config(text="CONNECTED", bg="#123829", fg=GREEN)
            else: dot.config(fg=RED); state.config(text="OFFLINE", bg="#3d1720", fg=RED)
            recording.config(text=f"{status.recording_mode} {'●' if status.recording_active else '○'}", fg=GREEN if status.recording_active else TEXT)
            person.config(text="DETECTED" if status.person else "CLEAR", fg=AMBER if status.person else TEXT); fps.config(text=f"{status.fps:.1f}")
            overlay = worker.ptz_overlay(); detail.config(text=f"Detection {status.person_frames}/{worker.config.person_frames_required}  ·  {status.error}" + (f"\n{overlay}" if overlay else ""))
            with worker._lock: frame = worker.latest_frame
            if frame is not None:
                preview = cv2.resize(frame, (640, 360))
                if overlay:
                    cv2.rectangle(preview, (0, 0), (640, 31), (0, 0, 0), -1); color = (0, 165, 255) if "SENT" in overlay else (255, 169, 53)
                    cv2.putText(preview, overlay, (9, 21), cv2.FONT_HERSHEY_SIMPLEX, .47, color, 1, cv2.LINE_AA)
                photo = ImageTk.PhotoImage(Image.fromarray(cv2.cvtColor(preview, cv2.COLOR_BGR2RGB))); image.config(image=photo, text=""); image.image = photo
        if self.app.mode == "Record":
            if self.processor_status is not None:
                count, devices = self.app.stream_server.processor_summary()
                device_text = f" · {', '.join(devices)}" if devices else ""
                self.processor_status.config(
                    text=f"PROCESSORS {count} CONNECTED{device_text}",
                    fg=GREEN if count else RED,
                    bg="#123829" if count else "#3d1720",
                )
            self.system.config(text=f"●  {online}/{len(self.cards)} CAMERAS ONLINE", fg=GREEN if online else RED, bg="#123829" if online else "#3d1720")
            self.clock.config(text=time.strftime("%A, %B %d  ·  %I:%M:%S %p")); self.root.after(100, self.refresh)
            return
        checking = any(worker.active_check_running for worker in self.app.workers)
        total_frames = sum(worker.active_check_total_frames for worker in self.app.workers)
        processed_frames = sum(worker.active_check_processed_frames for worker in self.app.workers)
        cpu_percent = psutil.cpu_percent(interval=None)
        ram_percent = psutil.virtual_memory().percent
        try:
            disk_percent = psutil.disk_usage(self.app.config.recordings_dir).percent
        except OSError:
            disk_percent = 0.0
        gpu_usage = max((worker.detector.gpu_utilization_percent() for worker in self.app.workers), default=0.0)
        for name, value in (("CPU", cpu_percent), ("RAM", ram_percent), ("DISK", disk_percent), ("GPU", gpu_usage)):
            label, bar = self.resource_bars[name]
            label.config(text=f"{name} {value:.1f}%")
            bar.configure(value=value)
        if checking and processed_frames > 0:
            started = min((worker.active_check_processing_started_at for worker in self.app.workers
                           if worker.active_check_running and worker.active_check_processing_started_at),
                          default=time.monotonic())
            elapsed = max(0.001, time.monotonic() - started)
            remaining = max(0, int((total_frames - processed_frames) * elapsed / processed_frames))
            minutes, seconds = divmod(remaining, 60)
            batch_size = max((worker.detector.current_verification_batch_size for worker in self.app.workers), default=0)
            self.check_eta.config(
                text=f"{processed_frames:,}/{total_frames:,}  ETA {minutes}:{seconds:02d}  BATCH {batch_size}",
                fg=GREEN,
            )
        elif checking:
            files_total = sum(worker.active_check_total_files for worker in self.app.workers)
            batch_size = max((worker.detector.current_verification_batch_size for worker in self.app.workers), default=0)
            started = min((worker.active_check_started_at for worker in self.app.workers if worker.active_check_running), default=time.monotonic())
            waiting = self._duration_text(time.monotonic() - started)
            if files_total:
                self.check_eta.config(text=f"{files_total:,} clips · WAITING {waiting} · BATCH {batch_size}", fg=AMBER)
            else:
                self.check_eta.config(text=f"Preparing double-check…  WAITING {waiting} · BATCH {batch_size}", fg=AMBER)
        else:
            self.check_eta.config(text="Idle", fg=TEXT)
        if checking:
            self.check_button.config(text="STOP DOUBLE-CHECK", fg=RED, bg="#2a1117", state="normal")
        else:
            self.check_button.config(text="DOUBLE-CHECK RECORDINGS", fg=AMBER, bg="#3a2a12", state="normal")
        self.system.config(text=f"●  {online}/{len(self.cards)} CAMERAS ONLINE", fg=GREEN if online else RED, bg="#123829" if online else "#3d1720")
        free = self.app.storage.last_percent; self.storage_text.config(text="Storage unavailable" if free is None else f"{free:.1f}% FREE · {self.app.config.recordings_dir}"); self.storage_bar["value"] = max(0, min(100, free or 0))
        self.clock.config(text=time.strftime("%A, %B %d  ·  %I:%M:%S %p")); self.root.after(100, self.refresh)

    def _refresh_process_metrics(self):
        values = {"CPU": psutil.cpu_percent(interval=None), "RAM": psutil.virtual_memory().percent}
        try:
            values["DISK"] = psutil.disk_usage(self.app.config.recordings_dir).percent
        except OSError:
            values["DISK"] = 0.0
        values["GPU"] = self._gpu_usage()
        for name, value in values.items():
            self.process_metric_labels[name].config(text="N/A" if value is None else f"{value:.1f}%")
            if name in self.process_graphs:
                history = self.process_history[name]
                history.append(value or 0.0); del history[:-60]
                self._draw_graph(self.process_graphs[name], history, GREEN if name == "GPU" else BLUE)
        started = getattr(self.app, "processing_started_at", None)
        elapsed = time.monotonic() - started if started else 0
        self.process_elapsed.config(text=f"Elapsed {self._duration_text(elapsed)}")

    @staticmethod
    def _gpu_usage():
        try:
            import pynvml
            pynvml.nvmlInit()
            values = [pynvml.nvmlDeviceGetUtilizationRates(pynvml.nvmlDeviceGetHandleByIndex(index)).gpu
                      for index in range(pynvml.nvmlDeviceGetCount())]
            pynvml.nvmlShutdown()
            return max(values, default=0.0)
        except Exception:
            return None

    @staticmethod
    def _draw_graph(canvas, values, color):
        canvas.delete("all")
        width = max(1, canvas.winfo_width()); height = max(1, canvas.winfo_height())
        for fraction in (0.25, 0.5, 0.75):
            y = height * (1 - fraction); canvas.create_line(0, y, width, y, fill="#1d2b3b")
        if len(values) < 2:
            return
        points = []
        for index, value in enumerate(values):
            x = index * width / max(1, len(values) - 1)
            points.extend((x, height - (min(100, max(0, value)) / 100 * height)))
        canvas.create_line(*points, fill=color, width=2, smooth=True)
