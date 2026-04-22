# Copyright 2026 Nikolai Kolesnikov
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from typing import Tuple

import numpy as np
from scipy import signal as scipy_signal


def robust_scale_value(x: np.ndarray, q: float = 95.0) -> float:
    if x.size == 0:
        return 1.0
    value = float(np.percentile(np.abs(x), q))
    if not np.isfinite(value) or value < 1e-9:
        value = float(np.max(np.abs(x)))
    if not np.isfinite(value) or value < 1e-9:
        value = 1.0
    return value


def pca_first_component(xyz: np.ndarray) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    centered = xyz - np.mean(xyz, axis=0, keepdims=True)
    if centered.shape[0] < 3:
        axis = int(np.argmax(np.var(centered, axis=0))) if centered.shape[1] else 0
        vec = np.zeros(centered.shape[1] or 3)
        if vec.size:
            vec[min(axis, vec.size - 1)] = 1.0
        out = centered[:, axis] if centered.shape[1] else np.zeros(centered.shape[0])
        return out, vec, np.zeros(vec.size)
    cov = np.cov(centered, rowvar=False)
    eigvals, eigvecs = np.linalg.eigh(cov)
    idx = np.argsort(eigvals)[::-1]
    eigvals = eigvals[idx]
    eigvecs = eigvecs[:, idx]
    vec = eigvecs[:, 0]
    pc1 = centered @ vec
    return pc1, vec, eigvals


def causal_filter(sig: np.ndarray, fs: float) -> np.ndarray:
    if sig.size == 0:
        return sig
    x = np.asarray(sig, dtype=float)
    if fs <= 2.0:
        return x
    hp = 40.0
    nyq = 0.5 * fs
    if hp >= nyq:
        return x
    sos = scipy_signal.butter(6, hp, btype="highpass", fs=fs, output="sos")
    x = scipy_signal.sosfilt(sos, x)
    tau = 0.005
    alpha = 1.0 - np.exp(-1.0 / max(fs * tau, 1.0))
    y = np.empty_like(x)
    y[0] = x[0]
    for i in range(1, x.size):
        y[i] = y[i - 1] + alpha * (x[i] - y[i - 1])
    return y


def compute_envelope(sig: np.ndarray, fs: float) -> np.ndarray:
    if sig.size == 0:
        return sig
    env = np.abs(np.asarray(sig, dtype=float))
    tau = 0.010
    alpha = 1.0 - np.exp(-1.0 / max(fs * tau, 1.0))
    out = np.empty_like(env)
    out[0] = env[0]
    for i in range(1, env.size):
        out[i] = out[i - 1] + alpha * (env[i] - out[i - 1])
    return out


def clean_spectrogram_db(sxx_db: np.ndarray) -> np.ndarray:
    if sxx_db.size == 0:
        return sxx_db
    out = scipy_signal.medfilt2d(sxx_db, kernel_size=3)
    lo = float(np.percentile(out, 10))
    hi = float(np.percentile(out, 99.5))
    if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
        return out
    return np.clip(out, lo, hi)