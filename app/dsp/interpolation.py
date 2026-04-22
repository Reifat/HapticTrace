# Copyright 2026 Nikolai Kolesnikov
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from typing import Any, Dict, Tuple

import numpy as np
from scipy import signal as scipy_signal
from scipy.interpolate import Akima1DInterpolator, BarycentricInterpolator, PchipInterpolator


def build_window_weights(length: int, kind: str) -> np.ndarray:
    if length <= 1:
        return np.ones(max(length, 1), dtype=float)
    if kind == "triangular":
        weights = 1.0 - np.abs(np.linspace(-1.0, 1.0, length))
    else:
        weights = np.hanning(length)
    if not np.any(weights > 0):
        weights = np.ones(length, dtype=float)
    return np.asarray(weights, dtype=float)


def soft_clamp_values(values: np.ndarray, low: float, high: float, margin_ratio: float = 0.15) -> np.ndarray:
    if values.size == 0:
        return values
    low_f = float(low)
    high_f = float(high)
    if not np.isfinite(low_f) or not np.isfinite(high_f):
        return values
    if high_f < low_f:
        low_f, high_f = high_f, low_f
    span = max(high_f - low_f, 1e-9)
    margin = span * max(float(margin_ratio), 0.0)
    center = 0.5 * (low_f + high_f)
    half = max(0.5 * span + margin, 1e-9)
    return center + half * np.tanh((np.asarray(values, dtype=float) - center) / half)


