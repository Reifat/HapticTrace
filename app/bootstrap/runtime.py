# Copyright 2026 Nikolai Kolesnikov
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import os
import sys


def apply_macos_objc_env_guard() -> None:
    if sys.platform == "darwin" and os.environ.get("OBJC_PRINT_REPLACED_METHODS") != "NO":
        # This warning is controlled at process start on macOS, so restart once
        # with the variable set before any Cocoa/UI frameworks are loaded.
        os.environ["OBJC_PRINT_REPLACED_METHODS"] = "NO"
        os.execvpe(sys.executable, [sys.executable, *sys.argv], os.environ)