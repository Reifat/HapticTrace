# Copyright 2026 Nikolai Kolesnikov
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import threading
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from app.dsp import (
    CombinedState,
    IncrementalSensorState,
    InterpolationConfig,
    causal_filter,
    pca_first_component,
    robust_scale_value,
    windowed_overlap_interpolate,
)
from app.network import PhyphoxClient, normalize_url
from app.sensors import SensorBuffers, detect_sensor_buffers

from app.core.contracts import SessionTrackerProtocol


class PhyphoxService:
    def __init__(self, base_url: str = "http://192.168.1.1:8080") -> None:
        self.base_url = normalize_url(base_url)
        self.client: Optional[PhyphoxClient] = None
        self.connected = False
        self.is_measuring = False
        self.config_json: Optional[dict] = None
        self.acc_buffers: Optional[SensorBuffers] = None
        self.gyr_buffers: Optional[SensorBuffers] = None
        self.states: Dict[str, IncrementalSensorState] = {
            "accel": IncrementalSensorState(),
            "gyro": IncrementalSensorState(),
        }
        self.combined_state = CombinedState()
        self.latest_meta: Dict[str, Any] = {}
        self.stop_event = threading.Event()
        self.worker: Optional[threading.Thread] = None
        self.lock = threading.Lock()
        self.status_text = "Disconnected"
        self.experiment_status = "Experiment: unknown"
        self.sensor_status = "Sensors: unknown"
        self.info_text = ""
        self.session_tracker: Optional[SessionTrackerProtocol] = None
        self._interp_cache_key: Optional[Tuple[Any, ...]] = None
        self._interp_cache_value: Optional[Dict[str, Any]] = None

    def set_session_tracker(self, tracker: SessionTrackerProtocol) -> None:
        self.session_tracker = tracker

    def connect_async(self, base_url: str) -> bool:
        self.base_url = normalize_url(base_url)
        if self.worker and self.worker.is_alive():
            return False
        self.stop_event.clear()
        self.worker = threading.Thread(target=self._connect_and_poll_loop, daemon=True)
        self.worker.start()
        return True

    def disconnect(self) -> None:
        self.stop_event.set()
        self.connected = False
        self.is_measuring = False
        self.client = None
        self._set_status("Disconnected")
        self._set_experiment_status("Experiment: unknown")
        self._set_sensor_status("Sensors: unknown")
        self._set_info("")

    def _set_status(self, text: str) -> None:
        self.status_text = text

    def _set_info(self, text: str) -> None:
        self.info_text = text

    def set_info(self, text: str) -> None:
        self._set_info(text)

    def _set_experiment_status(self, text: str) -> None:
        self.experiment_status = text

    def _set_sensor_status(self, text: str) -> None:
        self.sensor_status = text

    def _connect_and_poll_loop(self) -> None:
        retry_delay = 1.0
        while not self.stop_event.is_set():
            try:
                url = normalize_url(self.base_url)
                self.base_url = url
                self._set_status(f"Connecting to {url} ...")
                self.client = PhyphoxClient(url)
                self.client.get_root()
                self.config_json = self.client.get_config()
                self.acc_buffers = detect_sensor_buffers(self.config_json, "accel")
                self.gyr_buffers = detect_sensor_buffers(self.config_json, "gyro")
                self.connected = True
                retry_delay = 1.0
                acc_ok = bool(self.acc_buffers and self.acc_buffers.complete)
                gyr_ok = bool(self.gyr_buffers and self.gyr_buffers.complete)
                sensor_messages = []
                if acc_ok:
                    sensor_messages.append(
                        f"accelerometer OK: t={self.acc_buffers.time}, x={self.acc_buffers.x}, y={self.acc_buffers.y}, z={self.acc_buffers.z}"
                    )
                else:
                    sensor_messages.append("accelerometer not found")
                if gyr_ok:
                    sensor_messages.append(
                        f"gyroscope OK: t={self.gyr_buffers.time}, x={self.gyr_buffers.x}, y={self.gyr_buffers.y}, z={self.gyr_buffers.z}"
                    )
                else:
                    sensor_messages.append("gyroscope not found")
                self._set_sensor_status("Sensors: " + " | ".join(sensor_messages))
                self._set_info("Connected")
                self._set_status("Connected")
                self._poll_loop()
            except Exception as exc:
                self.connected = False
                self.is_measuring = False
                self._set_status(f"Retrying in {retry_delay:.1f}s: {exc}")
                self._set_experiment_status("Experiment: unknown")
                self._set_sensor_status("Sensors: unknown")
                time.sleep(retry_delay)
                retry_delay = min(5.0, retry_delay + 0.5)

    def _poll_loop(self) -> None:
        assert self.client is not None
        while not self.stop_event.is_set() and self.connected and self.client:
            try:
                status = self.client.get_status()
                measuring = bool(status.get("measuring", False))
                self.is_measuring = measuring
                session = status.get("session", "?")
                self._set_experiment_status(f"Experiment: {'running' if measuring else 'paused'} | session={session}")
                if self.acc_buffers and self.acc_buffers.xyz_complete:
                    acc_json = self.client.poll_since(self.acc_buffers, self.states["accel"].last_raw_time)
                    self._append_sensor_json("accel", acc_json, self.acc_buffers)
                if self.gyr_buffers and self.gyr_buffers.xyz_complete:
                    gyr_json = self.client.poll_since(self.gyr_buffers, self.states["gyro"].last_raw_time)
                    self._append_sensor_json("gyro", gyr_json, self.gyr_buffers)
                time.sleep(0.08)
            except Exception as exc:
                self.connected = False
                self.is_measuring = False
                self._set_status(f"Connection lost, retrying: {exc}")
                self._set_info(traceback.format_exc(limit=1))
                return

    def _append_sensor_json(self, key: str, json_obj: dict, sensor: SensorBuffers) -> None:
        state = self.states[key]
        buf = json_obj.get("buffer", {})
        if not buf:
            return
        xvals = buf.get(sensor.x, {}).get("buffer", []) if sensor.x else []
        yvals = buf.get(sensor.y, {}).get("buffer", []) if sensor.y else []
        zvals = buf.get(sensor.z, {}).get("buffer", []) if sensor.z else []
        if not xvals or not yvals or not zvals:
            return
        if sensor.time and sensor.time in buf:
            tvals = buf.get(sensor.time, {}).get("buffer", [])
        else:
            n_est = min(len(xvals), len(yvals), len(zvals))
            start = 0.0 if state.last_raw_time is None else state.last_raw_time + 1e-3
            dt = 1.0 / 200.0
            tvals = [start + i * dt for i in range(n_est)]
        n = min(len(tvals), len(xvals), len(yvals), len(zvals))
        if n <= 0:
            return
        with self.lock:
            for tv, xv, yv, zv in zip(tvals[:n], xvals[:n], yvals[:n], zvals[:n]):
                raw_time = float(tv)
                if state.last_raw_time is not None and raw_time <= state.last_raw_time:
                    continue
                state.raw_time.append(raw_time)
                state.raw_x.append(float(xv))
                state.raw_y.append(float(yv))
                state.raw_z.append(float(zv))
                state.last_raw_time = raw_time
                if self.session_tracker is not None:
                    self.session_tracker.mark_sensor_sample(raw_time)

    def start_measurement(self) -> bool:
        if not self.client:
            raise RuntimeError("Phyphox is not connected")
        result = self.client.control("start")
        if result:
            self.is_measuring = True
            self._set_info("Start command sent")
        return result

    def stop_measurement(self) -> bool:
        if not self.client:
            raise RuntimeError("Phyphox is not connected")
        result = self.client.control("stop")
        if result:
            self.is_measuring = False
            self._set_info("Pause command sent")
        return result

    def clear_measurement(self) -> bool:
        if not self.client:
            self.reset_processing_states()
            return True
        result = self.client.control("clear")
        if result:
            self.reset_processing_states()
            self.is_measuring = False
            self._set_info("Clear command sent")
        return result

    def reset_processing_states(self) -> None:
        with self.lock:
            self.states = {
                "accel": IncrementalSensorState(),
                "gyro": IncrementalSensorState(),
            }
            self.combined_state = CombinedState()
            self.latest_meta = {}
            self._interp_cache_key = None
            self._interp_cache_value = None

    def has_acc(self) -> bool:
        return bool(self.acc_buffers and self.acc_buffers.complete)

    def has_gyr(self) -> bool:
        return bool(self.gyr_buffers and self.gyr_buffers.complete)

    def has_any_data(self) -> bool:
        with self.lock:
            return bool(
                len(self.combined_state.time)
                or len(self.states["accel"].processed_time)
                or len(self.states["gyro"].processed_time)
            )

    def _incremental_process_sensor(self, key: str) -> None:
        state = self.states[key]
        times = np.asarray(state.raw_time, dtype=float)
        if times.size < 32:
            return
        new_count = times.size - state.processed_upto_raw
        if new_count <= 0:
            return
        overlap = 256
        start_emit = state.processed_upto_raw
        window_start = max(0, start_emit - overlap)
        t = times[window_start:]
        x = np.asarray(state.raw_x, dtype=float)[window_start:]
        y = np.asarray(state.raw_y, dtype=float)[window_start:]
        z = np.asarray(state.raw_z, dtype=float)[window_start:]
        uniq_mask = np.concatenate(([True], np.diff(t) > 0))
        t = t[uniq_mask]
        x = x[uniq_mask]
        y = y[uniq_mask]
        z = z[uniq_mask]
        if t.size < 16:
            return
        fs = 1.0 / max(np.median(np.diff(t)), 1e-6)
        xyz = np.column_stack([x, y, z])
        pc1, pc_vec, eigvals = pca_first_component(xyz)
        filtered = causal_filter(pc1, fs=fs)
        emit_count = min(new_count, filtered.size)
        if emit_count <= 0:
            return
        filtered_new = filtered[-emit_count:]
        times_new = times[-emit_count:]
        chunk_scale = robust_scale_value(filtered_new, q=95.0)
        if state.scale_est is None:
            state.scale_est = chunk_scale
        else:
            state.scale_est = max(1e-6, 0.97 * state.scale_est + 0.03 * chunk_scale)
        norm_new = filtered_new / max(state.scale_est, 1e-6)
        for time_value, signal_value in zip(times_new, norm_new):
            state.processed_time.append(float(time_value))
            state.processed_sig.append(float(signal_value))
        state.processed_upto_raw = times.size
        state.pca_vec = pc_vec
        state.eigvals = eigvals

    def _incremental_combine(self, use_acc: bool, use_gyr: bool) -> None:
        selected: List[str] = []
        if use_acc and self.has_acc():
            selected.append("accel")
        if use_gyr and self.has_gyr():
            selected.append("gyro")
        cs = self.combined_state
        if not selected:
            return
        if len(selected) == 1:
            key = selected[0]
            state = self.states[key]
            proc_t = list(state.processed_time)
            proc_v = list(state.processed_sig)
            new_count = len(proc_t) - cs.processed_upto_base
            if new_count <= 0:
                return
            times_new = np.asarray(proc_t[-new_count:], dtype=float)
            sig_new = np.asarray(proc_v[-new_count:], dtype=float)
            chunk_scale = robust_scale_value(sig_new)
            if cs.scale_est is None:
                cs.scale_est = chunk_scale
            else:
                cs.scale_est = max(1e-6, 0.97 * cs.scale_est + 0.03 * chunk_scale)
            sig_norm = sig_new / max(cs.scale_est, 1e-6)
            for time_value, signal_value in zip(times_new, sig_norm):
                cs.time.append(float(time_value))
                cs.sig.append(float(signal_value))
            cs.processed_upto_base = len(proc_t)
            return
        base_key = "accel" if "accel" in selected else selected[0]
        other_key = "gyro" if base_key == "accel" else "accel"
        base = self.states[base_key]
        other = self.states[other_key]
        base_t = np.asarray(base.processed_time, dtype=float)
        base_v = np.asarray(base.processed_sig, dtype=float)
        if base_t.size == 0:
            return
        new_count = base_t.size - cs.processed_upto_base
        if new_count <= 0:
            return
        times_new = base_t[-new_count:]
        base_new = base_v[-new_count:]
        other_t = np.asarray(other.processed_time, dtype=float)
        other_v = np.asarray(other.processed_sig, dtype=float)
        if other_t.size >= 2:
            other_interp = np.interp(times_new, other_t, other_v, left=0.0, right=0.0)
        else:
            other_interp = np.zeros_like(base_new)
        combined_raw = 0.5 * (base_new + other_interp)
        chunk_scale = robust_scale_value(combined_raw)
        if cs.scale_est is None:
            cs.scale_est = chunk_scale
        else:
            cs.scale_est = max(1e-6, 0.97 * cs.scale_est + 0.03 * chunk_scale)
        combined_norm = combined_raw / max(cs.scale_est, 1e-6)
        for time_value, signal_value in zip(times_new, combined_norm):
            cs.time.append(float(time_value))
            cs.sig.append(float(signal_value))
        cs.processed_upto_base = base_t.size

    def update_processing(self, use_acc: bool, use_gyr: bool) -> None:
        with self.lock:
            for key in ["accel", "gyro"]:
                enabled = (key == "accel" and self.has_acc()) or (key == "gyro" and self.has_gyr())
                if enabled:
                    self._incremental_process_sensor(key)
            self._incremental_combine(use_acc, use_gyr)

    def current_output(
        self,
        use_acc: bool,
        use_gyr: bool,
        interpolation_config: Optional[InterpolationConfig] = None,
    ) -> Optional[Dict[str, Any]]:
        with self.lock:
            selected: List[str] = []
            if use_acc and self.has_acc():
                selected.append("accel")
            if use_gyr and self.has_gyr():
                selected.append("gyro")
            if not selected:
                return None
            if len(selected) == 1:
                key = selected[0]
                state = self.states[key]
                t = np.asarray(state.processed_time, dtype=float)
                v = np.asarray(state.processed_sig, dtype=float)
                label = "Accelerometer" if key == "accel" else "Gyroscope"
                meta = {
                    "used": [key],
                    "parts_meta": [{
                        "sensor": key,
                        "pca_vec": None if state.pca_vec is None else state.pca_vec.tolist(),
                        "eigvals": None if state.eigvals is None else state.eigvals.tolist(),
                        "scale_est": state.scale_est,
                    }],
                }
            else:
                t = np.asarray(self.combined_state.time, dtype=float)
                v = np.asarray(self.combined_state.sig, dtype=float)
                label = "Accelerometer + Gyroscope"
                meta = {
                    "used": selected,
                    "parts_meta": [{
                        "sensor": key,
                        "pca_vec": None if self.states[key].pca_vec is None else self.states[key].pca_vec.tolist(),
                        "eigvals": None if self.states[key].eigvals is None else self.states[key].eigvals.tolist(),
                        "scale_est": self.states[key].scale_est,
                    } for key in selected],
                    "combined_scale_est": self.combined_state.scale_est,
                }
            if t.size < 16:
                return None
            rel_t = t - t[0]
            fs = 1.0 / max(np.median(np.diff(t)), 1e-6)
            result = {"times": t, "rel_t": rel_t, "signal": v, "fs": fs, "label": label, "meta": dict(meta)}
            if interpolation_config is not None and interpolation_config.enabled:
                cache_key = (
                    tuple(selected),
                    int(t.size),
                    float(t[0]),
                    float(t[-1]),
                    float(fs),
                    round(float(v[-1]), 9),
                    int(interpolation_config.target_samples_on_window),
                    round(float(interpolation_config.window_ms), 6),
                    round(float(interpolation_config.overlap_ratio), 6),
                    interpolation_config.window_kind,
                    interpolation_config.method,
                    int(interpolation_config.poly_order),
                    interpolation_config.post_smoothing,
                )
                if self._interp_cache_key == cache_key and self._interp_cache_value is not None:
                    interp_payload = self._interp_cache_value
                else:
                    interp_t, interp_v, interp_meta = windowed_overlap_interpolate(
                        t,
                        v,
                        interpolation_config.target_samples_on_window,
                        interpolation_config.window_ms,
                        interpolation_config.overlap_ratio,
                        interpolation_config.window_kind,
                        interpolation_config.method,
                        interpolation_config.poly_order,
                        interpolation_config.post_smoothing,
                    )
                    interp_rel_t = interp_t - interp_t[0] if interp_t.size else interp_t
                    interp_fs = 1.0 / max(np.median(np.diff(interp_t)), 1e-6) if interp_t.size >= 2 else fs
                    interp_payload = {
                        "times": interp_t,
                        "rel_t": interp_rel_t,
                        "signal": interp_v,
                        "fs": interp_fs,
                        "meta": interp_meta,
                    }
                    self._interp_cache_key = cache_key
                    self._interp_cache_value = interp_payload
                result.update({
                    "times": interp_payload["times"],
                    "rel_t": interp_payload["rel_t"],
                    "signal": interp_payload["signal"],
                    "fs": interp_payload["fs"],
                })
                result["meta"]["interpolation"] = {
                    **interp_payload["meta"],
                    "enabled": True,
                    "apply_to_export": interpolation_config.apply_to_export,
                }
            else:
                result["meta"]["interpolation"] = {"applied": False, "enabled": False}
            self.latest_meta = result["meta"]
            return result

    def export_data(self, out_dir: Path, use_acc: bool, use_gyr: bool, extra_meta: Dict[str, Any]) -> Path:
        from app.core.persistence import export_phyphox_data

        return export_phyphox_data(self, out_dir, use_acc, use_gyr, extra_meta)

    def load_exported_data(self, session_dir: Path) -> Dict[str, Any]:
        from app.core.persistence import load_phyphox_data

        return load_phyphox_data(self, session_dir)