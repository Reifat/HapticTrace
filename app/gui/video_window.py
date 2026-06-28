# Copyright 2026 Nikolai Kolesnikov
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import threading
import time
import logging
from dataclasses import dataclass
from queue import Empty
from typing import Any, Dict, Optional, Tuple

import numpy as np
import tkinter as tk
from PIL import Image, ImageTk
from tkinter import messagebox, ttk

from app.core.parsing import parse_optional_float
from app.core.session import RecordingSession, UnifiedSessionController
from app.gui.models import PlaybackState
from app.platform.capture import AVFoundationPlaybackVideo, IphoneCaptureService, VideoClipMetadata, read_video_asset_info

logger = logging.getLogger(__name__)


@dataclass
class PlaybackDecodeRequest:
    request_id: int
    generation: int
    clip_path: str
    frame_index: int
    safe_video_time_s: float
    view_size: Tuple[int, int]
    exact_timing: bool
    fps_hint: float
    prefer_speed: bool


@dataclass
class PlaybackDecodeResult:
    request_id: int
    generation: int
    clip_path: str
    frame_index: int
    view_size: Tuple[int, int]
    prefer_speed: bool
    frame_bgra: Optional[np.ndarray] = None
    error: Optional[Exception] = None


class VideoWindowController:
    SPEED_OPTIONS = [0.1, 0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0, 4.0]
    STATE_LIVE_PREVIEW = "live_preview"
    STATE_PLAYBACK_PAUSED = "playback_paused"
    STATE_PLAYBACK_PLAYING = "playback_playing"

    def __init__(
        self,
        root: tk.Tk,
        capture_service: IphoneCaptureService,
        session_controller: UnifiedSessionController,
        on_cursor_changed,
    ) -> None:
        self.root = root
        self.capture = capture_service
        self.session_controller = session_controller
        self.on_cursor_changed = on_cursor_changed
        self.state = PlaybackState()
        self.preview_image = None
        self.preview_image_size: Tuple[int, int] = (0, 0)
        self.preview_image_fast_path: Optional[bool] = None
        self.preview_canvas = None
        self.preview_image_item = None
        self.preview_text_item = None
        self.mode = "live"
        self.view_state = self.STATE_LIVE_PREVIEW
        self.state_generation = 0
        self.closed = False
        self.scale_update_locked = False
        self.offset_window = None
        self.playback_path: Optional[str] = None
        self.playback_fps = 30.0
        self.playback_frame_count = 0
        self.playback_duration_s = 0.0
        self.playback_last_requested_signature: Optional[Tuple[str, int, Tuple[int, int], bool, bool]] = None
        self.last_preview_present_mono = 0.0
        self.preview_min_interval_s = 0.0
        self.live_preview_fps = 0.0
        self.live_preview_frames_presented = 0
        self.live_preview_fps_start_mono: Optional[float] = None
        self.live_preview_render_ema_ms = 0.0
        self.live_preview_prefer_speed = False
        self.live_preview_quality_switch_mono = 0.0
        self.last_rendered_frame_index: Optional[int] = None
        self.last_rendered_view_size: Tuple[int, int] = (0, 0)
        self.cursor_notify_after_id = None
        self.cursor_notify_generation: Optional[int] = None
        self.pending_cursor_notify_time: Optional[float] = None
        self.playback_render_after_id = None
        self.playback_render_generation: Optional[int] = None
        self.playback_render_dirty = False
        self.playback_tick_after_id = None
        self.playback_tick_generation: Optional[int] = None
        self.playback_decode_request_inflight = False
        self.timeline_drag_active = False
        self.last_timeline_bounds: Optional[Tuple[float, float]] = None
        self.resume_playback_after_speed_change = False
        self.playback_decode_condition = threading.Condition()
        self.playback_decode_stop = False
        self.playback_decode_request_serial = 0
        self.playback_decode_latest_request_id = 0
        self.playback_decode_last_consumed_request_id = 0
        self.playback_decode_request: Optional[PlaybackDecodeRequest] = None
        self.playback_decode_result_lock = threading.Lock()
        self.playback_decode_result: Optional[PlaybackDecodeResult] = None
        self.playback_decode_thread: Optional[threading.Thread] = None

        self.window = tk.Toplevel(self.root)
        self.window.title("Video Playback")
        self.window.geometry("640x720")
        self.window.minsize(420, 320)
        self.window.attributes("-topmost", True)
        self.window.protocol("WM_DELETE_WINDOW", self._minimize_window)

        self.mode_var = tk.StringVar(value="Video mode: live preview")
        self.timeline_var = tk.StringVar(value="Time: -- / --")
        self.speed_var = tk.StringVar(value="1x")
        self.offset_summary_var = tk.StringVar(value="Offset: auto 0.000s | manual 0.000s | effective 0.000s")
        self.manual_offset_var = tk.DoubleVar(value=0.0)
        self.offset_range_min_var = tk.DoubleVar(value=-1.0)
        self.offset_range_max_var = tk.DoubleVar(value=1.0)

        self._build_ui()
        self._set_controls_enabled(False)
        self._ensure_playback_decode_worker()
        self._poll_window()

    def _build_ui(self) -> None:
        top = ttk.Frame(self.window, padding=8)
        top.pack(side=tk.TOP, fill=tk.X)
        ttk.Label(top, textvariable=self.mode_var).pack(anchor="w")
        ttk.Label(top, textvariable=self.offset_summary_var).pack(anchor="w", pady=(4, 0))

        self.preview_frame = tk.Frame(self.window, bd=1, relief=tk.SUNKEN, bg="black")
        self.preview_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))
        self.preview_canvas = tk.Canvas(self.preview_frame, bg="black", highlightthickness=0, bd=0, cursor="arrow")
        self.preview_canvas.pack(fill=tk.BOTH, expand=True)
        self.preview_image_item = self.preview_canvas.create_image(0, 0, anchor="nw")
        self.preview_text_item = self.preview_canvas.create_text(0, 0, text="Ожидание видео...", fill="white")
        self.preview_canvas.bind("<Configure>", self._on_preview_canvas_configure)

        controls = ttk.Frame(self.window, padding=(8, 0, 8, 8))
        controls.pack(side=tk.TOP, fill=tk.X)
        self.play_pause_btn = ttk.Button(controls, text="Play", command=self.toggle_playback)
        self.restart_btn = ttk.Button(controls, text="Restart", command=self.restart_playback)
        self.step_back_btn = ttk.Button(controls, text="Step -", command=lambda: self.step_playback(-1))
        self.step_forward_btn = ttk.Button(controls, text="Step +", command=lambda: self.step_playback(1))
        self.offset_btn = ttk.Button(controls, text="Offset Settings", command=self.open_offset_window)
        self.show_btn = ttk.Button(controls, text="Raise Window", command=self.show)
        self.play_pause_btn.pack(side=tk.LEFT)
        self.restart_btn.pack(side=tk.LEFT, padx=(4, 0))
        self.step_back_btn.pack(side=tk.LEFT, padx=(12, 0))
        self.step_forward_btn.pack(side=tk.LEFT, padx=(4, 0))
        self.offset_btn.pack(side=tk.LEFT, padx=(12, 0))
        self.show_btn.pack(side=tk.RIGHT)

        speed_box = ttk.Frame(self.window, padding=(8, 0, 8, 8))
        speed_box.pack(side=tk.TOP, fill=tk.X)
        ttk.Label(speed_box, text="Speed:").pack(side=tk.LEFT)
        self.speed_combo = ttk.Combobox(
            speed_box,
            textvariable=self.speed_var,
            values=[self._format_speed_label(value) for value in self.SPEED_OPTIONS],
            state="readonly",
            width=8,
        )
        self.speed_combo.pack(side=tk.LEFT, padx=(6, 0))
        self.speed_combo.bind("<ButtonPress-1>", self._on_speed_combo_press, add="+")
        self.speed_combo.bind("<<ComboboxSelected>>", self._on_speed_selected)
        ttk.Label(speed_box, textvariable=self.timeline_var).pack(side=tk.RIGHT)

        timeline = ttk.Frame(self.window, padding=(8, 0, 8, 8))
        timeline.pack(side=tk.TOP, fill=tk.X)
        self.timeline_scale = ttk.Scale(
            timeline,
            from_=0.0,
            to=1.0,
            command=self._on_scale_changed,
        )
        self.timeline_scale.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.timeline_scale.bind("<ButtonPress-1>", self._on_timeline_press)
        self.timeline_scale.bind("<ButtonRelease-1>", self._on_timeline_release)

        self.window.bind("<Left>", self._on_left_key)
        self.window.bind("<Right>", self._on_right_key)
        self.window.bind("<space>", self._on_space_key)

    def _minimize_window(self) -> None:
        if self.window.winfo_exists():
            self.window.iconify()

    def show(self) -> None:
        if not self.window.winfo_exists():
            return
        self.window.deiconify()
        self.window.lift()

    def hide(self) -> None:
        if not self.window.winfo_exists():
            return
        self.resume_playback_after_speed_change = False
        self._stop_playback_loop()
        self.window.withdraw()

    def close(self) -> None:
        self.closed = True
        self.resume_playback_after_speed_change = False
        self._cancel_pending_cursor_notification()
        self._cancel_pending_playback_render()
        self._cancel_playback_tick()
        self._stop_playback_decode_worker()
        self._stop_playback_loop()
        self._release_playback_video()
        if self.offset_window is not None:
            try:
                if self.offset_window.winfo_exists():
                    self.offset_window.destroy()
            except Exception:
                pass
        if self.window.winfo_exists():
            self.window.destroy()

    def is_live_preview_state(self) -> bool:
        return self.view_state == self.STATE_LIVE_PREVIEW

    def is_playback_mode(self) -> bool:
        return (
            self.view_state in (self.STATE_PLAYBACK_PAUSED, self.STATE_PLAYBACK_PLAYING)
            and self.state.cursor_time_s is not None
            and self.capture.has_finished_recording()
        )

    def is_playback_playing_state(self) -> bool:
        return self.view_state == self.STATE_PLAYBACK_PLAYING and self.is_playback_mode()

    def _render_context_for_state(self, view_state: Optional[str] = None) -> str:
        state = self.view_state if view_state is None else view_state
        if state == self.STATE_LIVE_PREVIEW:
            return "live"
        if state in (self.STATE_PLAYBACK_PAUSED, self.STATE_PLAYBACK_PLAYING):
            return "playback"
        return "none"

    def _reset_render_cache(self) -> None:
        self.preview_image = None
        self.preview_image_size = (0, 0)
        self.preview_image_fast_path = None
        self.last_rendered_frame_index = None
        self.last_rendered_view_size = (0, 0)

    def _reset_live_preview_stats(self) -> None:
        self.live_preview_fps = 0.0
        self.live_preview_frames_presented = 0
        self.live_preview_fps_start_mono = None
        self.live_preview_render_ema_ms = 0.0
        self.live_preview_prefer_speed = False
        self.live_preview_quality_switch_mono = 0.0

    def _ensure_playback_decode_worker(self) -> None:
        if self.playback_decode_thread is not None and self.playback_decode_thread.is_alive():
            return
        self.playback_decode_stop = False
        self.playback_decode_thread = threading.Thread(
            target=self._playback_decode_worker_loop,
            name="video-playback-decode",
            daemon=True,
        )
        self.playback_decode_thread.start()

    def _stop_playback_decode_worker(self) -> None:
        with self.playback_decode_condition:
            self.playback_decode_stop = True
            self.playback_decode_request = None
            self.playback_decode_condition.notify_all()

    def _clear_pending_playback_decode_result(self) -> None:
        with self.playback_decode_result_lock:
            self.playback_decode_result = None

    def _invalidate_playback_decode_requests(self) -> None:
        with self.playback_decode_condition:
            self.playback_decode_request_serial += 1
            self.playback_decode_latest_request_id = self.playback_decode_request_serial
            self.playback_decode_last_consumed_request_id = self.playback_decode_request_serial
            self.playback_decode_request = None
            self.playback_decode_condition.notify_all()
        self.playback_last_requested_signature = None
        self.playback_decode_request_inflight = False
        self.playback_render_dirty = False
        self._clear_pending_playback_decode_result()

    def _enqueue_playback_decode_request(
        self,
        clip_path: str,
        frame_index: int,
        safe_video_time_s: float,
        view_size: Tuple[int, int],
        exact_timing: bool,
        fps_hint: float,
        prefer_speed: bool,
    ) -> None:
        self._ensure_playback_decode_worker()
        with self.playback_decode_condition:
            self.playback_decode_request_serial += 1
            request_id = self.playback_decode_request_serial
            request = PlaybackDecodeRequest(
                request_id=request_id,
                generation=self.state_generation,
                clip_path=clip_path,
                frame_index=frame_index,
                safe_video_time_s=safe_video_time_s,
                view_size=view_size,
                exact_timing=exact_timing,
                fps_hint=fps_hint,
                prefer_speed=prefer_speed,
            )
            self.playback_decode_latest_request_id = request_id
            self.playback_decode_request = request
            self.playback_decode_condition.notify_all()

    def _consume_playback_decode_result(self) -> None:
        result = None
        with self.playback_decode_result_lock:
            if self.playback_decode_result is not None:
                result = self.playback_decode_result
                self.playback_decode_result = None
        if result is None:
            return
        if self.playback_decode_request_inflight:
            self.playback_decode_request_inflight = False
        if result.generation != self.state_generation:
            return
        if not self.is_playback_mode():
            return
        if self.playback_path is not None and result.clip_path != self.playback_path:
            return
        if result.request_id <= self.playback_decode_last_consumed_request_id:
            return
        self.playback_decode_last_consumed_request_id = result.request_id
        if result.error is not None:
            self.playback_last_requested_signature = None
            logger.exception("Failed to decode playback video frame", exc_info=result.error)
            self.capture.shared_state.report_error(result.error)
            self._stop_playback_loop()
            self._show_preview_text("Не удалось декодировать видео")
            return
        if result.frame_bgra is None:
            return
        self.last_rendered_frame_index = result.frame_index
        self._render_frame(result.frame_bgra, prefer_speed=result.prefer_speed)
        if self.is_playback_playing_state() and self.playback_render_dirty:
            self.playback_render_dirty = False
            self._request_playback_render()

    def _playback_decode_worker_loop(self) -> None:
        capture: Optional[AVFoundationPlaybackVideo] = None
        capture_path: Optional[str] = None
        last_handled_request_id = 0
        while True:
            with self.playback_decode_condition:
                while (
                    not self.playback_decode_stop
                    and (
                        self.playback_decode_request is None
                        or self.playback_decode_request.request_id == last_handled_request_id
                    )
                ):
                    self.playback_decode_condition.wait()
                if self.playback_decode_stop:
                    break
                request = self.playback_decode_request
                if request is None:
                    continue
                last_handled_request_id = request.request_id
            try:
                if capture is None or capture_path != request.clip_path:
                    if capture is not None:
                        capture.close()
                    capture = AVFoundationPlaybackVideo(request.clip_path)
                    capture_path = request.clip_path
                capture.configure_frame_extraction(
                    max_size=request.view_size,
                    exact_timing=request.exact_timing,
                    fps_hint=request.fps_hint,
                )
                frame_bgra = capture.read_frame_at_time(request.safe_video_time_s)
                result = PlaybackDecodeResult(
                    request_id=request.request_id,
                    generation=request.generation,
                    clip_path=request.clip_path,
                    frame_index=request.frame_index,
                    view_size=request.view_size,
                    prefer_speed=request.prefer_speed,
                    frame_bgra=frame_bgra,
                )
            except Exception as exc:
                result = PlaybackDecodeResult(
                    request_id=request.request_id,
                    generation=request.generation,
                    clip_path=request.clip_path,
                    frame_index=request.frame_index,
                    view_size=request.view_size,
                    prefer_speed=request.prefer_speed,
                    error=exc,
                )
            with self.playback_decode_condition:
                if self.playback_decode_stop:
                    break
                latest_request = self.playback_decode_request
                is_stale = latest_request is None or latest_request.request_id != request.request_id
                if is_stale and result.error is not None:
                    continue
            with self.playback_decode_result_lock:
                self.playback_decode_result = result
        if capture is not None:
            try:
                capture.close()
            except Exception:
                pass

    def _transition_view_state(self, next_state: str) -> None:
        previous_render_context = self._render_context_for_state()
        next_render_context = self._render_context_for_state(next_state)
        self.state_generation += 1
        self.view_state = next_state
        self.mode = "live" if next_state == self.STATE_LIVE_PREVIEW else "playback"
        self.state.is_playing = next_state == self.STATE_PLAYBACK_PLAYING
        if not self.state.is_playing:
            self.state.last_tick_mono = None
        self.timeline_drag_active = False
        self._cancel_pending_cursor_notification()
        self._cancel_pending_playback_render()
        self._cancel_playback_tick()
        self._invalidate_playback_decode_requests()
        if previous_render_context != next_render_context:
            self._reset_render_cache()

    def set_capture_info(self, capture_status: str, _device_status: str) -> None:
        if self.closed or not self.is_live_preview_state():
            return
        if self.capture.shared_state.last_frame_shape is not None:
            height, width = self.capture.shared_state.last_frame_shape[:2]
            self.mode_var.set(
                f"Video mode: live preview | {width}x{height} | "
                f"capture {self.capture.shared_state.last_fps:.1f} fps | "
                f"preview {self.live_preview_fps:.1f} fps"
            )
            return
        self.mode_var.set(f"Video mode: live preview | {capture_status}")
        if self.capture.connecting:
            self._show_preview_text_if_changed("Connecting to iPhone...")
        elif self.capture.connected:
            self._show_preview_text_if_changed(
                "Connected. Waiting for video frames...\n"
                "If this does not change, check Camera permission for HapticTrace."
            )
        elif "error:" in capture_status:
            self._show_preview_text_if_changed(capture_status)
        else:
            self._show_preview_text_if_changed("Ожидание live preview...")

    def activate_live_mode(self) -> None:
        self._transition_view_state(self.STATE_LIVE_PREVIEW)
        self._reset_live_preview_stats()
        self.state.cursor_time_s = None
        self.state.duration_s = 0.0
        self.state.visible_start_s = 0.0
        self.state.visible_end_s = 0.0
        self.state.last_tick_mono = None
        self.mode_var.set("Video mode: live preview")
        self._release_playback_video()
        self._set_controls_enabled(False)
        self._update_timeline_label()
        self._show_preview_text("Ожидание live preview...")

    def activate_playback_mode(self, duration_s: float) -> None:
        self._transition_view_state(self.STATE_PLAYBACK_PAUSED)
        self.state.duration_s = max(duration_s, 0.0)
        self.state.visible_start_s = 0.0
        self.state.visible_end_s = self.state.duration_s
        self.state.last_tick_mono = None
        self.play_pause_btn.config(text="Play")
        self._update_auto_offset()
        if self.state.cursor_time_s is None:
            self.state.cursor_time_s = 0.0
        self.mode_var.set("Video mode: recorded playback")
        self._set_controls_enabled(True)
        self._set_cursor_local(self.state.cursor_time_s)

    def reset_offset_state(self) -> None:
        self.state.manual_offset_s = 0.0
        self.manual_offset_var.set(0.0)
        self._update_auto_offset()
        self._refresh_offset_summary()
        if self.is_playback_mode():
            self._request_playback_render()

    def export_settings(self) -> Dict[str, Any]:
        return {
            "playback_speed": self.state.selected_speed,
            "auto_offset_s": self.state.auto_offset_s,
            "manual_offset_s": self.state.manual_offset_s,
            "effective_offset_s": self.get_effective_offset_s(),
        }

    def load_settings(self, settings: Dict[str, Any]) -> None:
        speed = float(settings.get("playback_speed") or 1.0)
        self.state.selected_speed = speed
        self.speed_var.set(self._format_speed_label(speed))
        manual_offset = parse_optional_float(settings.get("manual_offset_s"))
        self.manual_offset_var.set(manual_offset or 0.0)
        self.state.manual_offset_s = manual_offset or 0.0
        self._refresh_offset_summary()
        if self.is_playback_mode():
            self._request_playback_render()

    def sync_from_app(
        self,
        cursor_time_s: Optional[float],
        duration_s: float,
        visible_range_s: Optional[Tuple[float, float]] = None,
    ) -> None:
        self.state.duration_s = max(duration_s, 0.0)
        if visible_range_s is None:
            self.state.visible_start_s = 0.0
            self.state.visible_end_s = self.state.duration_s
        else:
            visible_start_s = min(max(float(visible_range_s[0]), 0.0), self.state.duration_s)
            visible_end_s = min(max(float(visible_range_s[1]), visible_start_s), self.state.duration_s)
            if visible_end_s <= visible_start_s:
                visible_end_s = min(self.state.duration_s, visible_start_s + 0.001)
            self.state.visible_start_s = visible_start_s
            self.state.visible_end_s = visible_end_s
        if cursor_time_s is None:
            self.state.cursor_time_s = None
            self._invalidate_playback_decode_requests()
            self._update_timeline_label()
            return
        if self.timeline_drag_active:
            start_s, end_s = self._get_timeline_bounds()
            local_cursor = self.state.cursor_time_s if self.state.cursor_time_s is not None else cursor_time_s
            self.state.cursor_time_s = min(max(local_cursor, start_s), end_s)
            self._sync_timeline_scale_widget(start_s, end_s, self.state.cursor_time_s, update_value=False)
            self._update_timeline_label()
            return
        self._set_cursor_local(cursor_time_s)

    def toggle_playback(self) -> None:
        if not self.is_playback_mode():
            return
        if self.is_playback_playing_state():
            self._transition_view_state(self.STATE_PLAYBACK_PAUSED)
            self.play_pause_btn.config(text="Play")
            self._request_playback_render()
            if self.state.cursor_time_s is not None:
                self._dispatch_cursor_change(self.state.cursor_time_s, throttle=False)
        else:
            self._transition_view_state(self.STATE_PLAYBACK_PLAYING)
            self.state.last_tick_mono = time.monotonic()
            self.play_pause_btn.config(text="Pause")
            self._request_playback_render()
            self._schedule_playback_tick()

    def restart_playback(self) -> None:
        if not self.is_playback_mode():
            return
        if self.is_playback_playing_state():
            self._transition_view_state(self.STATE_PLAYBACK_PAUSED)
            self.play_pause_btn.config(text="Play")
        self._set_cursor_local(self.state.visible_start_s)
        self._notify_cursor_change(self.state.visible_start_s)

    def step_playback(self, direction: int) -> None:
        if not self.is_playback_mode():
            return
        if self.is_playback_playing_state():
            self._transition_view_state(self.STATE_PLAYBACK_PAUSED)
            self.play_pause_btn.config(text="Play")
        step = self._get_step_size() * direction
        current = self.state.cursor_time_s or 0.0
        self._set_cursor_local(current + step)
        if self.state.cursor_time_s is not None:
            self._notify_cursor_change(self.state.cursor_time_s)

    def _on_left_key(self, event) -> None:
        self.step_playback(-1)

    def _on_right_key(self, event) -> None:
        self.step_playback(1)

    def _on_space_key(self, event) -> None:
        self.toggle_playback()

    def _pause_playback_for_speed_change(self) -> None:
        if not self.is_playback_playing_state():
            return
        self._transition_view_state(self.STATE_PLAYBACK_PAUSED)
        self.play_pause_btn.config(text="Play")
        self._request_playback_render()
        if self.state.cursor_time_s is not None:
            self._dispatch_cursor_change(self.state.cursor_time_s, throttle=False)

    def _resume_playback_from_speed_change(self) -> None:
        if not self.resume_playback_after_speed_change:
            return
        self.resume_playback_after_speed_change = False
        if not self.is_playback_mode() or self.is_playback_playing_state():
            return
        self._transition_view_state(self.STATE_PLAYBACK_PLAYING)
        self.state.last_tick_mono = time.monotonic()
        self.play_pause_btn.config(text="Pause")
        self._request_playback_render()
        self._schedule_playback_tick()

    def _on_speed_combo_press(self, event) -> None:
        if str(self.speed_combo.cget("state")) == "disabled":
            return
        self.resume_playback_after_speed_change = self.is_playback_playing_state()
        if self.resume_playback_after_speed_change:
            self._pause_playback_for_speed_change()

    def _on_speed_selected(self, event) -> None:
        label = self.speed_var.get()
        self.state.selected_speed = self._parse_speed_label(label)
        if self.resume_playback_after_speed_change:
            self.window.after_idle(self._resume_playback_from_speed_change)

    def _format_speed_label(self, speed: float) -> str:
        return f"{speed:g}x"

    def _parse_speed_label(self, label: str) -> float:
        try:
            return max(float(label.rstrip("x")), 0.01)
        except Exception:
            return 1.0

    def _on_scale_changed(self, value: str) -> None:
        if self.scale_update_locked or not self.is_playback_mode():
            return
        try:
            cursor_time_s = float(value)
        except (TypeError, ValueError):
            return
        self._set_cursor_local(cursor_time_s, update_scale=False)
        if self.state.cursor_time_s is not None:
            self._notify_cursor_change(self.state.cursor_time_s, throttle=True)

    def _on_timeline_press(self, event) -> None:
        if self.is_playback_playing_state():
            self._transition_view_state(self.STATE_PLAYBACK_PAUSED)
            self.play_pause_btn.config(text="Play")
        self.timeline_drag_active = True
        self._cancel_pending_cursor_notification()

    def _on_timeline_release(self, event) -> None:
        if not self.timeline_drag_active:
            return
        self.timeline_drag_active = False
        self._cancel_pending_cursor_notification()
        if self.state.cursor_time_s is not None:
            self._notify_cursor_change(self.state.cursor_time_s)

    def _set_controls_enabled(self, enabled: bool) -> None:
        state = ["!disabled"] if enabled else ["disabled"]
        self.play_pause_btn.state(state)
        self.restart_btn.state(state)
        self.step_back_btn.state(state)
        self.step_forward_btn.state(state)
        self.offset_btn.state(state)
        self.speed_combo.configure(state="readonly" if enabled else "disabled")
        self.timeline_scale.state(state)
        if not enabled:
            self.resume_playback_after_speed_change = False
            self.play_pause_btn.config(text="Play")

    def _update_timeline_label(self) -> None:
        if self.state.cursor_time_s is None:
            self.timeline_var.set("Time: -- / --")
            return
        self.timeline_var.set(
            f"Time: {self.state.cursor_time_s:.3f}s / {self.state.duration_s:.3f}s | View: {self.state.visible_start_s:.3f}-{self.state.visible_end_s:.3f}s"
        )

    def _notify_cursor_change(self, cursor_time_s: float, throttle: bool = False) -> None:
        self._dispatch_cursor_change(cursor_time_s, throttle=throttle)

    def _get_cursor_notify_delay_ms(self) -> int:
        if self.is_playback_playing_state():
            return 1
        return 16

    def _dispatch_cursor_change(self, cursor_time_s: float, throttle: bool) -> None:
        if throttle and self.is_playback_playing_state():
            throttle = False
        if not throttle:
            self._cancel_pending_cursor_notification()
            self.on_cursor_changed(cursor_time_s, "video_window")
            return
        self.pending_cursor_notify_time = cursor_time_s
        if self.cursor_notify_after_id is None:
            generation = self.state_generation
            self.cursor_notify_generation = generation
            self.cursor_notify_after_id = self.window.after(
                self._get_cursor_notify_delay_ms(),
                lambda gen=generation: self._flush_pending_cursor_notification(gen),
            )

    def _flush_pending_cursor_notification(self, generation: int) -> None:
        if generation != self.cursor_notify_generation or generation != self.state_generation:
            return
        self.cursor_notify_after_id = None
        self.cursor_notify_generation = None
        if self.pending_cursor_notify_time is None:
            return
        cursor_time_s = self.pending_cursor_notify_time
        self.pending_cursor_notify_time = None
        self.on_cursor_changed(cursor_time_s, "video_window")

    def _cancel_pending_cursor_notification(self) -> None:
        if self.cursor_notify_after_id is not None:
            try:
                self.window.after_cancel(self.cursor_notify_after_id)
            except Exception:
                pass
            self.cursor_notify_after_id = None
        self.cursor_notify_generation = None
        self.pending_cursor_notify_time = None

    def _cancel_pending_playback_render(self) -> None:
        if self.playback_render_after_id is not None:
            try:
                self.window.after_cancel(self.playback_render_after_id)
            except Exception:
                pass
            self.playback_render_after_id = None
        self.playback_render_generation = None
        self.playback_render_dirty = False

    def _get_playback_render_delay_ms(self) -> int:
        if self.is_playback_playing_state():
            return 0
        return 0

    def _cancel_playback_tick(self) -> None:
        if self.playback_tick_after_id is not None:
            try:
                self.window.after_cancel(self.playback_tick_after_id)
            except Exception:
                pass
            self.playback_tick_after_id = None
        self.playback_tick_generation = None

    def _request_playback_render(self) -> None:
        if self.closed or not self.is_playback_mode() or self.state.cursor_time_s is None:
            return
        if self.is_playback_playing_state() and self.playback_decode_request_inflight:
            self.playback_render_dirty = True
            return
        if self.playback_render_after_id is None:
            generation = self.state_generation
            self.playback_render_generation = generation
            if self.is_playback_playing_state():
                self._flush_pending_playback_render(generation)
            else:
                self.playback_render_after_id = self.window.after(
                    self._get_playback_render_delay_ms(),
                    lambda gen=generation: self._flush_pending_playback_render(gen),
                )

    def _flush_pending_playback_render(self, generation: int) -> None:
        if generation != self.playback_render_generation or generation != self.state_generation:
            return
        self.playback_render_after_id = None
        self.playback_render_generation = None
        if self.closed or not self.is_playback_mode() or self.state.cursor_time_s is None:
            return
        self._render_playback_frame()

    def _schedule_playback_tick(self, delay_ms: int = 1) -> None:
        if self.closed or not self.is_playback_playing_state():
            return
        if self.playback_tick_after_id is not None:
            return
        generation = self.state_generation
        self.playback_tick_generation = generation
        self.playback_tick_after_id = self.window.after(
            delay_ms,
            lambda gen=generation: self._playback_tick(gen),
        )

    def is_timeline_scrubbing(self) -> bool:
        return self.timeline_drag_active

    def _get_latest_recording_session(self) -> Optional[RecordingSession]:
        sessions = self.session_controller.finished_sessions
        if not sessions:
            return None
        return sessions[-1]

    def _get_latest_video_clip(self) -> Optional[VideoClipMetadata]:
        clips = self.capture.shared_state.recorder.finished_clips
        if not clips:
            return None
        return clips[-1]

    def _update_auto_offset(self) -> None:
        session = self._get_latest_recording_session()
        if session is None:
            self.state.auto_offset_s = 0.0
        elif session.first_sensor_receive_wall_ts is not None and session.first_video_frame_wall_ts is not None:
            self.state.auto_offset_s = session.first_video_frame_wall_ts - session.first_sensor_receive_wall_ts
        else:
            self.state.auto_offset_s = 0.0
        self._refresh_offset_summary()

    def get_effective_offset_s(self) -> float:
        return self.state.auto_offset_s + self.state.manual_offset_s

    def _refresh_offset_summary(self) -> None:
        self.offset_summary_var.set(
            f"Offset: auto {self.state.auto_offset_s:+.3f}s | manual {self.state.manual_offset_s:+.3f}s | effective {self.get_effective_offset_s():+.3f}s"
        )

    def open_offset_window(self) -> None:
        if self.offset_window is not None:
            try:
                if self.offset_window.winfo_exists():
                    self.offset_window.lift()
                    self.offset_window.focus_force()
                    return
            except Exception:
                pass
        win = tk.Toplevel(self.window)
        self.offset_window = win
        win.title("Video Offset Settings")
        win.geometry("420x260")
        win.transient(self.window)
        main = ttk.Frame(win, padding=12)
        main.pack(fill=tk.BOTH, expand=True)

        range_row = ttk.Frame(main)
        range_row.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(range_row, text="Range min, s").pack(side=tk.LEFT)
        ttk.Spinbox(
            range_row,
            from_=-10.0,
            to=10.0,
            increment=0.1,
            textvariable=self.offset_range_min_var,
            width=8,
            command=self._update_offset_slider_range,
        ).pack(side=tk.LEFT, padx=(6, 12))
        ttk.Label(range_row, text="Range max, s").pack(side=tk.LEFT)
        ttk.Spinbox(
            range_row,
            from_=-10.0,
            to=10.0,
            increment=0.1,
            textvariable=self.offset_range_max_var,
            width=8,
            command=self._update_offset_slider_range,
        ).pack(side=tk.LEFT, padx=(6, 0))

        ttk.Label(main, text="Manual offset slider").pack(anchor="w")
        self.offset_scale = ttk.Scale(main, from_=-1.0, to=1.0, variable=self.manual_offset_var, command=self._on_offset_slider_changed)
        self.offset_scale.pack(fill=tk.X, pady=(6, 8))

        buttons = ttk.Frame(main)
        buttons.pack(fill=tk.X, pady=(0, 8))
        ttk.Button(buttons, text="-50 ms", command=lambda: self._adjust_manual_offset(-0.05)).pack(side=tk.LEFT)
        ttk.Button(buttons, text="-10 ms", command=lambda: self._adjust_manual_offset(-0.01)).pack(side=tk.LEFT, padx=(4, 0))
        ttk.Button(buttons, text="+10 ms", command=lambda: self._adjust_manual_offset(0.01)).pack(side=tk.LEFT, padx=(12, 0))
        ttk.Button(buttons, text="+50 ms", command=lambda: self._adjust_manual_offset(0.05)).pack(side=tk.LEFT, padx=(4, 0))

        ttk.Label(main, textvariable=self.offset_summary_var).pack(anchor="w", pady=(0, 8))
        actions = ttk.Frame(main)
        actions.pack(fill=tk.X)
        ttk.Button(actions, text="Reset to Auto", command=self._reset_manual_offset).pack(side=tk.LEFT)

        self._update_offset_slider_range()

        def close_window() -> None:
            self.offset_window = None
            win.destroy()

        win.protocol("WM_DELETE_WINDOW", close_window)

    def _update_offset_slider_range(self) -> None:
        range_min = float(self.offset_range_min_var.get())
        range_max = float(self.offset_range_max_var.get())
        if range_min == range_max:
            range_max = range_min + 0.1
            self.offset_range_max_var.set(range_max)
        if range_min > range_max:
            range_min, range_max = range_max, range_min
            self.offset_range_min_var.set(range_min)
            self.offset_range_max_var.set(range_max)
        if hasattr(self, "offset_scale") and self.offset_scale is not None:
            self.offset_scale.configure(from_=range_min, to=range_max)
        manual = min(max(float(self.manual_offset_var.get()), range_min), range_max)
        self.manual_offset_var.set(manual)
        self._apply_manual_offset(live_update=False)

    def _adjust_manual_offset(self, delta_s: float) -> None:
        range_min = float(self.offset_range_min_var.get())
        range_max = float(self.offset_range_max_var.get())
        next_value = float(self.manual_offset_var.get()) + delta_s
        self.manual_offset_var.set(min(max(next_value, range_min), range_max))
        self._apply_manual_offset(live_update=True)

    def _on_offset_slider_changed(self, value: str) -> None:
        try:
            self.manual_offset_var.set(float(value))
        except (TypeError, ValueError):
            return
        self._apply_manual_offset(live_update=True)

    def _apply_manual_offset(self, live_update: bool = True) -> None:
        previous_effective_offset_s = self.get_effective_offset_s()
        current_video_time_s: Optional[float] = None
        if self.state.cursor_time_s is not None:
            current_video_time_s = max(0.0, self.state.cursor_time_s - previous_effective_offset_s)
        try:
            self.state.manual_offset_s = float(self.manual_offset_var.get())
        except Exception:
            self.state.manual_offset_s = 0.0
            self.manual_offset_var.set(0.0)
        self._refresh_offset_summary()
        if live_update and self.is_playback_mode() and current_video_time_s is not None:
            next_cursor_time_s = current_video_time_s + self.get_effective_offset_s()
            self._set_cursor_local(next_cursor_time_s)
            if self.state.cursor_time_s is not None:
                self._notify_cursor_change(self.state.cursor_time_s)

    def _reset_manual_offset(self) -> None:
        self.manual_offset_var.set(0.0)
        self._apply_manual_offset(live_update=True)

    def _get_step_size(self) -> float:
        if self.playback_fps > 0:
            return max(1.0 / self.playback_fps, 0.01)
        return 0.05

    def _get_timeline_bounds(self) -> Tuple[float, float]:
        if self.state.duration_s <= 0.0:
            return 0.0, 0.001
        start_s = min(max(self.state.visible_start_s, 0.0), self.state.duration_s)
        end_s = min(max(self.state.visible_end_s, start_s), self.state.duration_s)
        if end_s <= start_s:
            end_s = min(self.state.duration_s, start_s + 0.001)
        return start_s, max(end_s, start_s + 0.001)

    def _sync_timeline_scale_widget(
        self,
        start_s: float,
        end_s: float,
        cursor: float,
        update_value: bool = True,
    ) -> None:
        if self.timeline_scale is None:
            return
        bounds = (start_s, end_s)
        needs_reconfigure = self.last_timeline_bounds != bounds
        if not needs_reconfigure and not update_value:
            return
        self.scale_update_locked = True
        try:
            if needs_reconfigure:
                self.timeline_scale.configure(from_=start_s, to=end_s)
                self.last_timeline_bounds = bounds
            if update_value:
                self.timeline_scale.set(cursor)
        finally:
            self.scale_update_locked = False

    def _set_cursor_local(self, cursor_time_s: float, update_scale: bool = True) -> None:
        start_s, end_s = self._get_timeline_bounds()
        cursor = min(max(cursor_time_s, start_s), end_s)
        self.state.cursor_time_s = cursor
        self._sync_timeline_scale_widget(start_s, end_s, cursor, update_value=update_scale)
        self._update_timeline_label()
        if self.is_playback_mode():
            self._request_playback_render()

    def _open_playback_video(self) -> bool:
        clip = self._get_latest_video_clip()
        if clip is None:
            return False
        if self.playback_path == clip.temp_path:
            return True
        self._release_playback_video()
        try:
            info = read_video_asset_info(clip.temp_path)
        except Exception as exc:
            logger.exception("Failed to read playback video metadata")
            self.capture.shared_state.report_error(exc)
            return False
        self.playback_path = clip.temp_path
        fps_candidates = []
        if clip.frame_count > 0 and clip.duration_s is not None and clip.duration_s > 0.0:
            fps_candidates.append(clip.frame_count / clip.duration_s)
        if clip.fps_hint > 0.0:
            fps_candidates.append(clip.fps_hint)
        if info.fps > 0.0:
            fps_candidates.append(info.fps)
        bounded_fps_candidates = [value for value in fps_candidates if 0.0 < value <= 240.0]
        resolved_fps = max(bounded_fps_candidates or fps_candidates or [1.0])
        self.playback_fps = max(resolved_fps, 1.0)
        self.playback_frame_count = max(clip.frame_count, info.frame_count, 0)
        self.playback_duration_s = max(float(clip.duration_s or 0.0), float(info.duration_s or 0.0))
        self.playback_last_requested_signature = None
        return True

    def _release_playback_video(self) -> None:
        self._cancel_pending_playback_render()
        self._cancel_playback_tick()
        self._invalidate_playback_decode_requests()
        self.playback_path = None
        self.playback_fps = 30.0
        self.playback_frame_count = 0
        self.playback_duration_s = 0.0
        self.last_rendered_frame_index = None
        self.last_rendered_view_size = (0, 0)
        self.playback_last_requested_signature = None

    def _show_preview_text(self, text: str) -> None:
        if self.preview_canvas is None or self.preview_text_item is None or self.preview_image_item is None:
            return
        self.preview_canvas.itemconfigure(self.preview_image_item, image="")
        self.preview_canvas.itemconfigure(self.preview_text_item, text=text)
        self._reset_render_cache()
        self._update_preview_text_position()

    def _show_preview_text_if_changed(self, text: str) -> None:
        if self.preview_canvas is None or self.preview_text_item is None:
            return
        try:
            if self.preview_canvas.itemcget(self.preview_text_item, "text") == text:
                return
        except tk.TclError:
            return
        self._show_preview_text(text)

    def _update_preview_text_position(self) -> None:
        if self.preview_canvas is None or self.preview_text_item is None:
            return
        view_w = max(self.preview_canvas.winfo_width(), 100)
        view_h = max(self.preview_canvas.winfo_height(), 100)
        self.preview_canvas.coords(self.preview_text_item, view_w * 0.5, view_h * 0.5)

    def _on_preview_canvas_configure(self, event) -> None:
        self.last_rendered_frame_index = None
        self.last_rendered_view_size = (0, 0)
        self._update_preview_text_position()
        if self.is_playback_mode() and self.state.cursor_time_s is not None:
            self._request_playback_render()

    def _resize_for_preview(
        self,
        frame_image: Image.Image,
        width: int,
        height: int,
        view_w: int,
        view_h: int,
        high_quality: bool,
    ) -> Tuple[Image.Image, int, int]:
        if width <= view_w and height <= view_h:
            return frame_image, width, height
        scale = min(view_w / max(width, 1), view_h / max(height, 1))
        new_w = max(1, int(width * scale))
        new_h = max(1, int(height * scale))
        resampling = Image.Resampling if hasattr(Image, "Resampling") else Image
        resample_filter = resampling.LANCZOS if high_quality else resampling.BILINEAR
        return frame_image.resize((new_w, new_h), resample=resample_filter), new_w, new_h

    def _choose_live_preview_prefer_speed(self) -> bool:
        capture_fps = self.capture.shared_state.last_fps or 60.0
        if capture_fps < 45.0:
            return False
        target_fps = min(max(capture_fps, 30.0), 60.0)
        budget_ms = 1000.0 / target_fps
        now = time.monotonic()
        if self.live_preview_prefer_speed:
            if (
                self.live_preview_render_ema_ms > 0.0
                and self.live_preview_render_ema_ms < budget_ms * 0.45
                and (self.live_preview_fps <= 0.0 or self.live_preview_fps >= capture_fps * 0.90)
                and now - self.live_preview_quality_switch_mono > 2.0
            ):
                self.live_preview_prefer_speed = False
                self.live_preview_quality_switch_mono = now
            return self.live_preview_prefer_speed
        if (
            self.live_preview_render_ema_ms > budget_ms * 0.75
            or (self.live_preview_fps > 0.0 and self.live_preview_fps < capture_fps * 0.82)
        ) and now - self.live_preview_quality_switch_mono > 1.0:
            self.live_preview_prefer_speed = True
            self.live_preview_quality_switch_mono = now
        return self.live_preview_prefer_speed

    def _note_live_preview_presented(self, render_elapsed_s: float) -> None:
        now = time.monotonic()
        elapsed_ms = max(render_elapsed_s * 1000.0, 0.0)
        if self.live_preview_render_ema_ms <= 0.0:
            self.live_preview_render_ema_ms = elapsed_ms
        else:
            self.live_preview_render_ema_ms = self.live_preview_render_ema_ms * 0.85 + elapsed_ms * 0.15
        if self.live_preview_fps_start_mono is None:
            self.live_preview_fps_start_mono = now
            self.live_preview_frames_presented = 0
        self.live_preview_frames_presented += 1
        window_elapsed = now - self.live_preview_fps_start_mono
        if window_elapsed >= 1.0:
            self.live_preview_fps = self.live_preview_frames_presented / window_elapsed
            self.live_preview_frames_presented = 0
            self.live_preview_fps_start_mono = now

    def _render_frame(self, frame_bgra: np.ndarray, prefer_speed: bool = False) -> None:
        if self.preview_canvas is None or self.preview_image_item is None or self.preview_text_item is None:
            return
        view_w = max(self.preview_canvas.winfo_width(), 100)
        view_h = max(self.preview_canvas.winfo_height(), 100)
        if prefer_speed:
            working_frame = frame_bgra
            height, width = working_frame.shape[:2]
            frame_image = Image.frombuffer("RGBA", (width, height), working_frame, "raw", "BGRA", 0, 1)
            display_image, new_w, new_h = self._resize_for_preview(
                frame_image,
                width,
                height,
                view_w,
                view_h,
                high_quality=False,
            )
            if (
                self.preview_image is None
                or self.preview_image_size != (new_w, new_h)
                or self.preview_image_fast_path is not True
            ):
                self.preview_image = ImageTk.PhotoImage(display_image)
                self.preview_image_size = (new_w, new_h)
                self.preview_image_fast_path = True
                self.preview_canvas.itemconfigure(self.preview_image_item, image=self.preview_image)
            else:
                self.preview_image.paste(display_image)
        else:
            height, width = frame_bgra.shape[:2]
            working_frame = np.ascontiguousarray(frame_bgra)
            frame_image = Image.frombuffer("RGBA", (width, height), working_frame, "raw", "BGRA", 0, 1)
            display_image, new_w, new_h = self._resize_for_preview(
                frame_image,
                width,
                height,
                view_w,
                view_h,
                high_quality=True,
            )
            if (
                self.preview_image is None
                or self.preview_image_size != (new_w, new_h)
                or self.preview_image_fast_path is not False
            ):
                self.preview_image = ImageTk.PhotoImage(display_image)
                self.preview_image_size = (new_w, new_h)
                self.preview_image_fast_path = False
                self.preview_canvas.itemconfigure(self.preview_image_item, image=self.preview_image)
            else:
                self.preview_image.paste(display_image)
        x = (view_w - new_w) // 2
        y = (view_h - new_h) // 2
        self.preview_canvas.coords(self.preview_image_item, x, y)
        self.preview_canvas.itemconfigure(self.preview_text_item, text="")
        self.last_rendered_view_size = (view_w, view_h)

    def _render_playback_frame(self) -> None:
        if self.state.cursor_time_s is None:
            return
        if not self._open_playback_video():
            return
        if self.playback_path is None:
            return
        effective_offset = self.get_effective_offset_s()
        video_time_s = max(0.0, self.state.cursor_time_s - effective_offset)
        frame_index = int(round(video_time_s * self.playback_fps))
        if self.playback_frame_count > 0:
            frame_index = min(max(frame_index, 0), self.playback_frame_count - 1)
        else:
            frame_index = max(frame_index, 0)
        if self.playback_fps > 0.0:
            safe_video_time_s = frame_index / self.playback_fps
        else:
            safe_video_time_s = video_time_s
        if self.playback_duration_s > 0.0:
            safe_video_time_s = min(max(safe_video_time_s, 0.0), max(self.playback_duration_s - 1e-6, 0.0))
        current_view_size = (
            max(self.preview_canvas.winfo_width(), 100) if self.preview_canvas is not None else 100,
            max(self.preview_canvas.winfo_height(), 100) if self.preview_canvas is not None else 100,
        )
        exact_timing = not self.is_playback_playing_state()
        prefer_speed = self.is_playback_playing_state()
        request_signature = (
            self.playback_path,
            frame_index,
            current_view_size,
            exact_timing,
            prefer_speed,
        )
        if request_signature == self.playback_last_requested_signature:
            return
        self.playback_last_requested_signature = request_signature
        if self.is_playback_playing_state():
            self.playback_decode_request_inflight = True
        self._enqueue_playback_decode_request(
            clip_path=self.playback_path,
            frame_index=frame_index,
            safe_video_time_s=safe_video_time_s,
            view_size=current_view_size,
            exact_timing=exact_timing,
            fps_hint=self.playback_fps,
            prefer_speed=prefer_speed,
        )

    def _stop_playback_loop(self) -> None:
        self._cancel_playback_tick()
        if not self.is_live_preview_state():
            self.view_state = self.STATE_PLAYBACK_PAUSED
            self.mode = "playback"
        self.state.is_playing = False
        self.state.last_tick_mono = None
        self.play_pause_btn.config(text="Play")

    def _playback_tick(self, generation: int) -> None:
        if generation != self.playback_tick_generation or generation != self.state_generation:
            return
        self.playback_tick_after_id = None
        self.playback_tick_generation = None
        if self.closed or not self.is_playback_playing_state():
            return
        self._consume_playback_decode_result()
        now = time.monotonic()
        last_tick = self.state.last_tick_mono or now
        delta = max(now - last_tick, 0.0)
        self.state.last_tick_mono = now
        next_cursor = (self.state.cursor_time_s or self.state.visible_start_s) + delta * self.state.selected_speed
        if next_cursor >= self.state.visible_end_s:
            self._transition_view_state(self.STATE_PLAYBACK_PAUSED)
            self.play_pause_btn.config(text="Play")
            self._set_cursor_local(self.state.visible_end_s)
            if self.state.cursor_time_s is not None:
                self._dispatch_cursor_change(self.state.cursor_time_s, throttle=False)
            return
        self._set_cursor_local(next_cursor)
        if self.state.cursor_time_s is not None:
            self._dispatch_cursor_change(self.state.cursor_time_s, throttle=True)
        self._schedule_playback_tick()

    def consume_live_frame(self, frame_bgra: Optional[np.ndarray]) -> None:
        if not self.is_live_preview_state() or frame_bgra is None:
            return
        now = time.monotonic()
        if now - self.last_preview_present_mono < self.preview_min_interval_s:
            return
        self.last_preview_present_mono = now
        prefer_speed = self._choose_live_preview_prefer_speed()
        render_start = time.perf_counter()
        self._render_frame(frame_bgra, prefer_speed=prefer_speed)
        self._note_live_preview_presented(time.perf_counter() - render_start)

    def _show_error(self, title: str, message: str) -> None:
        messagebox.showerror(title, message)

    def _poll_window(self) -> None:
        if self.closed:
            return
        if self.capture.shared_state.last_error is not None:
            err = self.capture.shared_state.last_error
            self.capture.shared_state.last_error = None
            logger.warning("Capture/playback error shown to user: %s", err)
            self._show_error("Capture error", str(err))
        self._consume_playback_decode_result()
        frame_bgra = None
        try:
            while True:
                frame_bgra = self.capture.shared_state.preview_queue.get_nowait()
        except Empty:
            pass
        self.consume_live_frame(frame_bgra)
        next_delay_ms = 1 if self.is_playback_playing_state() else 16
        self.window.after(next_delay_ms, self._poll_window)
