# Copyright 2026 Nikolai Kolesnikov
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

import numpy as np


@dataclass
class IncrementalSensorState:
    raw_time: deque = field(default_factory=lambda: deque(maxlen=60000))
    raw_x: deque = field(default_factory=lambda: deque(maxlen=60000))
    raw_y: deque = field(default_factory=lambda: deque(maxlen=60000))
    raw_z: deque = field(default_factory=lambda: deque(maxlen=60000))
    last_raw_time: Optional[float] = None
    processed_time: deque = field(default_factory=lambda: deque(maxlen=60000))
    processed_sig: deque = field(default_factory=lambda: deque(maxlen=60000))
    processed_upto_raw: int = 0
    scale_est: Optional[float] = None
    pca_vec: Optional[np.ndarray] = None
    eigvals: Optional[np.ndarray] = None


@dataclass
class CombinedState:
    time: deque = field(default_factory=lambda: deque(maxlen=60000))
    sig: deque = field(default_factory=lambda: deque(maxlen=60000))
    processed_upto_base: int = 0
    scale_est: Optional[float] = None


@dataclass
class InterpolationConfig:
    enabled: bool = False
    target_samples_on_window: int = 120
    window_ms: float = 120.0
    overlap_ratio: float = 0.6
    window_kind: str = "hann"
    method: str = "lagrange"
    poly_order: int = 4
    post_smoothing: str = "none"
    apply_to_export: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "enabled": bool(self.enabled),
            "target_samples_on_window": int(self.target_samples_on_window),
            "window_ms": float(self.window_ms),
            "overlap_ratio": float(self.overlap_ratio),
            "window_kind": str(self.window_kind),
            "method": str(self.method),
            "poly_order": int(self.poly_order),
            "post_smoothing": str(self.post_smoothing),
            "apply_to_export": bool(self.apply_to_export),
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "InterpolationConfig":
        window_ms = float(payload.get("window_ms", 120.0))
        legacy_target_fs = float(payload.get("target_fs", 1000.0))
        legacy_target_samples = max(8, int(round(legacy_target_fs * max(window_ms, 10.0) / 1000.0)))
        return cls(
            enabled=bool(payload.get("enabled", False)),
            target_samples_on_window=int(payload.get("target_samples_on_window", legacy_target_samples)),
            window_ms=window_ms,
            overlap_ratio=float(payload.get("overlap_ratio", 0.6)),
            window_kind=str(payload.get("window_kind", "hann")),
            method=str(payload.get("method", "lagrange")),
            poly_order=int(payload.get("poly_order", 4)),
            post_smoothing=str(payload.get("post_smoothing", "none")),
            apply_to_export=bool(payload.get("apply_to_export", False)),
        )