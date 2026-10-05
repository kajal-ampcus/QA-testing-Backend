"""Start the virtual display for the live browser view, then run the worker.

The API container does not use this launcher. Chromium is drawn on DISPLAY
and streamed from this container; testers do not start a script on their PC.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

_DISPLAY = os.environ.get("DISPLAY", ":99")
_SCREEN = "1440x900x24"


def _require(name: str) -> str:
    found = shutil.which(name)
    if not found:
        raise SystemExit(f"{name} is not installed, so the live browser view cannot start.")
    return found


def _spawn(command: list[str]) -> None:
    subprocess.Popen(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def _wait_for_display() -> None:
    socket_name = _DISPLAY.removeprefix(":")
    socket_path = Path(f"/tmp/.X11-unix/X{socket_name}")
    for _ in range(50):
        if socket_path.exists():
            return
        time.sleep(0.1)
    raise SystemExit("The virtual display did not start.")


def main() -> None:
    os.environ["DISPLAY"] = _DISPLAY
    xvfb = _require("Xvfb")
    x11vnc = _require("x11vnc")
    websockify = _require("websockify")
    _spawn([xvfb, _DISPLAY, "-screen", "0", _SCREEN, "-ac", "-nolisten", "tcp"])
    _wait_for_display()
    _spawn(
        [
            x11vnc,
            "-display",
            _DISPLAY,
            "-localhost",
            "-rfbport",
            "5900",
            "-nopw",
            "-forever",
            "-shared",
            "-noxdamage",
            "-quiet",
        ]
    )
    _spawn([websockify, "--web", "/usr/share/novnc", "6080", "127.0.0.1:5900"])
    os.execvp("arq", ["arq", "apps.worker.arq_worker.WorkerSettings"])


if __name__ == "__main__":
    main()
