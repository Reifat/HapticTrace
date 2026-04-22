# Copyright 2026 Nikolai Kolesnikov
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from typing import Any, Optional


def parse_optional_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    return float(value)


def parse_optional_int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    return int(value)