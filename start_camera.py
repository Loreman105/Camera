"""Double-click this file, or run:  python start_camera.py"""
from __future__ import annotations

import sys


def main() -> None:
    try:
        from security_camera.main import Application
    except ModuleNotFoundError as exc:
        if exc.name in {"cv2", "PIL"}:
            print("Missing dependency. Run: pip install -r requirements.txt", file=sys.stderr)
            input("Press Enter to close...")
            raise SystemExit(1)
        raise
    Application().run()


if __name__ == "__main__":
    main()
