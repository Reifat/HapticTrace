# Copyright 2026 Nikolai Kolesnikov
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import logging
import tempfile
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional


def configure_runtime_logging(app_name: str = "HapticTrace") -> Optional[Path]:
    log_path: Optional[Path] = None
    handlers: list[logging.Handler] = []
    formatter = logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")

    candidate_dirs = [
        Path.home() / "Library" / "Logs" / app_name,
        Path(tempfile.gettempdir()) / app_name,
    ]
    for log_dir in candidate_dirs:
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            candidate_path = log_dir / f"{app_name}.log"
            file_handler = RotatingFileHandler(
                candidate_path,
                maxBytes=1_000_000,
                backupCount=3,
                encoding="utf-8",
            )
            file_handler.setFormatter(formatter)
            handlers.append(file_handler)
            log_path = candidate_path
            break
        except OSError:
            continue

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    handlers.append(stream_handler)

    logging.basicConfig(level=logging.INFO, handlers=handlers, force=True)
    logging.getLogger("matplotlib").setLevel(logging.WARNING)
    logging.getLogger("PIL").setLevel(logging.WARNING)
    return log_path
