# Copyright 2026 Nikolai Kolesnikov
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import argparse
import logging
import signal
from pathlib import Path

from app.bootstrap.runtime import apply_macos_objc_env_guard
from app.core.runtime_logging import configure_runtime_logging

apply_macos_objc_env_guard()

import tkinter as tk

from app.gui.main_window import HapticTraceApp


def main() -> None:
    runtime_log_path = configure_runtime_logging()
    logging.getLogger(__name__).info("Runtime log path: %s", runtime_log_path)

    parser = argparse.ArgumentParser(description="HapticTrace: phyphox haptic monitor with iPhone capture")
    parser.add_argument("--url", default="http://192.168.1.67:8080", help="phyphox base URL")
    parser.add_argument("--autosave-dir", default=str(Path.home() / "HapticTrace_autosaves"))
    args = parser.parse_args()

    root = tk.Tk()
    app = HapticTraceApp(root, args.url, Path(args.autosave_dir), runtime_log_path=runtime_log_path)

    def handle_stop(_sig, _frame) -> None:
        try:
            app.on_close()
        finally:
            raise SystemExit(0)

    signal.signal(signal.SIGINT, handle_stop)
    root.mainloop()


if __name__ == "__main__":
    main()
