"""HybridWatch as a desktop app: own window (no browser, no console).

    pythonw app.py            # continue where it left off
    pythonw app.py --reset    # fresh database, replay the demo from the start

Closing the window stops the worker and the server.
"""
import argparse
import os
import socket
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
os.chdir(HERE)

# pythonw has no console: send output to a log file so nothing crashes on print().
if sys.stdout is None or sys.stderr is None:
    _log = open(HERE / "data" / "app.log", "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stderr = _log

import uvicorn
import webview

from hybridwatch.config import load_config
from hybridwatch.worker import Worker, reset_db

LOADING_HTML = """
<html><body style="margin:0;height:100vh;display:flex;align-items:center;justify-content:center;
background:#0f1720;color:#cbd5e1;font-family:Segoe UI,sans-serif">
<div style="text-align:center"><div style="font-size:28px;font-weight:600;color:#fff">HybridWatch</div>
<div style="margin-top:10px;opacity:.7">Starting...</div></div></body></html>
"""


ALREADY_RUNNING_HTML = LOADING_HTML.replace(
    "Starting...",
    "HybridWatch is already running in another window.<br>"
    "Close it (including the start.bat console window), then try again.")


def is_hybridwatch(port):
    try:
        import httpx
        return "HybridWatch" in httpx.get(f"http://127.0.0.1:{port}/", timeout=2).text
    except Exception:
        return False


def port_free(port):
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


def find_port(start):
    for p in range(start, start + 20):
        if port_free(p):
            return p
    raise RuntimeError("No free port")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true")
    ap.add_argument("--speed", type=float, default=None)
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()

    window_kw = dict(width=1400, height=900, min_size=(900, 600))

    # Already running (e.g. started with start.bat)? Just show it, don't start a second copy.
    if not port_free(args.port) and is_hybridwatch(args.port):
        if args.reset:
            webview.create_window("HybridWatch", html=ALREADY_RUNNING_HTML, **window_kw)
        else:
            webview.create_window("HybridWatch", url=f"http://127.0.0.1:{args.port}", **window_kw)
        webview.start()
        return

    cfg = load_config()
    if args.speed is not None:
        cfg["replay"]["speed"] = args.speed
    if args.reset:
        reset_db(cfg)

    port = find_port(args.port)
    url = f"http://127.0.0.1:{port}"

    stop = threading.Event()
    worker = Worker(cfg, stop)
    threading.Thread(target=worker.run, daemon=True).start()

    server = uvicorn.Server(uvicorn.Config("hybridwatch.api:app", host="127.0.0.1", port=port,
                                           log_level="warning", log_config=None))
    threading.Thread(target=server.run, daemon=True).start()

    window = webview.create_window("HybridWatch", html=LOADING_HTML, **window_kw)

    def load_when_ready():
        for _ in range(300):
            if not port_free(port):
                window.load_url(url)
                return
            time.sleep(0.2)

    webview.start(load_when_ready)  # blocks until the window is closed
    stop.set()
    server.should_exit = True


if __name__ == "__main__":
    main()
