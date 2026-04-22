# Copyright 2026 Nikolai Kolesnikov
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from typing import Optional

import requests

from app.sensors.phyphox_config import SensorBuffers


def normalize_url(url: str) -> str:
    url = url.strip()
    if not url:
        return "http://192.168.1.1:8080"
    if not url.startswith(("http://", "https://")):
        url = "http://" + url
    return url.rstrip("/")


class PhyphoxClient:
    def __init__(self, base_url: str, timeout: float = 2.0) -> None:
        self.base_url = normalize_url(base_url)
        self.session = requests.Session()
        self.timeout = timeout

    def get_root(self) -> None:
        response = self.session.get(f"{self.base_url}/", timeout=self.timeout)
        response.raise_for_status()

    def get_config(self) -> dict:
        response = self.session.get(f"{self.base_url}/config", timeout=self.timeout)
        response.raise_for_status()
        return response.json()

    def get_status(self) -> dict:
        response = self.session.get(f"{self.base_url}/get", timeout=self.timeout)
        response.raise_for_status()
        return response.json().get("status", {})

    def control(self, cmd: str) -> bool:
        response = self.session.get(f"{self.base_url}/control", params={"cmd": cmd}, timeout=self.timeout)
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, dict) and "result" in payload:
            return bool(payload["result"])
        return True

    def poll_since(self, sensor: SensorBuffers, last_time: Optional[float]) -> dict:
        if not sensor.xyz_complete:
            raise RuntimeError("Sensor buffers are incomplete")
        if sensor.time is None:
            params = {sensor.x: "full", sensor.y: "full", sensor.z: "full"}
        elif last_time is None:
            params = {sensor.time: "full", sensor.x: "full", sensor.y: "full", sensor.z: "full"}
        else:
            threshold = f"{last_time:.9f}"
            ref = f"{threshold}|{sensor.time}"
            params = {sensor.time: threshold, sensor.x: ref, sensor.y: ref, sensor.z: ref}
        response = self.session.get(f"{self.base_url}/get", params=params, timeout=self.timeout)
        response.raise_for_status()
        return response.json()