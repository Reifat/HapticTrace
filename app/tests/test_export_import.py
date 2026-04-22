# Copyright 2026 Nikolai Kolesnikov
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import numpy as np
import pytest

from app.core.phyphox_runtime import PhyphoxService
from app.sensors import SensorBuffers


def _seed_runtime() -> PhyphoxService:
    runtime = PhyphoxService("http://example.local:8080")
    runtime.config_json = {"name": "demo"}
    runtime.acc_buffers = SensorBuffers(time="acc_time", x="acc_x", y="acc_y", z="acc_z")
    runtime.gyr_buffers = SensorBuffers(time="gyr_time", x="gyr_x", y="gyr_y", z="gyr_z")

    acc = runtime.states["accel"]
    for row in [
        (0.0, 1.0, 2.0, 3.0),
        (0.1, 1.5, 2.5, 3.5),
        (0.2, 2.0, 3.0, 4.0),
    ]:
        acc.raw_time.append(row[0])
        acc.raw_x.append(row[1])
        acc.raw_y.append(row[2])
        acc.raw_z.append(row[3])
        acc.last_raw_time = row[0]
    acc.processed_time.extend([0.0, 0.1, 0.2])
    acc.processed_sig.extend([0.2, 0.4, 0.6])
    acc.processed_upto_raw = 3
    acc.scale_est = 1.25
    acc.pca_vec = np.array([1.0, 0.0, 0.0])
    acc.eigvals = np.array([3.0, 2.0, 1.0])

    gyro = runtime.states["gyro"]
    gyro.processed_time.extend([0.0, 0.1, 0.2])
    gyro.processed_sig.extend([0.1, 0.2, 0.3])
    gyro.processed_upto_raw = 3
    gyro.scale_est = 0.75
    gyro.pca_vec = np.array([0.0, 1.0, 0.0])
    gyro.eigvals = np.array([2.5, 1.0, 0.5])

    runtime.combined_state.time.extend([0.0, 0.1, 0.2])
    runtime.combined_state.sig.extend([0.15, 0.3, 0.45])
    runtime.combined_state.processed_upto_base = 3
    runtime.combined_state.scale_est = 0.9
    runtime.latest_meta = {
        "parts_meta": [
            {"sensor": "accel", "pca_vec": [1.0, 0.0, 0.0], "eigvals": [3.0, 2.0, 1.0], "scale_est": 1.25},
            {"sensor": "gyro", "pca_vec": [0.0, 1.0, 0.0], "eigvals": [2.5, 1.0, 0.5], "scale_est": 0.75},
        ],
        "combined_scale_est": 0.9,
    }
    return runtime


def test_export_import_roundtrip_restores_processing_state(tmp_path) -> None:
    source = _seed_runtime()
    export_dir = tmp_path / "session"
    source.export_data(export_dir, use_acc=True, use_gyr=True, extra_meta={"recordings": []})

    restored = PhyphoxService("http://placeholder")
    meta = restored.load_exported_data(export_dir)

    assert meta["config"] == {"name": "demo"}
    assert restored.base_url == "http://example.local:8080"
    assert list(restored.states["accel"].raw_time) == [0.0, 0.1, 0.2]
    assert list(restored.states["accel"].processed_sig) == [0.2, 0.4, 0.6]
    assert list(restored.combined_state.sig) == [0.15, 0.3, 0.45]
    assert restored.states["accel"].scale_est == 1.25
    assert restored.combined_state.scale_est == 0.9
    np.testing.assert_allclose(restored.states["accel"].pca_vec, np.array([1.0, 0.0, 0.0]))
    np.testing.assert_allclose(restored.states["accel"].eigvals, np.array([3.0, 2.0, 1.0]))


def test_load_exported_data_requires_session_metadata(tmp_path) -> None:
    runtime = PhyphoxService("http://placeholder")

    with pytest.raises(RuntimeError, match="session_meta.json not found"):
        runtime.load_exported_data(tmp_path)