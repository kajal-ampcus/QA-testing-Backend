"""Start the virtual display for the live browser view, then run the worker.

The API container does not use this launcher. Chromium is drawn on DISPLAY
and streamed from this container; testers do not start a script on their PC.
"""

from __future__ import annotations

import errno
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

_DISPLAY = os.environ.get("DISPLAY", ":99")
_SCREEN = "1440x900x24"


def _require(name: str) -> str:
    found = shutil.which(name)
    if not found:
        raise SystemExit(f"{name} is not installed, so the live browser view cannot start.")
    return found


def _spawn(command: list[str]) -> subprocess.Popen:
    return subprocess.Popen(
        command,
        start_new_session=True,
    )


def _display_paths() -> tuple[Path, Path]:
    number = _DISPLAY.removeprefix(":")
    if not _DISPLAY.startswith(":") or not number.isdigit():
        raise SystemExit("The worker requires a local DISPLAY such as :99.")
    return Path(f"/tmp/.X11-unix/X{number}"), Path(f"/tmp/.X{number}-lock")


def _display_ready() -> bool:
    path, _ = _display_paths()
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(1)
        try:
            connection.connect(str(path))
            return True
        except OSError as error:
            if error.errno in (errno.ENOENT, errno.ECONNREFUSED):
                return False
            raise


def _clean_stale_display() -> None:
    if _display_ready():
        raise SystemExit(f"Display {_DISPLAY} is already running; refusing to replace it.")
    path, lock = _display_paths()
    # A restarted container can reuse the old Xvfb PID. Probe the socket rather
    # than trusting the PID in the lock (which may now refer to a zombie).
    path.unlink(missing_ok=True)
    lock.unlink(missing_ok=True)


def _port_ready(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


def _wait_ready(process: subprocess.Popen, ready, name: str) -> None:
    for _ in range(50):
        if process.poll() is not None:
            raise SystemExit(f"{name} exited with code {process.returncode}; see container logs.")
        if ready():
            return
        time.sleep(0.1)
    raise SystemExit(f"{name} did not become ready; see container logs.")


def main() -> None:
    os.environ["DISPLAY"] = _DISPLAY
    xvfb = _require("Xvfb")
    x11vnc = _require("x11vnc")
    websockify = _require("websockify")
    _clean_stale_display()
    display = _spawn([xvfb, _DISPLAY, "-screen", "0", _SCREEN, "-ac", "-nolisten", "tcp"])
    _wait_ready(display, _display_ready, "Xvfb")
    vnc = _spawn(
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
    _wait_ready(vnc, lambda: _port_ready(5900), "x11vnc")
    proxy = _spawn([websockify, "--web", "/usr/share/novnc", "6080", "127.0.0.1:5900"])
    _wait_ready(proxy, lambda: _port_ready(6080), "websockify")
    os.execvp("arq", ["arq", "apps.worker.arq_worker.WorkerSettings"])


if __name__ == "__main__":
    if "--check" in sys.argv:
        if not (_display_ready() and _port_ready(5900) and _port_ready(6080)):
            raise SystemExit("Live browser display is unavailable.")
        os.execvp("arq", ["arq", "--check", "apps.worker.arq_worker.WorkerSettings"])
    else:
        main()
