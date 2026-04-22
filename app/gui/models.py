# Copyright 2026 Nikolai Kolesnikov
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class PlaybackState:
    cursor_time_s: Optional[float] = None
    duration_s: float = 0.0
    visible_start_s: float = 0.0
    visible_end_s: float = 0.0
    is_playing: bool = False
    selected_speed: float = 1.0
    auto_offset_s: float = 0.0
    manual_offset_s: float = 0.0
    last_tick_mono: Optional[float] = None


@dataclass
class AppSession:
    session_id: str
    title: str
    snapshot_dir: Path
    has_data: bool = False