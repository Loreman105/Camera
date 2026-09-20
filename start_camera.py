"""Double-click this file, or run:  python start_camera.py"""
from __future__ import annotations

import sys
import argparse
import tkinter as tk
from tkinter import messagebox, ttk
from pathlib import Path


MODES = ("One computer", "Record", "Process")


def choose_mode(default_server: str = "") -> tuple[str, str | None] | None:
    selection = {}
    root = tk.Tk()
    root.title("Sentinel startup")
    root.resizable(False, False)
    root.configure(padx=24, pady=20)

    ttk.Label(root, text="How should Sentinel run?", font=("Segoe UI", 12, "bold")).grid(
        row=0, column=0, columnspan=2, sticky="w", pady=(0, 12)
    )
    mode = tk.StringVar(value=MODES[0])
    ttk.Label(root, text="Mode").grid(row=1, column=0, sticky="w", pady=6)
    dropdown = ttk.Combobox(root, textvariable=mode, values=MODES, state="readonly", width=24)
    dropdown.grid(row=1, column=1, sticky="ew", pady=6)
    server = tk.StringVar(value=default_server)
    server_label = ttk.Label(root, text="Recorder URL")
    server_entry = ttk.Entry(root, textvariable=server, width=27)

    def update_fields(*_):
        visible = mode.get() == "Process"
        if visible:
            server_label.grid(row=2, column=0, sticky="w", pady=6)
            server_entry.grid(row=2, column=1, sticky="ew", pady=6)
        else:
            server_label.grid_remove()
            server_entry.grid_remove()

    def launch():
        if mode.get() == "Process" and not server.get().strip():
            messagebox.showerror("Recorder URL required", "Enter the camera computer URL.", parent=root)
            return
        selection["value"] = (mode.get(), server.get().strip() if mode.get() == "Process" else None)
        root.destroy()

    mode.trace_add("write", update_fields)
    ttk.Button(root, text="Launch", command=launch).grid(row=3, column=0, columnspan=2, sticky="e", pady=(16, 0))
    root.protocol("WM_DELETE_WINDOW", root.destroy)
    update_fields()
    root.mainloop()
    return selection.get("value")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--recording-only", action="store_true", help="Skip the mode selector and capture without local detection")
    args = parser.parse_args()
    try:
        from security_camera.main import Application
    except ModuleNotFoundError as exc:
        if exc.name in {"cv2", "PIL"}:
            print("Missing dependency. Run: pip install -r requirements.txt", file=sys.stderr)
            input("Press Enter to close...")
            raise SystemExit(1)
        raise
    if args.recording_only:
        Application(recording_only=True).run()
        return
    saved_server = ""
    try:
        from security_camera.config import load
        saved_server = load(Path("settings.json")).processor_server_url
    except (OSError, ValueError, TypeError):
        pass
    choice = choose_mode(saved_server)
    if choice is None:
        return
    mode, server = choice
    if mode == "Process":
        Application(mode="Process", processor_url=server).run()
        return
    Application(mode=mode).run()


if __name__ == "__main__":
    main()
