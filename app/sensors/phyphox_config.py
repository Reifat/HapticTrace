# Copyright 2026 Nikolai Kolesnikov
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple


def normalize_name(name: str) -> str:
    return "".join(ch.lower() for ch in str(name) if ch.isalnum())


def extract_named_records(obj: Any, out: List[Dict[str, str]]) -> None:
    if isinstance(obj, dict):
        label = obj.get("label")
        name = obj.get("name")
        buf = obj.get("buffer") or obj.get("source") or obj.get("input")
        if isinstance(buf, str) and (isinstance(label, str) or isinstance(name, str)):
            out.append({"buffer": buf, "label": str(label or name or "")})
        for value in obj.values():
            extract_named_records(value, out)
    elif isinstance(obj, list):
        for item in obj:
            extract_named_records(item, out)


def fuzzy_pick(records: Sequence[Tuple[str, str]], families: Sequence[str], axis: Optional[str]) -> Optional[str]:
    axis_norm = normalize_name(axis) if axis else ""
    family_norms = [normalize_name(family) for family in families]
    family_join = "".join(family_norms)
    scored: List[Tuple[int, str]] = []
    for buf, label in records:
        text = normalize_name(f"{buf} {label}")
        if not any(family in text for family in family_norms):
            continue
        score = 0
        if axis_norm:
            if "time" in text or text.endswith("timestamp") or text.endswith("seconds") or text == "t":
                continue
            if text.endswith(axis_norm):
                score += 120
            if f"_{axis_norm}" in str(buf).lower() or f"-{axis_norm}" in str(buf).lower():
                score += 80
            if f"axis{axis_norm}" in text or f"{axis_norm}axis" in text:
                score += 70
            if axis_norm == "x" and any(key in text for key in ["accx", "gyrx", "gyrox", "rotationratex", "accelerationx"]):
                score += 100
            if axis_norm == "y" and any(key in text for key in ["accy", "gyry", "gyroy", "rotationratey", "accelerationy"]):
                score += 100
            if axis_norm == "z" and any(key in text for key in ["accz", "gyrz", "gyroz", "rotationratez", "accelerationz"]):
                score += 100
            if axis_norm in text:
                score += 20
            other_axes = {"x", "y", "z"} - {axis_norm}
            if any(text.endswith(other_axis) for other_axis in other_axes):
                score -= 40
        else:
            if "time" in text or "timestamp" in text or text in {"t", "time", "seconds"}:
                score += 120
            else:
                continue
            if "gyro" in family_join or "gyr" in family_join or "rotationrate" in family_join:
                if any(key in text for key in ["gyrtime", "gyrotime", "gyr_time", "gyro_time", "rotationratetime", "gyrtimestamp"]):
                    score += 120
                elif "acc" in text:
                    score -= 80
            if "acc" in family_join:
                if any(key in text for key in ["acctime", "acc_time", "accelerationtime", "acctimestamp"]):
                    score += 120
                elif "gyr" in text or "gyro" in text:
                    score -= 80
        scored.append((score, buf))
    if not scored:
        return None
    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[0][1]


@dataclass
class SensorBuffers:
    time: Optional[str]
    x: Optional[str]
    y: Optional[str]
    z: Optional[str]

    @property
    def complete(self) -> bool:
        return all([self.time, self.x, self.y, self.z])

    @property
    def xyz_complete(self) -> bool:
        return all([self.x, self.y, self.z])


def detect_sensor_buffers(config: dict, sensor_type: str) -> SensorBuffers:
    records: List[Tuple[str, str]] = []
    for item in config.get("buffers", []):
        if isinstance(item, dict) and isinstance(item.get("name"), str):
            name = item["name"]
            records.append((name, name))
    extracted: List[Dict[str, str]] = []
    extract_named_records(config, extracted)
    for rec in extracted:
        records.append((rec["buffer"], rec["label"]))
    seen = set()
    unique_records: List[Tuple[str, str]] = []
    for buf, label in records:
        key = (buf, label)
        if key in seen:
            continue
        seen.add(key)
        unique_records.append((buf, label))
    records = unique_records
    if sensor_type == "accel":
        families = ["acc", "accelerometer", "acceleration"]
        exact_candidates = {
            "time": ["acctime", "acc_time", "accelerationtime", "acceleration_time"],
            "x": ["accx", "acc_x", "accelerationx", "acceleration_x"],
            "y": ["accy", "acc_y", "accelerationy", "acceleration_y"],
            "z": ["accz", "acc_z", "accelerationz", "acceleration_z"],
        }
    else:
        families = ["gyr", "gyro", "gyroscope", "rotationrate"]
        exact_candidates = {
            "time": ["gyrtime", "gyr_time", "gyrotime", "gyro_time", "rotationratetime", "rotationrate_time"],
            "x": ["gyrx", "gyr_x", "gyrox", "gyro_x", "rotationratex", "rotationrate_x"],
            "y": ["gyry", "gyr_y", "gyroy", "gyro_y", "rotationratey", "rotationrate_y"],
            "z": ["gyrz", "gyr_z", "gyroz", "gyro_z", "rotationratez", "rotationrate_z"],
        }
    norm_to_buf = {normalize_name(buf): buf for buf, _ in records}

    def exact_pick(kind: str) -> Optional[str]:
        for candidate in exact_candidates[kind]:
            norm_candidate = normalize_name(candidate)
            if norm_candidate in norm_to_buf:
                return norm_to_buf[norm_candidate]
        return None

    return SensorBuffers(
        time=exact_pick("time") or fuzzy_pick(records, families, None),
        x=exact_pick("x") or fuzzy_pick(records, families, "x"),
        y=exact_pick("y") or fuzzy_pick(records, families, "y"),
        z=exact_pick("z") or fuzzy_pick(records, families, "z"),
    )