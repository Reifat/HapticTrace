# Copyright 2026 Nikolai Kolesnikov
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from typing import Protocol


class SessionTrackerProtocol(Protocol):
    def mark_sensor_sample(self, sensor_sample_time_s: float) -> None:
        ...

    def mark_video_frame(self, frame_wall_ts: float) -> None:
        ...