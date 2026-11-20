"""One command to start everything: the always-on worker + the web interface.

    py run.py              # continue where it left off
    py run.py --reset      # fresh database, replay the demo from the start
    py run.py --speed 0    # load the whole replay instantly (backlog mode)

Then open http://localhost:8000
(You can also run the parts separately: `py -m hybridwatch.worker` and
`py -m uvicorn hybridwatch.api:app --port 8000`. Closing the browser never stops the worker.)
"""
import argparse
import threading
import webbrowser

import uvicorn

from hybridwatch.config import load_config
from hybridwatch.worker import Worker, reset_db


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true")
    ap.add_argument("--speed", type=float, default=None, help="replay speed (simulated seconds per real second, 0 = instant)")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    cfg = load_config()
    if args.speed is not None:
        cfg["replay"]["speed"] = args.speed
    if args.reset:
        reset_db(cfg)
    stop = threading.Event()
    worker = Worker(cfg, stop)
    threading.Thread(target=worker.run, daemon=True).start()
    if not args.no_browser:
        threading.Timer(1.5, lambda: webbrowser.open(f"http://localhost:{args.port}")).start()
    try:
        uvicorn.run("hybridwatch.api:app", host="127.0.0.1", port=args.port, log_level="warning")
    finally:
        stop.set()


if __name__ == "__main__":
    main()
