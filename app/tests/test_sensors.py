# Copyright 2026 Nikolai Kolesnikov
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from app.sensors import detect_sensor_buffers, extract_named_records, normalize_name


def test_normalize_name_strips_case_and_punctuation() -> None:
    assert normalize_name("Acc X-axis") == "accxaxis"


def test_extract_named_records_walks_nested_config() -> None:
    payload = {
        "blocks": [
            {
                "label": "Acceleration X",
                "buffer": "acc_x",
            },
            {
                "nested": {
                    "name": "Gyro Time",
                    "source": "gyro_time",
                }
            },
        ]
    }
    out = []

    extract_named_records(payload, out)

    assert {"buffer": "acc_x", "label": "Acceleration X"} in out
    assert {"buffer": "gyro_time", "label": "Gyro Time"} in out


def test_detect_sensor_buffers_prefers_exact_accelerometer_names() -> None:
    config = {
        "buffers": [
            {"name": "acc_time"},
            {"name": "acc_x"},
            {"name": "acc_y"},
            {"name": "acc_z"},
        ]
    }

    buffers = detect_sensor_buffers(config, "accel")

    assert buffers.time == "acc_time"
    assert buffers.x == "acc_x"
    assert buffers.y == "acc_y"
    assert buffers.z == "acc_z"
    assert buffers.complete is True


def test_detect_sensor_buffers_fuzzy_matches_gyro_records() -> None:
    config = {
        "views": [
            {"label": "Gyroscope X", "buffer": "gyrX"},
            {"label": "Gyroscope Y", "buffer": "gyrY"},
            {"label": "Gyroscope Z", "buffer": "gyrZ"},
            {"label": "Gyro Time", "buffer": "gyro_time"},
        ]
    }

    buffers = detect_sensor_buffers(config, "gyro")

    assert buffers.time == "gyro_time"
    assert buffers.x == "gyrX"
    assert buffers.y == "gyrY"
    assert buffers.z == "gyrZ"


def test_detect_sensor_buffers_marks_incomplete_when_axis_missing() -> None:
    config = {
        "buffers": [
            {"name": "acc_time"},
            {"name": "temperature"},
        ]
    }

    buffers = detect_sensor_buffers(config, "accel")

    assert buffers.xyz_complete is False