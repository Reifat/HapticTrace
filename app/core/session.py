# Copyright 2026 Nikolai Kolesnikov
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

from .parsing import parse_optional_float


@dataclass
class RecordingSession:
    session_id: str
    start_wall_ts: float
    sensor_start_request_ts: Optional[float] = None
    video_start_request_ts: Optional[float] = None
    sensor_stop_request_ts: Optional[float] = None
    video_stop_request_ts: Optional[float] = None
    first_sensor_sample_time_s: Optional[float] = None
    first_sensor_receive_wall_ts: Optional[float] = None
    first_video_frame_wall_ts: Optional[float] = None
    video_clip_id: Optional[str] = None
    state: str = "recording"

    def to_dict(self) -> Dict[str, Any]:
        offset_sensor = None
        offset_video = None
        offset_between = None
        if self.first_sensor_receive_wall_ts is not None:
            offset_sensor = self.first_sensor_receive_wall_ts - self.start_wall_ts
        if self.first_video_frame_wall_ts is not None:
            offset_video = self.first_video_frame_wall_ts - self.start_wall_ts
        if self.first_sensor_receive_wall_ts is not None and self.first_video_frame_wall_ts is not None:
            offset_between = self.first_video_frame_wall_ts - self.first_sensor_receive_wall_ts
        return {
            "session_id": self.session_id,
            "state": self.state,
            "start_wall_ts": self.start_wall_ts,
            "sensor_start_request_ts": self.sensor_start_request_ts,
            "video_start_request_ts": self.video_start_request_ts,
            "sensor_stop_request_ts": self.sensor_stop_request_ts,
            "video_stop_request_ts": self.video_stop_request_ts,
            "first_sensor_sample_time_s": self.first_sensor_sample_time_s,
            "first_sensor_receive_wall_ts": self.first_sensor_receive_wall_ts,
            "first_video_frame_wall_ts": self.first_video_frame_wall_ts,
            "video_clip_id": self.video_clip_id,
            "offset_sensor_receive_from_session_start_s": offset_sensor,
            "offset_video_first_frame_from_session_start_s": offset_video,
            "offset_video_minus_sensor_receive_s": offset_between,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RecordingSession":
        return cls(
            session_id=str(data.get("session_id") or ""),
            start_wall_ts=float(data.get("start_wall_ts") or time.time()),
            sensor_start_request_ts=parse_optional_float(data.get("sensor_start_request_ts")),
            video_start_request_ts=parse_optional_float(data.get("video_start_request_ts")),
            sensor_stop_request_ts=parse_optional_float(data.get("sensor_stop_request_ts")),
            video_stop_request_ts=parse_optional_float(data.get("video_stop_request_ts")),
            first_sensor_sample_time_s=parse_optional_float(data.get("first_sensor_sample_time_s")),
            first_sensor_receive_wall_ts=parse_optional_float(data.get("first_sensor_receive_wall_ts")),
            first_video_frame_wall_ts=parse_optional_float(data.get("first_video_frame_wall_ts")),
            video_clip_id=str(data.get("video_clip_id")) if data.get("video_clip_id") is not None else None,
            state=str(data.get("state") or "stopped"),
        )


class UnifiedSessionController:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.active_session: Optional[RecordingSession] = None
        self.finished_sessions: List[RecordingSession] = []

    def start_session(self) -> RecordingSession:
        with self.lock:
            if self.active_session is not None and self.active_session.state == "recording":
                raise RuntimeError("Recording session is already active")
            session = RecordingSession(
                session_id=datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6],
                start_wall_ts=time.time(),
            )
            self.active_session = session
            return session

    def mark_video_start_request(self, wall_ts: float, clip_id: str) -> None:
        with self.lock:
            if self.active_session is None:
                return
            self.active_session.video_start_request_ts = wall_ts
            self.active_session.video_clip_id = clip_id

    def mark_sensor_start_request(self, wall_ts: float) -> None:
        with self.lock:
            if self.active_session is None:
                return
            self.active_session.sensor_start_request_ts = wall_ts

    def mark_video_stop_request(self, wall_ts: float) -> None:
        with self.lock:
            if self.active_session is None:
                return
            self.active_session.video_stop_request_ts = wall_ts

    def mark_sensor_stop_request(self, wall_ts: float) -> None:
        with self.lock:
            if self.active_session is None:
                return
            self.active_session.sensor_stop_request_ts = wall_ts

    def mark_sensor_sample(self, sensor_sample_time_s: float) -> None:
        with self.lock:
            if self.active_session is None:
                return
            if self.active_session.first_sensor_sample_time_s is None:
                self.active_session.first_sensor_sample_time_s = sensor_sample_time_s
                self.active_session.first_sensor_receive_wall_ts = time.time()

    def mark_video_frame(self, frame_wall_ts: float) -> None:
        with self.lock:
            if self.active_session is None:
                return
            if self.active_session.first_video_frame_wall_ts is None:
                self.active_session.first_video_frame_wall_ts = frame_wall_ts

    def finish_session(self) -> Optional[RecordingSession]:
        with self.lock:
            if self.active_session is None:
                return None
            self.active_session.state = "stopped"
            finished = self.active_session
            self.finished_sessions.append(finished)
            self.active_session = None
            return finished

    def clear(self) -> None:
        with self.lock:
            self.active_session = None
            self.finished_sessions = []

    def export_metadata(self) -> Dict[str, Any]:
        with self.lock:
            return {
                "active_session": None if self.active_session is None else self.active_session.to_dict(),
                "finished_sessions": [session.to_dict() for session in self.finished_sessions],
            }

    def load_metadata(self, payload: Dict[str, Any]) -> None:
        active_payload = payload.get("active_session") if isinstance(payload, dict) else None
        finished_payload = payload.get("finished_sessions") if isinstance(payload, dict) else []
        with self.lock:
            self.active_session = RecordingSession.from_dict(active_payload) if isinstance(active_payload, dict) else None
            self.finished_sessions = [
                RecordingSession.from_dict(item)
                for item in finished_payload
                if isinstance(item, dict)
            ]