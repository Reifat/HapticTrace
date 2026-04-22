# Copyright 2026 Nikolai Kolesnikov
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from unittest.mock import Mock

from app.network import PhyphoxClient, normalize_url
from app.sensors import SensorBuffers


def test_normalize_url_adds_scheme_and_trims_slash() -> None:
    assert normalize_url("192.168.0.1:8080/") == "http://192.168.0.1:8080"
    assert normalize_url("") == "http://192.168.1.1:8080"


def test_control_returns_result_flag() -> None:
    client = PhyphoxClient("http://device")
    response = Mock()
    response.json.return_value = {"result": False}
    response.raise_for_status.return_value = None
    client.session = Mock()
    client.session.get.return_value = response

    result = client.control("start")

    assert result is False
    client.session.get.assert_called_once_with("http://device/control", params={"cmd": "start"}, timeout=2.0)


def test_poll_since_without_time_requests_full_xyz_buffers() -> None:
    client = PhyphoxClient("http://device")
    response = Mock()
    response.json.return_value = {"buffer": {}}
    response.raise_for_status.return_value = None
    client.session = Mock()
    client.session.get.return_value = response
    sensor = SensorBuffers(time=None, x="acc_x", y="acc_y", z="acc_z")

    client.poll_since(sensor, last_time=None)

    client.session.get.assert_called_once_with(
        "http://device/get",
        params={"acc_x": "full", "acc_y": "full", "acc_z": "full"},
        timeout=2.0,
    )


def test_poll_since_with_last_time_formats_incremental_reference() -> None:
    client = PhyphoxClient("http://device")
    response = Mock()
    response.json.return_value = {"buffer": {}}
    response.raise_for_status.return_value = None
    client.session = Mock()
    client.session.get.return_value = response
    sensor = SensorBuffers(time="acc_time", x="acc_x", y="acc_y", z="acc_z")

    client.poll_since(sensor, last_time=1.23456789)

    client.session.get.assert_called_once_with(
        "http://device/get",
        params={
            "acc_time": "1.234567890",
            "acc_x": "1.234567890|acc_time",
            "acc_y": "1.234567890|acc_time",
            "acc_z": "1.234567890|acc_time",
        },
        timeout=2.0,
    )


def test_get_status_returns_nested_status_payload() -> None:
    client = PhyphoxClient("http://device")
    response = Mock()
    response.json.return_value = {"status": {"measuring": True}}
    response.raise_for_status.return_value = None
    client.session = Mock()
    client.session.get.return_value = response

    status = client.get_status()

    assert status == {"measuring": True}