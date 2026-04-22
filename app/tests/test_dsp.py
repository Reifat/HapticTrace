# Copyright 2026 Nikolai Kolesnikov
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import numpy as np

from app.dsp import (
    causal_filter,
    clean_spectrogram_db,
    compute_envelope,
    pca_first_component,
    robust_scale_value,
    windowed_overlap_interpolate,
)


def test_robust_scale_value_returns_one_for_empty_input() -> None:
    assert robust_scale_value(np.array([])) == 1.0


def test_pca_first_component_falls_back_to_max_variance_axis_on_short_input() -> None:
    xyz = np.array([[0.0, 0.0, 0.0], [0.0, 5.0, 0.0]])
    pc1, vec, eigvals = pca_first_component(xyz)

    assert pc1.shape == (2,)
    assert np.argmax(np.abs(vec)) == 1
    assert eigvals.shape == (3,)


def test_causal_filter_returns_original_signal_when_fs_too_low() -> None:
    signal = np.array([0.0, 1.0, -1.0, 0.5])
    filtered = causal_filter(signal, fs=2.0)

    np.testing.assert_allclose(filtered, signal)


def test_compute_envelope_is_non_negative_and_preserves_shape() -> None:
    signal = np.array([0.0, -1.0, 2.0, -3.0, 1.0])
    envelope = compute_envelope(signal, fs=200.0)

    assert envelope.shape == signal.shape
    assert np.all(envelope >= 0.0)


def test_clean_spectrogram_db_suppresses_large_outlier() -> None:
    data = np.full((5, 5), -80.0)
    data[2, 2] = 40.0

    cleaned = clean_spectrogram_db(data)

    assert cleaned.shape == data.shape
    assert cleaned[2, 2] < 40.0


def test_windowed_overlap_interpolate_returns_original_when_not_enough_points() -> None:
    times = np.array([0.0, 0.1, 0.2])
    values = np.array([0.0, 1.0, 0.0])

    interp_t, interp_v, meta = windowed_overlap_interpolate(
        times,
        values,
        target_samples_on_window=64,
        window_ms=120.0,
        overlap_ratio=0.6,
        window_kind="hann",
        method="lagrange",
        poly_order=4,
        post_smoothing="none",
    )

    np.testing.assert_allclose(interp_t, times)
    np.testing.assert_allclose(interp_v, values)
    assert meta["applied"] is False


def test_windowed_overlap_interpolate_upsamples_and_keeps_monotonic_timebase() -> None:
    times = np.linspace(0.0, 0.5, 50)
    values = np.sin(times * 12.0)

    interp_t, interp_v, meta = windowed_overlap_interpolate(
        times,
        values,
        target_samples_on_window=240,
        window_ms=120.0,
        overlap_ratio=0.6,
        window_kind="hann",
        method="pchip",
        poly_order=4,
        post_smoothing="none",
    )

    assert meta["applied"] is True
    assert interp_t.size > times.size
    assert interp_v.shape == interp_t.shape
    assert np.all(np.diff(interp_t) > 0)