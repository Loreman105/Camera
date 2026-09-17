from __future__ import annotations

import time
import tkinter as tk
from tkinter import ttk

import cv2
from PIL import Image, ImageTk

BG, SURFACE, CARD, EDGE = "#0b1018", "#121b28", "#172231", "#29384c"
TEXT, MUTED, BLUE, GREEN, AMBER, RED = "#f2f6fc", "#93a4b8", "#35a9ff", "#38d996", "#ffb648", "#ff6270"


class SecurityUI:
    """A dependency-free dark control-room dashboard."""
    def __init__(self, root, app):
        self.root, self.app, self.cards = root, app, []
        root.title("Sentinel · Local Security Camera")
        root.geometry("1440x900"); root.minsize(1050, 700); root.configure(bg=BG)
        self._style(); self._header(); self._cameras(); self._footer()
        root.bind("<Escape>", lambda _e: root.attributes("-fullscreen", False))
        root.bind("<F11>", lambda _e: self.fullscreen())
        root.protocol("WM_DELETE_WINDOW", app.close); self.refresh()

    def _style(self):
        style = ttk.Style(self.root); style.theme_use("clam")
        style.configure("Storage.Horizontal.TProgressbar", troughcolor="#263548", background=BLUE, bordercolor="#263548", lightcolor=BLUE, darkcolor=BLUE)

    def _header(self):
        header = tk.Frame(self.root, bg=BG); header.pack(fill="x", padx=32, pady=(22, 12))
        tk.Label(header, text="SENTINEL", font=("Segoe UI", 22, "bold"), bg=BG, fg=TEXT).pack(side="left")
        tk.Label(header, text="LOCAL SECURITY CAMERA", font=("Segoe UI", 10, "bold"), bg=BG, fg=BLUE).pack(side="left", padx=12, pady=(7, 0))
        self.clock = tk.Label(header, font=("Segoe UI", 10), bg=BG, fg=MUTED); self.clock.pack(side="right", pady=(7, 0))
        self.system = tk.Label(header, font=("Segoe UI", 9, "bold"), bg="#123829", fg=GREEN, padx=12, pady=5); self.system.pack(side="right", padx=(0, 15))

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
        bottom = tk.Frame(self.root, bg=BG); bottom.pack(fill="x", padx=32, pady=(15, 24))
        storage = tk.Frame(bottom, bg=SURFACE, highlightthickness=1, highlightbackground=EDGE); storage.pack(side="left", fill="x", expand=True)
        row = tk.Frame(storage, bg=SURFACE); row.pack(fill="x", padx=14, pady=(9, 3))
        tk.Label(row, text="STORAGE", font=("Segoe UI", 8, "bold"), bg=SURFACE, fg=MUTED).pack(side="left")
        self.storage_text = tk.Label(row, font=("Segoe UI", 9, "bold"), bg=SURFACE, fg=TEXT); self.storage_text.pack(side="right")
        self.storage_bar = ttk.Progressbar(storage, style="Storage.Horizontal.TProgressbar", maximum=100); self.storage_bar.pack(fill="x", padx=14, pady=(0, 11))
        actions = tk.Frame(bottom, bg=BG); actions.pack(side="right", padx=(14, 0))
        self.record_button = self._button(actions, "● START RECORDING", self.app.toggle_recording, GREEN, "#061b12")
        self.record_button.pack(side="left", padx=3)
        self.check_button = self._button(actions, "DOUBLE-CHECK RECORDINGS", self.app.check_active_recordings, AMBER, "#3a2a12")
        self.check_button.pack(side="left", padx=3)
        self.check_progress = ttk.Progressbar(actions, length=150, mode="determinate", maximum=1)
        self.check_progress.pack(side="left", padx=5)
        self.check_eta = tk.Label(actions, text="", font=("Segoe UI", 8), bg=BG, fg=MUTED, width=16, anchor="w")
        self.check_eta.pack(side="left", padx=(0, 3))
        for label, command, fg, bg in (("DETECTION", self.app.toggle_detection, BLUE, "#102a40"), ("SETTINGS", self.settings, TEXT, "#263548"), ("⛶", self.fullscreen, TEXT, "#263548"), ("EXIT", self.app.close, MUTED, "#263548")):
            self._button(actions, label, command, fg, bg).pack(side="left", padx=3)

    def sync_recording_button(self):
        if self.app.recording_enabled:
            self.record_button.config(text="■ STOP RECORDING", command=self.app.toggle_recording, fg=RED, bg="#2a1117")
        else:
            self.record_button.config(text="● START RECORDING", command=self.app.toggle_recording, fg=GREEN, bg="#061b12")

    @staticmethod
    def _button(parent, label, command, fg, bg):
        return tk.Button(parent, text=label, command=command, font=("Segoe UI", 9, "bold"), bd=0, padx=12, pady=9, bg=bg, fg=fg, activebackground="#344963", activeforeground=TEXT, cursor="hand2")

    def fullscreen(self): self.root.attributes("-fullscreen", not self.root.attributes("-fullscreen"))

    def settings(self):
        box = tk.Toplevel(self.root); box.title("Camera Settings"); box.configure(bg=SURFACE); box.resizable(False, False)
        tk.Label(box, text="CAMERA ASSIGNMENTS", font=("Segoe UI", 12, "bold"), bg=SURFACE, fg=TEXT).grid(row=0, column=0, columnspan=2, sticky="w", padx=22, pady=(20, 13))
        values = []
        for index, worker in enumerate(self.app.workers):
            tk.Label(box, text=worker.camera.label, font=("Segoe UI", 10), bg=SURFACE, fg=TEXT).grid(row=index + 1, column=0, sticky="w", padx=22, pady=7)
            value = tk.StringVar(value="" if worker.camera.device_index is None else str(worker.camera.device_index)); ttk.Entry(box, textvariable=value, width=12).grid(row=index + 1, column=1, padx=(15, 22), pady=7); values.append(value)
        def save():
            for worker, value in zip(self.app.workers, values): worker.camera.device_index = int(value.get()) if value.get().strip() else None
            self.app.save_settings(); box.destroy()
        self._button(box, "SAVE · RESTART TO RECONNECT", save, TEXT, "#263548").grid(row=3, column=0, columnspan=2, pady=(16, 20))

    def refresh(self):
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
        checking = any(worker.active_check_running for worker in self.app.workers)
        total_frames = sum(worker.active_check_total_frames for worker in self.app.workers)
        processed_frames = sum(worker.active_check_processed_frames for worker in self.app.workers)
        progress_value = min(processed_frames, total_frames) if checking else total_frames
        self.check_progress.configure(maximum=max(1, total_frames), value=progress_value)
        if checking and processed_frames > 0:
            started = min((worker.active_check_started_at for worker in self.app.workers if worker.active_check_running), default=time.monotonic())
            elapsed = max(0.001, time.monotonic() - started)
            remaining = max(0, int((total_frames - processed_frames) * elapsed / processed_frames))
            minutes, seconds = divmod(remaining, 60)
            self.check_eta.config(text=f"{processed_frames:,}/{total_frames:,}  ETA {minutes}:{seconds:02d}")
        elif checking:
            files_total = sum(worker.active_check_total_files for worker in self.app.workers)
            if files_total:
                self.check_eta.config(text=f"{files_total:,} clips · ETA calculating")
            else:
                self.check_eta.config(text="Preparing double-check…")
        else:
            self.check_eta.config(text="")
        if checking:
            self.check_button.config(text="STOP DOUBLE-CHECK", fg=RED, bg="#2a1117", state="normal")
        else:
            self.check_button.config(text="DOUBLE-CHECK RECORDINGS", fg=AMBER, bg="#3a2a12", state="normal")
        self.system.config(text=f"●  {online}/{len(self.cards)} CAMERAS ONLINE", fg=GREEN if online else RED, bg="#123829" if online else "#3d1720")
        free = self.app.storage.last_percent; self.storage_text.config(text="Storage unavailable" if free is None else f"{free:.1f}% FREE · {self.app.config.recordings_dir}"); self.storage_bar["value"] = max(0, min(100, free or 0))
        self.clock.config(text=time.strftime("%A, %B %d  ·  %I:%M:%S %p")); self.root.after(100, self.refresh)
