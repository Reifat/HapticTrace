# Copyright 2026 Nikolai Kolesnikov
# SPDX-License-Identifier: Apache-2.0

"""HapticTrace package."""

from __future__ import annotations

import sys
from pathlib import Path

_PACKAGE_DIR = Path(__file__).resolve().parent

# Avoid shadowing stdlib modules like `platform` when Python adds the package
# directory itself to sys.path during package-style execution.
cleaned_sys_path = []
for entry in sys.path:
    try:
        if entry and Path(entry).resolve() == _PACKAGE_DIR:
            continue
    except Exception:
        pass
    cleaned_sys_path.append(entry)
sys.path[:] = cleaned_sys_path