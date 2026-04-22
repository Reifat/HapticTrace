# Copyright 2026 Nikolai Kolesnikov
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import csv
import json
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Sequence

import numpy as np

from app.core.parsing import parse_optional_float
from app.sensors import SensorBuffers

if TYPE_CHECKING:
    from app.core.phyphox_runtime import PhyphoxService


def export_phyphox_data(
    runtime: "PhyphoxService",
    out_dir: Path,
    use_acc: bool,
    use_gyr: bool,
    extra_meta: Dict[str, Any],
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    meta = {
        "url": runtime.base_url,
        "saved_at_unix": time.time(),
        "config": runtime.config_json,
        "acc_buffers": None if runtime.acc_buffers is None else runtime.acc_buffers.__dict__,
        "gyr_buffers": None if runtime.gyr_buffers is None else runtime.gyr_buffers.__dict__,
        "sensor_enabled": {"accel": use_acc, "gyro": use_gyr},
        "latest_meta": runtime.latest_meta,
    }
    meta.update(extra_meta)
    (out_dir / "session_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    with runtime.lock:
        for key in ["accel", "gyro"]:
            state = runtime.states[key]
            raw_t = np.asarray(state.raw_time, dtype=float)
            if raw_t.size:
                write_csv_rows(
                    out_dir / f"{key}_raw.csv",
                    ["time_s", "x", "y", "z"],
                    zip(
                        raw_t.tolist(),
                        np.asarray(state.raw_x, dtype=float).tolist(),
                        np.asarray(state.raw_y, dtype=float).tolist(),
                        np.asarray(state.raw_z, dtype=float).tolist(),
                    ),
                )
            proc_t = np.asarray(state.processed_time, dtype=float)
            if proc_t.size:
                write_csv_rows(
                    out_dir / f"{key}_processed.csv",
                    ["time_s", "pc1_normalized"],
                    zip(proc_t.tolist(), np.asarray(state.processed_sig, dtype=float).tolist()),
                )
        ct = np.asarray(runtime.combined_state.time, dtype=float)
        if ct.size:
            write_csv_rows(
                out_dir / "combined_signal.csv",
                ["time_s", "combined_signal"],
                zip(ct.tolist(), np.asarray(runtime.combined_state.sig, dtype=float).tolist()),
            )
    return out_dir


def load_phyphox_data(runtime: "PhyphoxService", session_dir: Path) -> Dict[str, Any]:
    meta_path = session_dir / "session_meta.json"
    if not meta_path.exists():
        raise RuntimeError("session_meta.json not found in the session archive")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    runtime.reset_processing_states()
    runtime.is_measuring = False
    from app.network import normalize_url

    runtime.base_url = normalize_url(str(meta.get("url") or runtime.base_url))
    runtime.config_json = meta.get("config")
    acc_meta = meta.get("acc_buffers")
    gyr_meta = meta.get("gyr_buffers")
    runtime.acc_buffers = SensorBuffers(**acc_meta) if isinstance(acc_meta, dict) else None
    runtime.gyr_buffers = SensorBuffers(**gyr_meta) if isinstance(gyr_meta, dict) else None
    runtime.latest_meta = meta.get("latest_meta") or {}
    parts_meta = {
        str(item.get("sensor")): item
        for item in runtime.latest_meta.get("parts_meta", [])
        if isinstance(item, dict) and item.get("sensor")
    }
    with runtime.lock:
        for key in ["accel", "gyro"]:
            state = runtime.states[key]
            raw_rows = _read_csv_rows(session_dir / f"{key}_raw.csv")
            for row in raw_rows:
                raw_time = float(row["time_s"])
                state.raw_time.append(raw_time)
                state.raw_x.append(float(row["x"]))
                state.raw_y.append(float(row["y"]))
                state.raw_z.append(float(row["z"]))
                state.last_raw_time = raw_time
            proc_rows = _read_csv_rows(session_dir / f"{key}_processed.csv")
            for row in proc_rows:
                state.processed_time.append(float(row["time_s"]))
                state.processed_sig.append(float(row["pc1_normalized"]))
            if len(state.raw_time):
                state.processed_upto_raw = len(state.raw_time)
            elif len(state.processed_time):
                state.processed_upto_raw = len(state.processed_time)
            meta_item = parts_meta.get(key) or {}
            pca_vec = meta_item.get("pca_vec")
            eigvals = meta_item.get("eigvals")
            state.pca_vec = np.asarray(pca_vec, dtype=float) if isinstance(pca_vec, list) and pca_vec else None
            state.eigvals = np.asarray(eigvals, dtype=float) if isinstance(eigvals, list) and eigvals else None
            state.scale_est = parse_optional_float(meta_item.get("scale_est"))
        combined_rows = _read_csv_rows(session_dir / "combined_signal.csv")
        for row in combined_rows:
            runtime.combined_state.time.append(float(row["time_s"]))
            runtime.combined_state.sig.append(float(row["combined_signal"]))
        runtime.combined_state.scale_est = parse_optional_float(runtime.latest_meta.get("combined_scale_est"))
        acc_processed_len = len(runtime.states["accel"].processed_time)
        gyr_processed_len = len(runtime.states["gyro"].processed_time)
        runtime.combined_state.processed_upto_base = max(acc_processed_len, gyr_processed_len)
    runtime.set_info(f"Loaded session from {session_dir.name}")
    return meta


def write_csv_rows(path: Path, headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as file_obj:
        writer = csv.writer(file_obj)
        writer.writerow(headers)
        for row in rows:
            writer.writerow(list(row))


def _read_csv_rows(path: Path) -> List[Dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as file_obj:
        return list(csv.DictReader(file_obj))