def interpolate_support(
    query_t: np.ndarray,
    support_t: np.ndarray,
    support_v: np.ndarray,
    method: str,
    poly_order: int,
) -> np.ndarray:
    if support_t.size < 2:
        raise ValueError("Not enough support points")
    if method == "linear":
        return np.interp(query_t, support_t, support_v)
    if method == "pchip":
        return PchipInterpolator(support_t, support_v, extrapolate=True)(query_t)
    if method == "akima":
        return Akima1DInterpolator(support_t, support_v)(query_t)
    if method != "lagrange":
        raise ValueError(f"Unknown interpolation method: {method}")
    effective_order = max(1, min(int(poly_order), support_t.size - 1))
    support_size = effective_order + 1
    out = np.empty_like(query_t, dtype=float)
    for idx, x in enumerate(query_t):
        pivot = int(np.searchsorted(support_t, x))
        start = max(0, min(pivot - support_size // 2, support_t.size - support_size))
        end = start + support_size
        local_t = support_t[start:end]
        local_v = support_v[start:end]
        interpolator = BarycentricInterpolator(local_t, local_v)
        out[idx] = float(interpolator(float(x)))
    return out


def interpolate_support_with_fallback(
    query_t: np.ndarray,
    support_t: np.ndarray,
    support_v: np.ndarray,
    method: str,
    poly_order: int,
) -> Tuple[np.ndarray, str]:
    try:
        return interpolate_support(query_t, support_t, support_v, method, poly_order), method
    except Exception:
        if method != "pchip":
            try:
                return interpolate_support(query_t, support_t, support_v, "pchip", poly_order), "pchip"
            except Exception:
                return interpolate_support(query_t, support_t, support_v, "linear", poly_order), "linear"
        return interpolate_support(query_t, support_t, support_v, "linear", poly_order), "linear"


def simple_window_lagrange(
    query_t: np.ndarray,
    support_t: np.ndarray,
    support_v: np.ndarray,
    poly_order: int,
) -> Tuple[np.ndarray, int]:
    if query_t.size == 0:
        return np.empty(0, dtype=float), 0
    if support_t.size < 2:
        raise ValueError("Not enough support points for lagrange")
    support_size = max(2, min(int(poly_order) + 1, int(support_t.size)))
    accum = np.zeros(query_t.size, dtype=float)
    weight_sum = np.zeros(query_t.size, dtype=float)
    segment_count = 0
    segment_step = max(1, support_size - 1)
    for seg_start in range(0, support_t.size - 1, segment_step):
        seg_end = min(seg_start + support_size, support_t.size)
        local_t = support_t[seg_start:seg_end]
        local_v = support_v[seg_start:seg_end]
        if local_t.size < 2:
            continue
        left_t = float(local_t[0])
        right_t = float(local_t[-1])
        if seg_end >= support_t.size:
            mask = (query_t >= left_t) & (query_t <= right_t)
        else:
            mask = (query_t >= left_t) & (query_t < right_t)
        if not np.any(mask):
            continue
        values, _ = interpolate_support_with_fallback(
            query_t[mask],
            local_t,
            local_v,
            "lagrange",
            max(1, min(int(poly_order), local_t.size - 1)),
        )
        values = soft_clamp_values(values, float(np.min(local_v)), float(np.max(local_v)))
        accum[mask] += values
        weight_sum[mask] += 1.0
        segment_count += 1
    valid = weight_sum > 1e-12
    out = np.empty_like(query_t, dtype=float)
    out[valid] = accum[valid] / weight_sum[valid]
    if np.any(~valid):
        fallback, _ = interpolate_support_with_fallback(
            query_t[~valid],
            support_t,
            support_v,
            "lagrange",
            max(1, min(int(poly_order), support_t.size - 1)),
        )
        out[~valid] = soft_clamp_values(fallback, float(np.min(support_v)), float(np.max(support_v)))
    return out, segment_count


def windowed_overlap_interpolate(
    times: np.ndarray,
    values: np.ndarray,
    target_samples_on_window: int,
    window_ms: float,
    overlap_ratio: float,
    window_kind: str,
    method: str,
    poly_order: int,
    post_smoothing: str,
) -> Tuple[np.ndarray, np.ndarray, Dict[str, Any]]:
    t = np.asarray(times, dtype=float)
    v = np.asarray(values, dtype=float)
    if t.size < 4 or v.size != t.size:
        return t.copy(), v.copy(), {"applied": False, "reason": "not_enough_points"}
    uniq_mask = np.concatenate(([True], np.diff(t) > 0))
    t = t[uniq_mask]
    v = v[uniq_mask]
    if t.size < 4:
        return t.copy(), v.copy(), {"applied": False, "reason": "not_enough_unique_points"}
    base_fs = 1.0 / max(np.median(np.diff(t)), 1e-6)
    window_s = max(float(window_ms), 10.0) / 1000.0
    target_samples = max(int(target_samples_on_window), 8)
    derived_target_fs = target_samples / max(window_s, 1e-6)
    capped_target_fs = min(max(float(derived_target_fs), base_fs), 1000.0)
    if capped_target_fs <= base_fs * 1.05:
        return t.copy(), v.copy(), {
            "applied": False,
            "reason": "target_fs_not_higher",
            "base_fs": base_fs,
            "target_fs": capped_target_fs,
            "target_samples_on_window": target_samples,
        }
    target_dt = 1.0 / capped_target_fs
    target_time = np.arange(float(t[0]), float(t[-1]) + 0.5 * target_dt, target_dt, dtype=float)
    if target_time.size < 4:
        return t.copy(), v.copy(), {
            "applied": False,
            "reason": "target_grid_too_small",
            "base_fs": base_fs,
            "target_fs": capped_target_fs,
            "target_samples_on_window": target_samples,
        }
    window_samples = max(8, int(round(capped_target_fs * window_s)))
    if window_samples > target_time.size:
        window_samples = target_time.size
    hop_samples = max(1, int(round(window_samples * (1.0 - min(max(float(overlap_ratio), 0.0), 0.95)))))
    hop_samples = min(hop_samples, max(window_samples - 1, 1)) if window_samples > 1 else 1
    weights = build_window_weights(window_samples, window_kind)
    accum = np.zeros_like(target_time)
    weight_sum = np.zeros_like(target_time)
    pad_s = max(float(window_ms) / 1000.0, target_dt * 4.0)
    lagrange_segments = 0
    for start in range(0, target_time.size, hop_samples):
        end = min(start + window_samples, target_time.size)
        chunk_t = target_time[start:end]
        if chunk_t.size < 2:
            continue
        left = chunk_t[0] - pad_s
        right = chunk_t[-1] + pad_s
        mask = (t >= left) & (t <= right)
        local_t = t[mask]
        local_v = v[mask]
        if local_t.size < 2:
            continue
        effective_order = max(1, min(int(poly_order), local_t.size - 1))
        if method == "lagrange":
            chunk_values, chunk_segment_count = simple_window_lagrange(
                chunk_t,
                local_t,
                local_v,
                effective_order,
            )
            lagrange_segments += int(chunk_segment_count)
        else:
            chunk_values, _ = interpolate_support_with_fallback(chunk_t, local_t, local_v, method, effective_order)
            chunk_values = soft_clamp_values(chunk_values, float(np.min(local_v)), float(np.max(local_v)))
        chunk_weights = weights[: chunk_t.size] if end - start < window_samples else weights
        if np.count_nonzero(chunk_weights) == 0:
            chunk_weights = np.ones(chunk_t.size, dtype=float)
        accum[start:end] += chunk_values * chunk_weights
        weight_sum[start:end] += chunk_weights
        if end >= target_time.size:
            break
    out = np.empty_like(target_time)
    valid = weight_sum > 1e-12
    out[valid] = accum[valid] / weight_sum[valid]
    if np.any(~valid):
        out[~valid] = np.interp(target_time[~valid], t, v)
    if post_smoothing == "savgol" and out.size >= 5:
        smooth_window = max(5, int(round(capped_target_fs * 0.015)))
        if smooth_window % 2 == 0:
            smooth_window += 1
        if smooth_window > out.size:
            smooth_window = out.size if out.size % 2 == 1 else out.size - 1
        if smooth_window >= 5:
            out = scipy_signal.savgol_filter(
                out,
                window_length=smooth_window,
                polyorder=min(2, smooth_window - 2),
                mode="interp",
            )
    return target_time, out, {
        "applied": True,
        "base_fs": base_fs,
        "target_fs": capped_target_fs,
        "target_samples_on_window": target_samples,
        "window_ms": float(window_ms),
        "overlap_ratio": float(overlap_ratio),
        "window_kind": window_kind,
        "method": method,
        "poly_order": int(poly_order),
        "post_smoothing": post_smoothing,
        "fallback": "pchip_then_linear",
        "lagrange_segments": lagrange_segments if method == "lagrange" else 0,
    }