# Copyright 2026 Nikolai Kolesnikov
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import pytest

from app.core.session import RecordingSession, UnifiedSessionController


def test_recording_session_roundtrip_preserves_fields() -> None:
    session = RecordingSession(
        session_id="session_1",
        start_wall_ts=10.0,
        sensor_start_request_ts=11.0,
        video_start_request_ts=12.0,
        sensor_stop_request_ts=20.0,
        video_stop_request_ts=21.0,
        first_sensor_sample_time_s=0.25,
        first_sensor_receive_wall_ts=10.5,
        first_video_frame_wall_ts=10.8,
        video_clip_id="clip123",
        state="stopped",
    )

    restored = RecordingSession.from_dict(session.to_dict())

    assert restored == session


def test_session_controller_tracks_first_events_and_roundtrips_metadata() -> None:
    controller = UnifiedSessionController()
    controller.start_session()
    controller.mark_sensor_start_request(1.0)
    controller.mark_video_start_request(2.0, "clip_a")
    controller.mark_sensor_sample(0.5)
    controller.mark_sensor_sample(0.9)
    controller.mark_video_frame(3.0)
    controller.mark_video_frame(4.0)
    controller.mark_sensor_stop_request(5.0)
    controller.mark_video_stop_request(6.0)
    finished = controller.finish_session()

    assert finished is not None
    assert finished.first_sensor_sample_time_s == 0.5
    assert finished.first_video_frame_wall_ts == 3.0
    assert finished.video_clip_id == "clip_a"

    payload = controller.export_metadata()
    restored = UnifiedSessionController()
    restored.load_metadata(payload)

    assert restored.active_session is None
    assert len(restored.finished_sessions) == 1
    assert restored.finished_sessions[0].video_clip_id == "clip_a"
    assert restored.finished_sessions[0].first_sensor_sample_time_s == 0.5


def test_start_session_while_recording_raises() -> None:
    controller = UnifiedSessionController()
    controller.start_session()

    with pytest.raises(RuntimeError):
        controller.start_session()