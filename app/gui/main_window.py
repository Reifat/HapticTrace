# Copyright 2026 Nikolai Kolesnikov
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import logging
import shutil
import tempfile
import time
import uuid
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import tkinter as tk
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.figure import Figure
from scipy import signal as scipy_signal
from tkinter import filedialog, messagebox, simpledialog, ttk

from app.core.persistence import write_csv_rows
from app.core.phyphox_runtime import PhyphoxService
from app.core.session_bundle import create_session_archive, extract_session_archive
from app.core.session import UnifiedSessionController
from app.dsp import InterpolationConfig, clean_spectrogram_db, compute_envelope
from app.gui.models import AppSession
from app.gui.video_window import VideoWindowController
from app.network.phyphox_client import normalize_url
from app.platform.capture import IphoneCaptureService

__all__ = ["HapticTraceApp"]

logger = logging.getLogger(__name__)


class HapticTraceApp:
    def __init__(
        self,
        root: tk.Tk,
        base_url: str,
        autosave_dir: Path,
        runtime_log_path: Optional[Path] = None,
    ) -> None:
        self.root = root
        self.root.title("HapticTrace")
        self.root.geometry("1100x920")
        self.root.minsize(900, 700)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self._configure_ttk_styles()

        self.autosave_dir = autosave_dir
        self.autosave_dir.mkdir(parents=True, exist_ok=True)
        self.runtime_log_path = runtime_log_path

        self.phyphox = PhyphoxService(base_url)
        self.capture = IphoneCaptureService()
        self.session_controller = UnifiedSessionController()
        self.phyphox.set_session_tracker(self.session_controller)
        self.capture.set_session_tracker(self.session_controller)

        self.base_url_var = tk.StringVar(value=normalize_url(base_url))
        self.status_var = tk.StringVar(value="Phyphox: disconnected")
        self.exp_status_var = tk.StringVar(value="Experiment: unknown")
        self.sensor_var = tk.StringVar(value="Sensors: unknown")
        self.capture_status_var = tk.StringVar(value="iPhone capture: disconnected")
        self.device_status_var = tk.StringVar(value="iPhone device: unknown")
        self.info_var = tk.StringVar(value="")
        self.record_var = tk.StringVar(value="Recording: stopped")
        self.record_mode_var = tk.StringVar(value="Sensors + Video")
        self.show_spectrogram_var = tk.BooleanVar(value=False)
        self.use_acc_var = tk.BooleanVar(value=True)
        self.use_gyr_var = tk.BooleanVar(value=True)
        self.use_env_var = tk.BooleanVar(value=False)
        self.redraw_requested = True
        self.view_locked = False
        self.manual_wave_xlim = None
        self.manual_wave_ylim = None
        self.manual_spec_xlim = None
        self.manual_spec_ylim = None
        self._drag_active = False
        self._drag_ax = None
        self._drag_last = None
        self.closed = False
        self.playback_cursor_time: Optional[float] = None
        self._wave_cursor_artist = None
        self._spec_cursor_artist = None
        self._playback_data_bounds: Optional[Tuple[float, float]] = None
        self.log_window = None
        self.log_text = None
        self.last_log_text: Optional[str] = None
        self.connection_window = None
        self.connection_mode_combo = None
        self.connection_mode_note_var = tk.StringVar(value="")
        self.connection_url_entry = None
        self.connection_phyphox_btn = None
        self.connection_phyphox_disconnect_btn = None
        self.connection_iphone_btn = None
        self.connection_iphone_disconnect_btn = None
        self.loaded_session_tempdir: Optional[Path] = None
        self.log_messages: deque[str] = deque(maxlen=2000)
        self.session_tabs_container = None
        self.session_notebook = None
        self._session_tab_frames: Dict[str, tk.Widget] = {}
        self._session_tab_syncing = False
        self.app_sessions: List[AppSession] = []
        self.active_app_session_id: Optional[str] = None
        self._session_counter = 0
        self.compare_window = None
        self.compare_figure = None
        self.compare_canvas = None
        self.compare_toolbar = None
        self.compare_controls_frame = None
        self.compare_offset_session_combo = None
        self.compare_cursor_combo = None
        self.compare_mode_var = tk.StringVar(value="overlay")
        self.compare_kind_var = tk.StringVar(value="signal")
        self.compare_visibility_vars: Dict[str, tk.BooleanVar] = {}
        self.compare_offset_enabled_var = tk.BooleanVar(value=False)
        self.compare_cursor_enabled_var = tk.BooleanVar(value=False)
        self.compare_cursor_min_x = 0.0
        self.compare_cursor_max_x = 0.0
        self.compare_cursor_items: List[Dict[str, Any]] = []
        self.compare_active_cursor_id_var = tk.StringVar(value="")
        self.compare_cursor_counter = 0
        self.compare_offset_target_var = tk.StringVar(value="")
        self.compare_session_offsets: Dict[str, Tuple[float, float]] = {}
        self._compare_drag_active = False
        self._compare_drag_ax = None
        self._compare_drag_last = None
        self._compare_drag_moved = False
        self._compare_cursor_drag_active = False
        self._compare_offset_drag_active = False

        self.spec_window_var = tk.StringVar(value="hann")
        self.spec_display_interp_var = tk.StringVar(value="nearest")
        self.spec_fragment_mode_var = tk.StringVar(value="visible_when_paused")
        self.spec_last_seconds_var = tk.DoubleVar(value=1.0)
        self.spec_max_freq_var = tk.DoubleVar(value=150.0)
        self.spec_clean_var = tk.StringVar(value="median_clip")
        self.spec_auto_var = tk.BooleanVar(value=True)
        self.spec_nperseg_var = tk.IntVar(value=256)
        self.spec_noverlap_ratio_var = tk.DoubleVar(value=0.92)
        self.spec_nfft_mult_var = tk.IntVar(value=4)
        self.spec_settings_window = None
        self.interp_enabled_var = tk.BooleanVar(value=False)
        self.interp_target_samples_on_window_var = tk.IntVar(value=120)
        self.interp_window_ms_var = tk.DoubleVar(value=120.0)
        self.interp_overlap_ratio_var = tk.DoubleVar(value=0.6)
        self.interp_window_kind_var = tk.StringVar(value="hann")
        self.interp_method_var = tk.StringVar(value="lagrange")
        self.interp_poly_order_var = tk.IntVar(value=4)
        self.interp_post_smoothing_var = tk.StringVar(value="none")
        self.interp_apply_export_var = tk.BooleanVar(value=False)
        self.interp_settings_window = None
        self.interp_source_stats_var = tk.StringVar(value="Source signal stats: waiting for data")

        self.video_window = VideoWindowController(
            self.root,
            self.capture,
            self.session_controller,
            self._handle_video_cursor_change,
        )
        initial_session = self._create_app_session(title="Session 1")
        self.active_app_session_id = initial_session.session_id

        self._build_ui()
        self._append_log("Приложение запущено")
        if self.runtime_log_path is not None:
            self._append_log(f"Runtime log: {self.runtime_log_path}")
        self._draw_empty_axes()
        self._schedule_update()

    def _configure_ttk_styles(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.map(
                "TButton",
                background=[("disabled", "#d8d8d8")],
                foreground=[("disabled", "#7a7a7a")],
            )
        except tk.TclError:
            pass

    def _build_menu(self) -> None:
        self.main_menu = tk.Menu(self.root)

        self.file_menu = tk.Menu(self.main_menu, tearoff=False)
        self.file_menu.add_command(label="New Session", command=self.new_session)
        self.file_menu.add_command(label="Close Session", command=self.close_current_session)
        self.file_menu.add_separator()
        self.file_menu.add_command(label="Save Session", command=self.save_session_dialog)
        self.file_menu.add_command(label="Load Session", command=self.load_session_dialog)
        self.file_menu.add_command(label="Save Graphs", command=self.save_graphs_dialog)
        self.file_menu.add_command(label="Save Video", command=self.save_video_dialog)
        self.file_menu.add_separator()
        self.file_menu.add_command(label="Exit", command=self.on_close)
        self.main_menu.add_cascade(label="File", menu=self.file_menu)

        self.edit_menu = tk.Menu(self.main_menu, tearoff=False)
        self.edit_menu.add_command(label="Rename Session", command=self.rename_current_session)
        self.main_menu.add_cascade(label="Edit", menu=self.edit_menu)

        self.connection_menu = tk.Menu(self.main_menu, tearoff=False)
        self.connection_menu.add_command(label="Connection Settings", command=self.open_connection_window)
        self.connection_menu.add_separator()
        self.connection_menu.add_command(label="Connect Sensors", command=self.start_phyphox_connect_loop)
        self.connection_menu.add_command(label="Disconnect Sensors", command=self.disconnect_phyphox)
        self.connection_menu.add_command(label="Connect iPhone", command=self.start_iphone_connect_loop)
        self.connection_menu.add_command(label="Disconnect iPhone", command=self.disconnect_iphone)
        self.main_menu.add_cascade(label="Connection", menu=self.connection_menu)

        self.view_menu = tk.Menu(self.main_menu, tearoff=False)
        self.view_menu.add_command(label="Show Video Window", command=self.show_video_window)
        self.view_menu.add_checkbutton(label="Show Spectrogram", variable=self.show_spectrogram_var, command=self.toggle_spectrogram_visibility)
        self.view_menu.add_command(label="Log", command=self.open_log_window)
        self.main_menu.add_cascade(label="View", menu=self.view_menu)

        self.tools_menu = tk.Menu(self.main_menu, tearoff=False)
        self.tools_menu.add_command(label="Spectrogram Settings", command=self._open_spectrogram_settings)
        self.tools_menu.add_command(label="Interpolation Settings", command=self._open_interpolation_settings)
        self.main_menu.add_cascade(label="Tools", menu=self.tools_menu)

        self.root.configure(menu=self.main_menu)

    @staticmethod
    def _set_menu_entry_state(menu: Optional[tk.Menu], label: str, enabled: bool) -> None:
        if menu is None:
            return
        menu.entryconfigure(label, state=(tk.NORMAL if enabled else tk.DISABLED))

    def _create_app_session(self, title: Optional[str] = None) -> AppSession:
        self._session_counter += 1
        session = AppSession(
            session_id=f"app_session_{uuid.uuid4().hex[:10]}",
            title=title or f"Session {self._session_counter}",
            snapshot_dir=Path(tempfile.mkdtemp(prefix="unified_haptic_app_session_")),
            has_data=False,
        )
        self.app_sessions.append(session)
        return session

    @staticmethod
    def _sanitize_session_name_for_filename(name: str) -> str:
        cleaned = "".join(ch if (ch.isalnum() or ch in ("-", "_")) else "_" for ch in str(name).strip())
        cleaned = cleaned.strip("_")
        return cleaned or f"session_{time.strftime('%Y%m%d_%H%M%S')}"

    def _prompt_for_session_title(self, initial_value: str, dialog_title: str) -> Optional[str]:
        value = simpledialog.askstring(dialog_title, "Session name:", initialvalue=initial_value, parent=self.root)
        if value is None:
            return None
        value = value.strip()
        return value or initial_value

    def _get_active_app_session(self) -> Optional[AppSession]:
        for session in self.app_sessions:
            if session.session_id == self.active_app_session_id:
                return session
        return None

    def rename_current_session(self) -> None:
        session = self._get_active_app_session()
        if session is None:
            return
        new_title = self._prompt_for_session_title(session.title, "Rename Session")
        if new_title is None:
            return
        session.title = new_title
        self._refresh_session_tabs()
        self._refresh_compare_window()

    def _is_recording_active(self) -> bool:
        return bool(self.phyphox.is_measuring or self.capture.shared_state.recorder.recording)

    def _current_state_has_data(self) -> bool:
        return bool(self.phyphox.has_any_data() or self.capture.has_finished_recording())

    def _count_sessions_with_data(self) -> int:
        active = self._get_active_app_session()
        count = 0
        for session in self.app_sessions:
            if session is active:
                if self._current_state_has_data():
                    count += 1
            elif session.has_data:
                count += 1
        return count

    def _clear_session_snapshot_dir(self, session: AppSession) -> None:
        shutil.rmtree(session.snapshot_dir, ignore_errors=True)
        session.snapshot_dir.mkdir(parents=True, exist_ok=True)

    def _export_current_state_to_dir(self, out_dir: Path) -> None:
        out_dir.parent.mkdir(parents=True, exist_ok=True)
        temp_dir = Path(tempfile.mkdtemp(prefix=f"{out_dir.name}_export_", dir=str(out_dir.parent)))
        try:
            recordings = self.capture.export_recordings(temp_dir)
            self.phyphox.export_data(
                temp_dir,
                self.use_acc_var.get(),
                self.use_gyr_var.get(),
                self._build_export_meta(recordings),
            )
            self._export_interpolated_signal(temp_dir)
            shutil.rmtree(out_dir, ignore_errors=True)
            temp_dir.replace(out_dir)
        except Exception:
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise

    def _save_active_session_snapshot(self) -> None:
        session = self._get_active_app_session()
        if session is None:
            return
        if not self._current_state_has_data():
            session.has_data = False
            self._clear_session_snapshot_dir(session)
            return
        self._export_current_state_to_dir(session.snapshot_dir)
        session.has_data = True

    def _current_video_uses_session_snapshot(self, session: Optional[AppSession]) -> bool:
        if session is None:
            return False
        root_dir = session.snapshot_dir.resolve()
        recorder = self.capture.shared_state.recorder
        clip_paths: List[str] = []
        with recorder.lock:
            if recorder.temp_path:
                clip_paths.append(recorder.temp_path)
            clip_paths.extend(clip.temp_path for clip in recorder.finished_clips if clip.temp_path)
        for clip_path in clip_paths:
            try:
                resolved = Path(clip_path).resolve()
            except Exception:
                continue
            if resolved == root_dir or root_dir in resolved.parents:
                return True
        return False

    def _load_session_dir_into_current_state(
        self,
        session_dir: Path,
        preserve_current_video_files: bool = False,
        clear_loaded_archive: bool = True,
    ) -> None:
        self._clear_local_session_state(
            clear_loaded_archive=clear_loaded_archive,
            remove_video_files=not preserve_current_video_files,
        )
        self.video_window.activate_live_mode()
        meta = self.phyphox.load_exported_data(session_dir)
        loaded_recordings: List[Dict[str, Any]] = []
        for item in meta.get("recordings", []):
            if not isinstance(item, dict):
                continue
            clip_path = session_dir / Path(str(item.get("path") or "")).name
            if not clip_path.exists():
                continue
            clip_record = dict(item)
            clip_record["temp_path"] = str(clip_path)
            loaded_recordings.append(clip_record)
        self.capture.load_recordings(loaded_recordings)
        self.session_controller.load_metadata(meta.get("sync_metadata") or {})
        recording_targets = meta.get("recording_targets") or {}
        record_sensors = bool(recording_targets.get("sensors", True))
        record_video = bool(recording_targets.get("video", True))
        self._set_record_mode_from_targets(record_sensors, record_video)
        interp_settings = meta.get("interpolation_settings") or {}
        interp_config = InterpolationConfig.from_dict(interp_settings)
        self.interp_enabled_var.set(interp_config.enabled)
        self.interp_target_samples_on_window_var.set(interp_config.target_samples_on_window)
        self.interp_window_ms_var.set(interp_config.window_ms)
        self.interp_overlap_ratio_var.set(interp_config.overlap_ratio)
        self.interp_window_kind_var.set(interp_config.window_kind)
        self.interp_method_var.set(interp_config.method)
        self.interp_poly_order_var.set(interp_config.poly_order)
        self.interp_post_smoothing_var.set(interp_config.post_smoothing)
        self.interp_apply_export_var.set(interp_config.apply_to_export)
        sensor_enabled = meta.get("sensor_enabled") or {}
        self.use_acc_var.set(bool(sensor_enabled.get("accel", self.phyphox.has_acc())))
        self.use_gyr_var.set(bool(sensor_enabled.get("gyro", self.phyphox.has_gyr())))
        if not self.use_acc_var.get() and not self.use_gyr_var.get():
            if self.phyphox.has_acc():
                self.use_acc_var.set(True)
            elif self.phyphox.has_gyr():
                self.use_gyr_var.set(True)
        self.video_window.activate_live_mode()
        duration_s = self._get_playback_duration()
        playback_settings = meta.get("playback_settings") or {}
        if self.capture.has_finished_recording() and duration_s > 0.0:
            self.video_window.activate_playback_mode(duration_s)
            self.video_window.load_settings(playback_settings)
            self.set_playback_cursor(0.0)
        else:
            self.video_window.reset_offset_state()
        self._reset_view()
        self._request_redraw_only()

    def _restore_app_session(self, session: AppSession, preserve_current_video_files: bool = False) -> None:
        if session.has_data and (session.snapshot_dir / "session_meta.json").exists():
            self._load_session_dir_into_current_state(
                session.snapshot_dir,
                preserve_current_video_files=preserve_current_video_files,
            )
        else:
            self._clear_local_session_state(
                clear_loaded_archive=True,
                remove_video_files=not preserve_current_video_files,
            )
            self._reset_view()
            self._request_redraw_only()

    def _refresh_session_tabs(self) -> None:
        if self.session_notebook is None or self.session_tabs_container is None:
            return
        self._session_tab_syncing = True
        try:
            for child in list(self._session_tab_frames.values()):
                try:
                    child.destroy()
                except Exception:
                    pass
            self._session_tab_frames = {}
            for tab_id in self.session_notebook.tabs():
                self.session_notebook.forget(tab_id)
            for session in self.app_sessions:
                frame = ttk.Frame(self.session_notebook)
                self.session_notebook.add(frame, text=session.title)
                self._session_tab_frames[str(frame)] = frame
                if session.session_id == self.active_app_session_id:
                    self.session_notebook.select(frame)
            if len(self.app_sessions) > 1:
                if not self.session_tabs_container.winfo_ismapped():
                    self.session_tabs_container.pack(side=tk.TOP, fill=tk.X, before=self.quick_box)
            else:
                if self.session_tabs_container.winfo_ismapped():
                    self.session_tabs_container.pack_forget()
        finally:
            self._session_tab_syncing = False
        self._refresh_compare_controls()

    def _handle_session_tab_changed(self, _event=None) -> None:
        if self._session_tab_syncing or self.session_notebook is None:
            return
        selected = self.session_notebook.select()
        if not selected:
            return
        tab_index = self.session_notebook.index(selected)
        if tab_index < 0 or tab_index >= len(self.app_sessions):
            return
        target_session = self.app_sessions[tab_index]
        if target_session.session_id == self.active_app_session_id:
            return
        if self._is_recording_active():
            self._refresh_session_tabs()
            self._show_info("Sessions", "Stop recording before switching sessions")
            return
        current_session = self._get_active_app_session()
        preserve_current_video_files = self._current_video_uses_session_snapshot(current_session)
        self._save_active_session_snapshot()
        self.active_app_session_id = target_session.session_id
        self._restore_app_session(
            target_session,
            preserve_current_video_files=preserve_current_video_files,
        )
        self._refresh_session_tabs()
        self._refresh_action_buttons()

    def new_session(self) -> None:
        if self._is_recording_active():
            self._show_info("Sessions", "Stop recording before creating a new session")
            return
        suggested_title = f"Session {self._session_counter + 1}"
        new_title = self._prompt_for_session_title(suggested_title, "New Session")
        if new_title is None:
            return
        preserve_current_video_files = self._current_video_uses_session_snapshot(self._get_active_app_session())
        self._save_active_session_snapshot()
        new_session = self._create_app_session(title=new_title)
        self.active_app_session_id = new_session.session_id
        self._clear_local_session_state(
            clear_loaded_archive=True,
            remove_video_files=not preserve_current_video_files,
        )
        self._reset_view()
        self._refresh_session_tabs()
        self._refresh_action_buttons()

    def _prompt_save_active_session(self, reason: str) -> bool:
        active_session = self._get_active_app_session()
        current_has_data = self._current_state_has_data()
        other_sessions_with_data = max(0, self._count_sessions_with_data() - (1 if current_has_data else 0))
        if active_session is None or not current_has_data:
            if reason == "exiting" and other_sessions_with_data > 0:
                suffix = "s" if other_sessions_with_data != 1 else ""
                return bool(
                    messagebox.askokcancel(
                        "Exit",
                        (
                            f"The current session is empty, but there are {other_sessions_with_data} "
                            f"other session{suffix} with data open in tabs.\n\n"
                            "They will not be saved automatically. Exit anyway?"
                        ),
                    )
                )
            return True
        extra_note = ""
        if other_sessions_with_data > 0:
            suffix = "s" if other_sessions_with_data != 1 else ""
            extra_note = f"\n\nThere are also {other_sessions_with_data} other session{suffix} with data open in tabs."
        answer = messagebox.askyesnocancel(
            "Save Session",
            f"Save '{active_session.title}' before {reason}?{extra_note}",
        )
        if answer is None:
            return False
        if answer:
            return self.save_session_dialog()
        return True

    def close_current_session(self) -> None:
        if self._is_recording_active():
            self._show_info("Close Session", "Stop recording before closing the current session")
            return
        current_session = self._get_active_app_session()
        if current_session is None:
            return
        if not self._prompt_save_active_session("closing this session"):
            return
        preserve_current_video_files = self._current_video_uses_session_snapshot(current_session)
        remaining_sessions = [session for session in self.app_sessions if session.session_id != current_session.session_id]
        if remaining_sessions:
            target_session = remaining_sessions[0]
            self.active_app_session_id = target_session.session_id
            self._restore_app_session(
                target_session,
                preserve_current_video_files=preserve_current_video_files,
            )
            self.app_sessions = remaining_sessions
        else:
            self._clear_local_session_state(
                clear_loaded_archive=True,
                remove_video_files=not preserve_current_video_files,
            )
            self.app_sessions = []
            new_session = self._create_app_session()
            self.active_app_session_id = new_session.session_id
            self._reset_view()
        shutil.rmtree(current_session.snapshot_dir, ignore_errors=True)
        self._refresh_session_tabs()
        self._refresh_action_buttons()
        self._refresh_compare_window()

    def _collect_compare_session_output(self, session: AppSession) -> Optional[Dict[str, Any]]:
        config = self._get_interpolation_config()
        if session.session_id == self.active_app_session_id:
            return self.phyphox.current_output(self.use_acc_var.get(), self.use_gyr_var.get(), config)
        if not session.has_data or not (session.snapshot_dir / "session_meta.json").exists():
            return None
        temp_phyphox = PhyphoxService(self.base_url_var.get())
        temp_phyphox.load_exported_data(session.snapshot_dir)
        return temp_phyphox.current_output(self.use_acc_var.get(), self.use_gyr_var.get(), config)

    def _refresh_compare_controls(self) -> None:
        if self.compare_controls_frame is None:
            return
        try:
            if not self.compare_controls_frame.winfo_exists():
                return
        except Exception:
            return
        for child in self.compare_controls_frame.winfo_children():
            child.destroy()
        for session in self.app_sessions:
            if session.session_id not in self.compare_visibility_vars:
                self.compare_visibility_vars[session.session_id] = tk.BooleanVar(value=True)
            label = session.title
            is_active = session.session_id == self.active_app_session_id
            has_data = self._current_state_has_data() if is_active else session.has_data
            if is_active:
                label += " (current)"
            if not has_data:
                label += " (empty)"
            ttk.Checkbutton(
                self.compare_controls_frame,
                text=label,
                variable=self.compare_visibility_vars[session.session_id],
                command=self._refresh_compare_window,
            ).pack(anchor="w", fill=tk.X, pady=2)

    def _compare_visible_sessions_with_data(self) -> List[AppSession]:
        visible: List[AppSession] = []
        for session in self.app_sessions:
            if not self.compare_visibility_vars.get(session.session_id, tk.BooleanVar(value=True)).get():
                continue
            is_active = session.session_id == self.active_app_session_id
            has_data = self._current_state_has_data() if is_active else session.has_data
            if has_data:
                visible.append(session)
        return visible

    def _compare_offset_target_session_id(self) -> str:
        current_title = self.compare_offset_target_var.get()
        for session in self._compare_visible_sessions_with_data():
            if session.title == current_title:
                return session.session_id
        return ""

    def _sync_compare_offset_target(self, sessions: Sequence[AppSession]) -> None:
        valid_titles = [session.title for session in sessions]
        if self.compare_offset_target_var.get() not in valid_titles:
            self.compare_offset_target_var.set(valid_titles[0] if valid_titles else "")
        if getattr(self, "compare_offset_session_combo", None) is not None:
            self.compare_offset_session_combo.configure(values=valid_titles)

    def _reset_compare_selected_offset(self) -> None:
        target_id = self._compare_offset_target_session_id()
        if not target_id:
            return
        self.compare_session_offsets[target_id] = (0.0, 0.0)
        self._refresh_compare_window()

    def _next_compare_cursor_label(self) -> str:
        self.compare_cursor_counter += 1
        return f"C{self.compare_cursor_counter}"

    def _active_compare_cursor(self) -> Optional[Dict[str, Any]]:
        active_id = self.compare_active_cursor_id_var.get()
        for item in self.compare_cursor_items:
            if item.get("id") == active_id:
                return item
        return self.compare_cursor_items[0] if self.compare_cursor_items else None

    def _sync_compare_cursor_selection(self) -> None:
        valid_ids = [str(item.get("id")) for item in self.compare_cursor_items]
        if self.compare_active_cursor_id_var.get() not in valid_ids:
            self.compare_active_cursor_id_var.set(valid_ids[0] if valid_ids else "")
        if getattr(self, "compare_cursor_combo", None) is not None:
            self.compare_cursor_combo.configure(values=valid_ids)

    def _add_compare_cursor(self) -> None:
        if self.compare_cursor_max_x > self.compare_cursor_min_x:
            cursor_time = 0.5 * (self.compare_cursor_min_x + self.compare_cursor_max_x)
        else:
            cursor_time = 0.0
        cursor = {"id": self._next_compare_cursor_label(), "time": float(cursor_time)}
        self.compare_cursor_items.append(cursor)
        self.compare_active_cursor_id_var.set(str(cursor["id"]))
        self.compare_cursor_enabled_var.set(True)
        self._refresh_compare_window()

    def _remove_active_compare_cursor(self) -> None:
        active = self._active_compare_cursor()
        if active is None:
            return
        self.compare_cursor_items = [item for item in self.compare_cursor_items if item.get("id") != active.get("id")]
        self._sync_compare_cursor_selection()
        self._refresh_compare_window()

    def _set_active_compare_cursor_time(self, cursor_time_s: float) -> None:
        active = self._active_compare_cursor()
        if active is None:
            if not self.compare_cursor_items:
                self._add_compare_cursor()
                active = self._active_compare_cursor()
        if active is None:
            return
        if self.compare_cursor_max_x > self.compare_cursor_min_x:
            active["time"] = min(
                max(float(cursor_time_s), self.compare_cursor_min_x),
                self.compare_cursor_max_x,
            )
        else:
            active["time"] = float(cursor_time_s)
        self.compare_active_cursor_id_var.set(str(active.get("id")))

    def _nearest_compare_cursor(self, ax, xdata: float) -> Optional[Dict[str, Any]]:
        if not self.compare_cursor_enabled_var.get() or ax is None or not self.compare_cursor_items:
            return None
        x0, x1 = ax.get_xlim()
        threshold = max(abs(x1 - x0) * 0.02, 1e-6)
        best_item = None
        best_distance = None
        for item in self.compare_cursor_items:
            distance = abs(float(xdata) - float(item.get("time", 0.0)))
            if distance <= threshold and (best_distance is None or distance < best_distance):
                best_item = item
                best_distance = distance
        return best_item

    def _refresh_compare_window(self) -> None:
        if self.compare_window is None:
            return
        try:
            if not self.compare_window.winfo_exists():
                return
        except Exception:
            return
        prev_axes = self._compare_axes()
        prev_view_state = [
            {
                "xlim": ax.get_xlim(),
                "ylim": ax.get_ylim(),
            }
            for ax in prev_axes
        ]
        self._refresh_compare_controls()
        self.compare_figure.clear()
        visible_sessions = self._compare_visible_sessions_with_data()
        self._sync_compare_offset_target(visible_sessions)
        self._sync_compare_cursor_selection()
        compare_payloads: List[Tuple[AppSession, Dict[str, Any]]] = []
        for session in visible_sessions:
            out = self._collect_compare_session_output(session)
            if out is not None:
                compare_payloads.append((session, out))
        if not compare_payloads:
            self.compare_cursor_min_x = 0.0
            self.compare_cursor_max_x = 0.0
            ax = self.compare_figure.add_subplot(111)
            ax.set_title("Compare")
            ax.text(0.5, 0.5, "No comparable session data", ha="center", va="center", transform=ax.transAxes)
            self.compare_canvas.draw_idle()
            return
        x_starts = [float(payload["rel_t"][0]) for _, payload in compare_payloads if len(payload["rel_t"])]
        x_ends = [float(payload["rel_t"][-1]) for _, payload in compare_payloads if len(payload["rel_t"])]
        if x_starts and x_ends:
            self.compare_cursor_min_x = min(x_starts)
            self.compare_cursor_max_x = max(x_ends)
            for item in self.compare_cursor_items:
                item["time"] = min(
                    max(float(item.get("time", 0.0)), self.compare_cursor_min_x),
                    self.compare_cursor_max_x,
                )
        data_kind = self.compare_kind_var.get()
        view_mode = self.compare_mode_var.get()
        if data_kind == "signal":
            if view_mode == "overlay":
                ax = self.compare_figure.add_subplot(111)
                for session, out in compare_payloads:
                    rel_t = out["rel_t"]
                    sig = out["signal"]
                    fs = out["fs"]
                    if self.use_env_var.get():
                        sig = compute_envelope(sig, fs)
                    offset_x, offset_y = self.compare_session_offsets.get(session.session_id, (0.0, 0.0))
                    ax.plot(rel_t + offset_x, sig + offset_y, linewidth=1.0, label=session.title)
                ax.set_title("Session comparison: signal")
                ax.set_xlabel("Time, s")
                ax.set_ylabel("Normalized amplitude")
                ax.legend()
            else:
                shared_ax = None
                for idx, (session, out) in enumerate(compare_payloads, start=1):
                    ax = self.compare_figure.add_subplot(len(compare_payloads), 1, idx, sharex=shared_ax)
                    if shared_ax is None:
                        shared_ax = ax
                    rel_t = out["rel_t"]
                    sig = out["signal"]
                    fs = out["fs"]
                    if self.use_env_var.get():
                        sig = compute_envelope(sig, fs)
                    ax.plot(rel_t, sig, linewidth=1.0, label=session.title)
                    ax.set_ylabel("Amplitude")
                    ax.legend(loc="upper right")
                shared_ax.set_xlabel("Time, s")
                self.compare_figure.suptitle("Session comparison: signal")
        else:
            cmaps = ["viridis", "plasma", "inferno", "magma", "cividis", "turbo"]
            if view_mode == "overlay":
                ax = self.compare_figure.add_subplot(111)
                alpha = 1.0 if len(compare_payloads) == 1 else max(0.20, 0.65 / len(compare_payloads))
                for idx, (session, out) in enumerate(compare_payloads):
                    rel_t = out["rel_t"]
                    sig = out["signal"]
                    fs = out["fs"]
                    if self.use_env_var.get():
                        sig = compute_envelope(sig, fs)
                    sxx_db, spec_t0, _spec_t1, tt, f = self._prepare_spectrogram(rel_t, sig, fs)
                    if tt.size and f.size:
                        ax.imshow(
                            sxx_db,
                            origin="lower",
                            aspect="auto",
                            interpolation=self.spec_display_interp_var.get(),
                            extent=[spec_t0 + tt[0], spec_t0 + tt[-1], f[0], f[-1]],
                            cmap=cmaps[idx % len(cmaps)],
                            alpha=alpha,
                        )
                        ax.plot([], [], label=session.title)
                ax.set_title("Session comparison: spectrogram")
                ax.set_xlabel("Time, s")
                ax.set_ylabel("Hz")
                ax.legend()
            else:
                shared_ax = None
                for idx, (session, out) in enumerate(compare_payloads, start=1):
                    ax = self.compare_figure.add_subplot(len(compare_payloads), 1, idx, sharex=shared_ax)
                    if shared_ax is None:
                        shared_ax = ax
                    rel_t = out["rel_t"]
                    sig = out["signal"]
                    fs = out["fs"]
                    if self.use_env_var.get():
                        sig = compute_envelope(sig, fs)
                    sxx_db, spec_t0, _spec_t1, tt, f = self._prepare_spectrogram(rel_t, sig, fs)
                    if tt.size and f.size:
                        ax.imshow(
                            sxx_db,
                            origin="lower",
                            aspect="auto",
                            interpolation=self.spec_display_interp_var.get(),
                            extent=[spec_t0 + tt[0], spec_t0 + tt[-1], f[0], f[-1]],
                        )
                    ax.set_ylabel("Hz")
                    ax.set_title(session.title)
                shared_ax.set_xlabel("Time, s")
                self.compare_figure.suptitle("Session comparison: spectrogram")
        if self.compare_cursor_enabled_var.get() and self.compare_cursor_items:
            active_cursor = self._active_compare_cursor()
            for item in self.compare_cursor_items:
                color = "red" if active_cursor is not None and item.get("id") == active_cursor.get("id") else "darkorange"
                for ax in self._compare_axes():
                    ax.axvline(
                        float(item.get("time", 0.0)),
                        color=color,
                        linewidth=1.0,
                        alpha=0.9,
                        linestyle="-" if color == "red" else "--",
                    )
        new_axes = self._compare_axes()
        if len(prev_view_state) == len(new_axes):
            for state, ax in zip(prev_view_state, new_axes):
                ax.set_xlim(state["xlim"])
                ax.set_ylim(state["ylim"])
        self.compare_figure.tight_layout()
        self.compare_canvas.draw_idle()

    def _compare_axes(self) -> List[Any]:
        if self.compare_figure is None:
            return []
        return [ax for ax in self.compare_figure.axes if getattr(ax, "get_visible", lambda: False)()]

    def _toggle_compare_cursor(self) -> None:
        if self.compare_cursor_enabled_var.get() and not self.compare_cursor_items:
            self._add_compare_cursor()
            return
        self._refresh_compare_window()

    def _on_compare_cursor_selected(self, _event=None) -> None:
        self._refresh_compare_window()

    def _on_compare_scroll_zoom(self, event) -> None:
        if self.compare_canvas is None or self.compare_figure is None:
            return
        if event is None or event.inaxes not in self._compare_axes():
            return
        if getattr(self.compare_toolbar, "mode", ""):
            return
        xdata = event.xdata
        ydata = event.ydata
        if xdata is None:
            return
        scale = 1 / 1.2 if event.button == "up" else 1.2
        axes = self._compare_axes()
        if not axes:
            return
        for ax in axes:
            x0, x1 = ax.get_xlim()
            relx = 0.5 if x1 == x0 else (xdata - x0) / (x1 - x0)
            width = (x1 - x0) * scale
            nx0 = xdata - width * relx
            nx1 = xdata + width * (1.0 - relx)
            ax.set_xlim(nx0, nx1)
        if ydata is not None:
            ax = event.inaxes
            y0, y1 = ax.get_ylim()
            rely = 0.5 if y1 == y0 else (ydata - y0) / (y1 - y0)
            height = (y1 - y0) * scale
            ny0 = ydata - height * rely
            ny1 = ydata + height * (1.0 - rely)
            ax.set_ylim(ny0, ny1)
        self.compare_canvas.draw_idle()

    def _on_compare_mouse_press(self, event) -> None:
        if self.compare_canvas is None or self.compare_figure is None:
            return
        if event is None or event.inaxes not in self._compare_axes():
            return
        if event.button != 1:
            return
        if getattr(self.compare_toolbar, "mode", ""):
            return
        if event.xdata is None or event.ydata is None:
            return
        near_cursor = self._nearest_compare_cursor(event.inaxes, float(event.xdata))
        if near_cursor is not None:
            self._compare_cursor_drag_active = True
            self.compare_active_cursor_id_var.set(str(near_cursor.get("id")))
            self._set_active_compare_cursor_time(float(event.xdata))
            self.compare_canvas.draw_idle()
            return
        if (
            self.compare_offset_enabled_var.get()
            and self.compare_kind_var.get() == "signal"
            and self.compare_mode_var.get() == "overlay"
            and self._compare_offset_target_session_id()
        ):
            self._compare_offset_drag_active = True
            self._compare_drag_last = (event.xdata, event.ydata)
            return
        self._compare_drag_active = True
        self._compare_drag_ax = event.inaxes
        self._compare_drag_last = (event.xdata, event.ydata)
        self._compare_drag_moved = False

    def _on_compare_mouse_move(self, event) -> None:
        if self.compare_canvas is None:
            return
        if self._compare_cursor_drag_active:
            if event is None or event.xdata is None:
                return
            self._set_active_compare_cursor_time(float(event.xdata))
            self._refresh_compare_window()
            return
        if self._compare_offset_drag_active:
            if event is None or event.xdata is None or event.ydata is None or self._compare_drag_last is None:
                return
            target_id = self._compare_offset_target_session_id()
            if not target_id:
                return
            last_x, last_y = self._compare_drag_last
            dx = float(event.xdata) - float(last_x)
            dy = float(event.ydata) - float(last_y)
            cur_x, cur_y = self.compare_session_offsets.get(target_id, (0.0, 0.0))
            self.compare_session_offsets[target_id] = (cur_x + dx, cur_y + dy)
            self._compare_drag_last = (event.xdata, event.ydata)
            self._refresh_compare_window()
            return
        if not self._compare_drag_active:
            return
        if event is None or event.inaxes is None or self._compare_drag_ax is None:
            return
        if event.xdata is None or event.ydata is None or self._compare_drag_last is None:
            return
        axes = self._compare_axes()
        if not axes:
            return
        last_x, last_y = self._compare_drag_last
        dx = event.xdata - last_x
        dy = event.ydata - last_y
        for ax in axes:
            x0, x1 = ax.get_xlim()
            ax.set_xlim(x0 - dx, x1 - dx)
        y0, y1 = self._compare_drag_ax.get_ylim()
        self._compare_drag_ax.set_ylim(y0 - dy, y1 - dy)
        self._compare_drag_last = (event.xdata, event.ydata)
        self._compare_drag_moved = True
        self.compare_canvas.draw_idle()

    def _on_compare_mouse_release(self, event) -> None:
        if self._compare_cursor_drag_active:
            self._compare_cursor_drag_active = False
            return
        if self._compare_offset_drag_active:
            self._compare_offset_drag_active = False
            self._compare_drag_last = None
            return
        if not self._compare_drag_active:
            return
        if (
            not self._compare_drag_moved
            and self.compare_cursor_enabled_var.get()
            and event is not None
            and event.xdata is not None
        ):
            self._set_active_compare_cursor_time(float(event.xdata))
            self._refresh_compare_window()
        self._compare_drag_active = False
        self._compare_drag_ax = None
        self._compare_drag_last = None
        self._compare_drag_moved = False

    def open_compare_window(self) -> None:
        if self.compare_window is not None:
            try:
                if self.compare_window.winfo_exists():
                    self._refresh_compare_window()
                    self.compare_window.lift()
                    self.compare_window.focus_force()
                    return
            except Exception:
                pass
        win = tk.Toplevel(self.root)
        self.compare_window = win
        win.title("Compare Sessions")
        win.geometry("1200x860")
        top = ttk.Frame(win, padding=12)
        top.pack(fill=tk.X)
        ttk.Label(top, text="Compare").pack(side=tk.LEFT)
        ttk.Radiobutton(top, text="Signal", variable=self.compare_kind_var, value="signal", command=self._refresh_compare_window).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Radiobutton(top, text="Spectrogram", variable=self.compare_kind_var, value="spectrogram", command=self._refresh_compare_window).pack(side=tk.LEFT, padx=(4, 0))
        ttk.Label(top, text="View").pack(side=tk.LEFT, padx=(16, 0))
        ttk.Radiobutton(top, text="Overlay", variable=self.compare_mode_var, value="overlay", command=self._refresh_compare_window).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Radiobutton(top, text="Split", variable=self.compare_mode_var, value="split", command=self._refresh_compare_window).pack(side=tk.LEFT, padx=(4, 0))
        ttk.Checkbutton(
            top,
            text="Shift selected session",
            variable=self.compare_offset_enabled_var,
            command=self._refresh_compare_window,
        ).pack(side=tk.LEFT, padx=(16, 0))
        self.compare_offset_session_combo = ttk.Combobox(
            top,
            textvariable=self.compare_offset_target_var,
            values=[],
            width=18,
            state="readonly",
        )
        self.compare_offset_session_combo.pack(side=tk.LEFT, padx=(6, 0))
        self.compare_offset_session_combo.bind("<<ComboboxSelected>>", lambda _e: self._refresh_compare_window())
        ttk.Button(top, text="Reset Shift", command=self._reset_compare_selected_offset).pack(side=tk.LEFT, padx=(4, 0))
        ttk.Checkbutton(top, text="Cursors", variable=self.compare_cursor_enabled_var, command=self._toggle_compare_cursor).pack(side=tk.LEFT, padx=(16, 0))
        self.compare_cursor_combo = ttk.Combobox(
            top,
            textvariable=self.compare_active_cursor_id_var,
            values=[],
            width=6,
            state="readonly",
        )
        self.compare_cursor_combo.pack(side=tk.LEFT, padx=(6, 0))
        self.compare_cursor_combo.bind("<<ComboboxSelected>>", self._on_compare_cursor_selected)
        ttk.Button(top, text="+ Cursor", command=self._add_compare_cursor).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(top, text="- Cursor", command=self._remove_active_compare_cursor).pack(side=tk.LEFT, padx=(4, 0))
        ttk.Button(top, text="Refresh", command=self._refresh_compare_window).pack(side=tk.RIGHT)

        body = ttk.Frame(win, padding=(12, 0, 12, 12))
        body.pack(fill=tk.BOTH, expand=True)
        self.compare_controls_frame = ttk.LabelFrame(body, text="Sessions", padding=8)
        self.compare_controls_frame.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 12))
        plot_frame = ttk.Frame(body)
        plot_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.compare_figure = Figure(figsize=(11, 7), dpi=100)
        self.compare_canvas = FigureCanvasTkAgg(self.compare_figure, master=plot_frame)
        toolbar_frame = ttk.Frame(plot_frame)
        toolbar_frame.pack(side=tk.TOP, fill=tk.X)
        self.compare_toolbar = NavigationToolbar2Tk(self.compare_canvas, toolbar_frame, pack_toolbar=False)
        self.compare_toolbar.update()
        self.compare_toolbar.pack(side=tk.LEFT, fill=tk.X)
        self.compare_canvas.get_tk_widget().pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        self.compare_canvas.mpl_connect("scroll_event", self._on_compare_scroll_zoom)
        self.compare_canvas.mpl_connect("button_press_event", self._on_compare_mouse_press)
        self.compare_canvas.mpl_connect("motion_notify_event", self._on_compare_mouse_move)
        self.compare_canvas.mpl_connect("button_release_event", self._on_compare_mouse_release)

        def close_window() -> None:
            self.compare_window = None
            self.compare_figure = None
            self.compare_canvas = None
            self.compare_toolbar = None
            self.compare_controls_frame = None
            self.compare_offset_session_combo = None
            self.compare_cursor_combo = None
            self._compare_drag_active = False
            self._compare_drag_ax = None
            self._compare_drag_last = None
            self._compare_drag_moved = False
            self._compare_cursor_drag_active = False
            self._compare_offset_drag_active = False
            win.destroy()

        win.protocol("WM_DELETE_WINDOW", close_window)
        self._refresh_compare_window()

    def _build_ui(self) -> None:
        self._build_menu()

        self.session_tabs_container = ttk.Frame(self.root, padding=(8, 8, 8, 0))
        self.session_notebook = ttk.Notebook(self.session_tabs_container)
        self.session_notebook.pack(fill=tk.X)
        self.session_notebook.bind("<<NotebookTabChanged>>", self._handle_session_tab_changed)

        self.quick_box = ttk.Frame(self.root, padding=(8, 8, 8, 6))
        self.quick_box.pack(side=tk.TOP, fill=tk.X)
        self.play_btn = ttk.Button(self.quick_box, text="Play", command=self.cmd_play)
        self.pause_btn = ttk.Button(self.quick_box, text="Stop", command=self.cmd_stop)
        self.clear_btn = ttk.Button(self.quick_box, text="Clear", command=self.cmd_clear)
        self.compare_btn = ttk.Button(self.quick_box, text="Compare", command=self.open_compare_window)
        self.reset_view_btn = ttk.Button(self.quick_box, text="Reset View", command=self._reset_view)
        self.play_btn.pack(side=tk.LEFT)
        self.pause_btn.pack(side=tk.LEFT, padx=(4, 0))
        self.clear_btn.pack(side=tk.LEFT, padx=(4, 0))
        self.compare_btn.pack(side=tk.LEFT, padx=(4, 0))
        self.reset_view_btn.pack(side=tk.LEFT, padx=(12, 0))
        ttk.Label(self.quick_box, textvariable=self.record_var).pack(side=tk.RIGHT)

        sensor_box = ttk.Frame(self.root, padding=(8, 0, 8, 6))
        sensor_box.pack(side=tk.TOP, fill=tk.X)
        ttk.Label(sensor_box, text="Output:").pack(side=tk.LEFT)
        self.acc_check = ttk.Checkbutton(sensor_box, text="Accelerometer", variable=self.use_acc_var, command=self._apply_sensor_toggle)
        self.gyr_check = ttk.Checkbutton(sensor_box, text="Gyroscope", variable=self.use_gyr_var, command=self._apply_sensor_toggle)
        self.env_check = ttk.Checkbutton(sensor_box, text="Envelope", variable=self.use_env_var, command=self._request_redraw_only)
        self.acc_check.pack(side=tk.LEFT, padx=(8, 4))
        self.gyr_check.pack(side=tk.LEFT, padx=4)
        self.env_check.pack(side=tk.LEFT, padx=(12, 4))

        plot_container = ttk.Frame(self.root, padding=(8, 0, 8, 8))
        plot_container.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        self.figure = Figure(figsize=(12, 8), dpi=100)
        self._configure_plot_axes()
        self.canvas = FigureCanvasTkAgg(self.figure, master=plot_container)

        toolbar_frame = ttk.Frame(plot_container)
        toolbar_frame.pack(side=tk.TOP, fill=tk.X)
        self.toolbar = NavigationToolbar2Tk(self.canvas, toolbar_frame, pack_toolbar=False)
        self.toolbar.update()
        self.toolbar.pack(side=tk.LEFT, fill=tk.X)
        self.canvas.get_tk_widget().pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        self.canvas.mpl_connect("scroll_event", self._on_scroll_zoom)
        self.canvas.mpl_connect("button_press_event", self._on_mouse_press)
        self.canvas.mpl_connect("motion_notify_event", self._on_mouse_move)
        self.canvas.mpl_connect("button_release_event", self._on_mouse_release)
        self.canvas.mpl_connect("draw_event", self._on_canvas_draw)

        self._refresh_session_tabs()
        self._update_sensor_controls()
        self._refresh_action_buttons()

    def _configure_plot_axes(self) -> None:
        self.figure.clear()
        if self.show_spectrogram_var.get():
            gs = self.figure.add_gridspec(2, 1, hspace=0.28)
            self.ax_wave = self.figure.add_subplot(gs[0, 0])
            self.ax_spec = self.figure.add_subplot(gs[1, 0], sharex=self.ax_wave)
            self.ax_spec.set_visible(True)
        else:
            gs = self.figure.add_gridspec(2, 1, height_ratios=[1.0, 0.0001], hspace=0.02)
            self.ax_wave = self.figure.add_subplot(gs[0, 0])
            self.ax_spec = self.figure.add_subplot(gs[1, 0], sharex=self.ax_wave)
            self.ax_spec.set_visible(False)

    def toggle_spectrogram_visibility(self) -> None:
        self._append_log(f"Spectrogram {'shown' if self.show_spectrogram_var.get() else 'hidden'}")
        self._configure_plot_axes()
        self._request_redraw_only()

    def _append_log(self, message: str) -> None:
        text = str(message).strip()
        if not text:
            return
        if text == self.last_log_text:
            return
        self.last_log_text = text
        entry = f"[{datetime.now().strftime('%H:%M:%S')}] {text}"
        logger.info(text)
        self.log_messages.append(entry)
        if self._has_live_log_widget():
            should_scroll = self._is_log_scrolled_to_bottom()
            self.log_text.configure(state=tk.NORMAL)
            self.log_text.insert(tk.END, entry + "\n")
            if should_scroll:
                self.log_text.see(tk.END)
            self.log_text.configure(state=tk.DISABLED)

    def _is_log_scrolled_to_bottom(self) -> bool:
        if not self._has_live_log_widget():
            return True
        try:
            _first, last = self.log_text.yview()
            return last >= 0.999
        except tk.TclError:
            return True

    def _has_live_log_widget(self) -> bool:
        if self.log_text is None:
            return False
        try:
            return bool(self.log_text.winfo_exists())
        except tk.TclError:
            self.log_text = None
            self.log_window = None
            return False

    def _set_status_value(self, variable: tk.StringVar, value: str, log_label: Optional[str] = None) -> None:
        if variable.get() == value:
            return
        variable.set(value)
        if log_label is not None:
            self._append_log(f"{log_label}: {value}")

    def _show_error(self, title: str, message: str) -> None:
        self._append_log(f"{title}: {message}")
        messagebox.showerror(title, message)

    def _show_info(self, title: str, message: str) -> None:
        self._append_log(f"{title}: {message}")
        messagebox.showinfo(title, message)

    def _refresh_log_window(self, scroll_to_end: bool = True) -> None:
        if not self._has_live_log_widget():
            return
        try:
            first, last = self.log_text.yview()
            was_at_bottom = last >= 0.999
        except tk.TclError:
            first = 0.0
            was_at_bottom = True
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.delete("1.0", tk.END)
        if self.log_messages:
            self.log_text.insert(tk.END, "\n".join(self.log_messages) + "\n")
        if scroll_to_end or was_at_bottom:
            self.log_text.see(tk.END)
        else:
            self.log_text.yview_moveto(first)
        self.log_text.configure(state=tk.DISABLED)

    def open_log_window(self) -> None:
        if self.log_window is not None:
            try:
                if self.log_window.winfo_exists():
                    self.log_window.lift()
                    self.log_window.focus_force()
                    return
            except Exception:
                pass
        win = tk.Toplevel(self.root)
        self.log_window = win
        win.title("Log")
        win.geometry("980x520")
        main = ttk.Frame(win, padding=10)
        main.pack(fill=tk.BOTH, expand=True)
        status_box = ttk.Frame(main)
        status_box.pack(fill=tk.X, pady=(0, 10))
        for text_var in [
            self.status_var,
            self.exp_status_var,
            self.sensor_var,
            self.capture_status_var,
            self.device_status_var,
            self.record_var,
            self.info_var,
        ]:
            ttk.Label(status_box, textvariable=text_var, wraplength=920, justify=tk.LEFT).pack(anchor="w")
        text_frame = ttk.Frame(main)
        text_frame.pack(fill=tk.BOTH, expand=True)
        scrollbar = ttk.Scrollbar(text_frame, orient=tk.VERTICAL)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text = tk.Text(text_frame, wrap=tk.WORD, state=tk.DISABLED, yscrollcommand=scrollbar.set)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.configure(command=self.log_text.yview)
        self._refresh_log_window(scroll_to_end=True)

        def close_window() -> None:
            self.log_text = None
            self.log_window = None
            win.destroy()

        win.bind(
            "<Destroy>",
            lambda _event: (
                setattr(self, "log_text", None),
                setattr(self, "log_window", None),
            ) if self.log_window is win else None,
        )
        win.protocol("WM_DELETE_WINDOW", close_window)

    def _refresh_connection_window(self) -> None:
        record_sensors, record_video = self._get_record_targets()
        if self.connection_mode_note_var is not None:
            if record_sensors and record_video:
                self.connection_mode_note_var.set("Mode uses both phyphox sensors and iPhone video.")
            elif record_sensors:
                self.connection_mode_note_var.set("Mode uses only phyphox sensors. Video controls are disabled.")
            else:
                self.connection_mode_note_var.set("Mode uses only iPhone video. Sensor controls are disabled.")
        if self.connection_url_entry is not None:
            self.connection_url_entry.configure(state=(tk.NORMAL if record_sensors else tk.DISABLED))
        if self.connection_phyphox_btn is not None:
            self.connection_phyphox_btn.state(["!disabled"] if record_sensors and not self.phyphox.connected else ["disabled"])
        if self.connection_phyphox_disconnect_btn is not None:
            self.connection_phyphox_disconnect_btn.state(["!disabled"] if self.phyphox.connected else ["disabled"])
        if self.connection_iphone_btn is not None:
            self.connection_iphone_btn.state(["!disabled"] if record_video and not self.capture.connecting and not self.capture.connected else ["disabled"])
        if self.connection_iphone_disconnect_btn is not None:
            self.connection_iphone_disconnect_btn.state(["!disabled"] if self.capture.connected else ["disabled"])

    def open_connection_window(self) -> None:
        if self.connection_window is not None:
            try:
                if self.connection_window.winfo_exists():
                    self.connection_window.lift()
                    self.connection_window.focus_force()
                    return
            except Exception:
                pass
        win = tk.Toplevel(self.root)
        self.connection_window = win
        win.title("Connection")
        win.geometry("760x380")
        main = ttk.Frame(win, padding=12)
        main.pack(fill=tk.BOTH, expand=True)
        mode_row = ttk.Frame(main)
        mode_row.pack(fill=tk.X, pady=(0, 10))
        ttk.Label(mode_row, text="Recording mode:").pack(side=tk.LEFT)
        self.connection_mode_combo = ttk.Combobox(
            mode_row,
            textvariable=self.record_mode_var,
            values=["Sensors + Video", "Sensors Only", "Video Only"],
            state="readonly",
            width=18,
        )
        self.connection_mode_combo.pack(side=tk.LEFT, padx=(8, 0))
        self.connection_mode_combo.bind("<<ComboboxSelected>>", self._handle_record_mode_change)
        ttk.Label(main, textvariable=self.connection_mode_note_var, wraplength=700, justify=tk.LEFT).pack(anchor="w", pady=(0, 10))
        url_row = ttk.Frame(main)
        url_row.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(url_row, text="Phyphox URL:").pack(side=tk.LEFT)
        self.connection_url_entry = ttk.Entry(url_row, textvariable=self.base_url_var, width=42)
        self.connection_url_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 0))
        buttons_row = ttk.Frame(main)
        buttons_row.pack(fill=tk.X, pady=(0, 12))
        self.connection_phyphox_btn = ttk.Button(buttons_row, text="Connect Sensors", command=self.start_phyphox_connect_loop)
        self.connection_phyphox_disconnect_btn = ttk.Button(buttons_row, text="Disconnect Sensors", command=self.disconnect_phyphox)
        self.connection_iphone_btn = ttk.Button(buttons_row, text="Connect iPhone", command=self.start_iphone_connect_loop)
        self.connection_iphone_disconnect_btn = ttk.Button(buttons_row, text="Disconnect iPhone", command=self.disconnect_iphone)
        self.connection_phyphox_btn.pack(side=tk.LEFT)
        self.connection_phyphox_disconnect_btn.pack(side=tk.LEFT, padx=(6, 0))
        self.connection_iphone_btn.pack(side=tk.LEFT, padx=(18, 0))
        self.connection_iphone_disconnect_btn.pack(side=tk.LEFT, padx=(6, 0))
        ttk.Separator(main, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=(0, 10))
        for text_var in [
            self.status_var,
            self.exp_status_var,
            self.sensor_var,
            self.capture_status_var,
            self.device_status_var,
            self.record_var,
            self.info_var,
        ]:
            ttk.Label(main, textvariable=text_var, wraplength=640, justify=tk.LEFT).pack(anchor="w", pady=2)
        self._refresh_connection_window()

        def close_window() -> None:
            self.connection_mode_combo = None
            self.connection_url_entry = None
            self.connection_phyphox_btn = None
            self.connection_phyphox_disconnect_btn = None
            self.connection_iphone_btn = None
            self.connection_iphone_disconnect_btn = None
            self.connection_window = None
            win.destroy()

        win.protocol("WM_DELETE_WINDOW", close_window)

    def show_video_window(self) -> None:
        if not self._get_record_targets()[1]:
            self._show_info("Video Playback", "Video Playback is unavailable in Sensors Only mode")
            return
        self._append_log("Video window raised")
        self.video_window.show()

    def start_phyphox_connect_loop(self) -> None:
        if not self._get_record_targets()[0]:
            self._show_info("Connect Sensors", "Sensor connection is disabled in the selected recording mode")
            return
        normalized_url = normalize_url(self.base_url_var.get())
        self.base_url_var.set(normalized_url)
        self._append_log(f"Connect Sensors requested: {normalized_url}")
        self.phyphox.connect_async(normalized_url)
        self._refresh_action_buttons()

    def start_iphone_connect_loop(self) -> None:
        if not self._get_record_targets()[1]:
            self._show_info("Connect iPhone", "Video connection is disabled in the selected recording mode")
            return
        self._append_log("Connect iPhone requested")
        self.capture.connect_async()
        self.video_window.show()
        self._refresh_action_buttons()

    def _get_record_targets(self) -> Tuple[bool, bool]:
        mode = self.record_mode_var.get()
        if mode == "Sensors Only":
            return True, False
        if mode == "Video Only":
            return False, True
        return True, True

    def _set_record_mode_from_targets(self, record_sensors: bool, record_video: bool) -> None:
        if record_sensors and record_video:
            self.record_mode_var.set("Sensors + Video")
        elif record_sensors:
            self.record_mode_var.set("Sensors Only")
        elif record_video:
            self.record_mode_var.set("Video Only")
        else:
            self.record_mode_var.set("Sensors Only")

    def _describe_record_targets(self) -> str:
        record_sensors, record_video = self._get_record_targets()
        if record_sensors and record_video:
            return "sensors+video"
        if record_sensors:
            return "sensors-only"
        if record_video:
            return "video-only"
        return "nothing selected"

    def _handle_record_mode_change(self, event=None) -> None:
        if not self._get_record_targets()[1]:
            self.video_window.hide()
        self._append_log(f"Record mode changed: {self._describe_record_targets()}")
        self._refresh_action_buttons()

    def disconnect_phyphox(self) -> None:
        if self.phyphox.is_measuring:
            self._show_info("Disconnect Sensors", "Stop recording before disconnecting sensors")
            return
        self.phyphox.disconnect()
        self._append_log("Sensors disconnected")
        self._refresh_action_buttons()

    def disconnect_iphone(self) -> None:
        if self.capture.shared_state.recorder.recording:
            self._show_info("Disconnect iPhone", "Stop recording before disconnecting iPhone capture")
            return
        self.capture.stop_capture()
        self.video_window.activate_live_mode()
        self._append_log("iPhone capture disconnected")
        self._refresh_action_buttons()

    def _get_latest_video_duration(self) -> float:
        clips = self.capture.shared_state.recorder.finished_clips
        if not clips:
            return 0.0
        clip = clips[-1]
        if clip.duration_s is not None and clip.duration_s > 0.0:
            return float(clip.duration_s)
        if clip.frame_count > 0 and clip.fps_hint > 0:
            return float(clip.frame_count) / float(max(clip.fps_hint, 1e-6))
        return 0.0

    def _capture_view(self) -> None:
        self.view_locked = True
        self.manual_wave_xlim = self.ax_wave.get_xlim()
        self.manual_wave_ylim = self.ax_wave.get_ylim()
        self.manual_spec_xlim = self.ax_spec.get_xlim()
        self.manual_spec_ylim = self.ax_spec.get_ylim()

    def _restore_view(self) -> None:
        if not self.view_locked:
            return
        if self.manual_wave_xlim is not None:
            self.ax_wave.set_xlim(self.manual_wave_xlim)
        if self.manual_wave_ylim is not None:
            self.ax_wave.set_ylim(self.manual_wave_ylim)
        if self.manual_spec_xlim is not None:
            self.ax_spec.set_xlim(self.manual_spec_xlim)
        if self.manual_spec_ylim is not None:
            self.ax_spec.set_ylim(self.manual_spec_ylim)

    def _reset_view(self) -> None:
        self.view_locked = False
        self.manual_wave_xlim = None
        self.manual_wave_ylim = None
        self.manual_spec_xlim = None
        self.manual_spec_ylim = None
        self.redraw_requested = True
        self._draw()
        self.canvas.draw_idle()
        self._sync_playback_view_range(clamp_cursor=True)

    def _get_playback_duration(self) -> float:
        out = self.phyphox.current_output(self.use_acc_var.get(), self.use_gyr_var.get(), self._get_interpolation_config())
        if out is not None:
            rel_t = out["rel_t"]
            if len(rel_t):
                return float(rel_t[-1])
        return self._get_latest_video_duration()

    def _get_visible_playback_range(self, duration_s: Optional[float] = None) -> Tuple[float, float]:
        total_duration_s = self._get_playback_duration() if duration_s is None else max(float(duration_s), 0.0)
        if total_duration_s <= 0.0:
            return 0.0, 0.0
        if (not self.phyphox.is_measuring) and self.view_locked and self.manual_wave_xlim is not None:
            start_s = max(0.0, min(float(self.manual_wave_xlim[0]), float(self.manual_wave_xlim[1])))
            end_s = min(total_duration_s, max(float(self.manual_wave_xlim[0]), float(self.manual_wave_xlim[1])))
            if end_s > start_s:
                return start_s, end_s
        return 0.0, total_duration_s

    def _sync_playback_view_range(self, clamp_cursor: bool = False) -> None:
        if not self.video_window.is_playback_mode():
            return
        duration_s = self._get_playback_duration()
        visible_range_s = self._get_visible_playback_range(duration_s)
        cursor_time_s = self.playback_cursor_time
        if clamp_cursor and cursor_time_s is not None:
            cursor_time_s = min(max(cursor_time_s, visible_range_s[0]), visible_range_s[1])
            self.playback_cursor_time = cursor_time_s
        self.video_window.sync_from_app(cursor_time_s, duration_s, visible_range_s)

    def _handle_video_cursor_change(self, cursor_time_s: float, source: str) -> None:
        self.set_playback_cursor(cursor_time_s, sync_video=(source != "video_window"))

    def set_playback_cursor(self, cursor_time_s: Optional[float], sync_video: bool = True) -> None:
        duration_s = self._get_playback_duration()
        visible_range_s = self._get_visible_playback_range(duration_s)
        if cursor_time_s is None:
            self.playback_cursor_time = None
            if sync_video:
                self.video_window.sync_from_app(None, duration_s, visible_range_s)
            self._update_playback_cursor_artists()
            self.canvas.draw_idle()
        else:
            cursor = min(max(float(cursor_time_s), visible_range_s[0]), visible_range_s[1])
            self.playback_cursor_time = cursor
            if sync_video:
                self.video_window.sync_from_app(cursor, duration_s, visible_range_s)
            self._update_playback_cursor_artists()
            self.canvas.draw_idle()

    def _clear_playback_cursor_artists(self) -> None:
        for attr_name in ("_wave_cursor_artist", "_spec_cursor_artist"):
            artist = getattr(self, attr_name, None)
            if artist is None:
                continue
            try:
                artist.remove()
            except Exception:
                pass
            setattr(self, attr_name, None)

    def _update_playback_cursor_artists(self) -> None:
        if not hasattr(self, "ax_wave") or self.ax_wave is None:
            return
        if (
            self.playback_cursor_time is None
            or self.phyphox.is_measuring
            or self._playback_data_bounds is None
        ):
            self._clear_playback_cursor_artists()
            return
        bounds_start, bounds_end = self._playback_data_bounds
        cursor_x = min(max(self.playback_cursor_time, bounds_start), bounds_end)
        if self._wave_cursor_artist is None:
            self._wave_cursor_artist = self.ax_wave.axvline(cursor_x, color="red", linewidth=1.0, alpha=0.9)
        else:
            self._wave_cursor_artist.set_xdata([cursor_x, cursor_x])
        if self.show_spectrogram_var.get():
            if self._spec_cursor_artist is None:
                self._spec_cursor_artist = self.ax_spec.axvline(cursor_x, color="red", linewidth=1.0, alpha=0.9)
            else:
                self._spec_cursor_artist.set_xdata([cursor_x, cursor_x])
        elif self._spec_cursor_artist is not None:
            try:
                self._spec_cursor_artist.remove()
            except Exception:
                pass
            self._spec_cursor_artist = None

    def _on_scroll_zoom(self, event) -> None:
        if self.phyphox.is_measuring:
            return
        if event is None or event.inaxes not in (self.ax_wave, self.ax_spec):
            return
        ax = event.inaxes
        xdata = event.xdata
        ydata = event.ydata
        if xdata is None:
            return
        scale = 1 / 1.2 if event.button == "up" else 1.2
        x0, x1 = self.ax_wave.get_xlim()
        half = (x1 - x0) * 0.5 * scale
        relx = 0.5 if x1 == x0 else (xdata - x0) / (x1 - x0)
        nx0 = xdata - 2 * half * relx
        nx1 = xdata + 2 * half * (1 - relx)
        self.ax_wave.set_xlim(nx0, nx1)
        self.ax_spec.set_xlim(nx0, nx1)
        if ydata is not None:
            y0, y1 = ax.get_ylim()
            halfy = (y1 - y0) * 0.5 * scale
            rely = 0.5 if y1 == y0 else (ydata - y0) / (y1 - y0)
            ny0 = ydata - 2 * halfy * rely
            ny1 = ydata + 2 * halfy * (1 - rely)
            ax.set_ylim(ny0, ny1)
        self._capture_view()
        self.redraw_requested = True
        self._draw()
        self.canvas.draw_idle()
        self._sync_playback_view_range(clamp_cursor=True)

    def _on_mouse_press(self, event) -> None:
        if self.phyphox.is_measuring:
            return
        if event is None or event.inaxes not in (self.ax_wave, self.ax_spec):
            return
        if getattr(event, "dblclick", False) and event.button == 1 and event.xdata is not None and self.video_window.is_playback_mode():
            self.set_playback_cursor(float(event.xdata))
            return
        if event.button != 1:
            return
        if getattr(self.toolbar, "mode", ""):
            return
        if event.xdata is None or event.ydata is None:
            return
        self._drag_active = True
        self._drag_ax = event.inaxes
        self._drag_last = (event.xdata, event.ydata)

    def _on_mouse_move(self, event) -> None:
        if not self._drag_active or self.phyphox.is_measuring:
            return
        if event is None or event.inaxes is None or self._drag_ax is None:
            return
        if event.xdata is None or event.ydata is None or self._drag_last is None:
            return
        last_x, last_y = self._drag_last
        dx = event.xdata - last_x
        dy = event.ydata - last_y
        wx0, wx1 = self.ax_wave.get_xlim()
        sx0, sx1 = self.ax_spec.get_xlim()
        self.ax_wave.set_xlim(wx0 - dx, wx1 - dx)
        self.ax_spec.set_xlim(sx0 - dx, sx1 - dx)
        y0, y1 = self._drag_ax.get_ylim()
        self._drag_ax.set_ylim(y0 - dy, y1 - dy)
        self._drag_last = (event.xdata, event.ydata)
        self._capture_view()
        self.canvas.draw_idle()

    def _on_mouse_release(self, event) -> None:
        if not self._drag_active:
            return
        self._drag_active = False
        self._drag_ax = None
        self._drag_last = None
        if not self.phyphox.is_measuring:
            self.redraw_requested = True
            self._draw()
            self.canvas.draw_idle()
            self._sync_playback_view_range(clamp_cursor=True)

    def _on_canvas_draw(self, event) -> None:
        if self.phyphox.is_measuring:
            self.view_locked = False
            self.manual_wave_xlim = None
            self.manual_wave_ylim = None
            self.manual_spec_xlim = None
            self.manual_spec_ylim = None
            return
        out = self.phyphox.current_output(self.use_acc_var.get(), self.use_gyr_var.get(), self._get_interpolation_config())
        if out is None:
            return
        rel_t = out["rel_t"]
        if len(rel_t) == 0:
            return
        full_start = float(rel_t[0])
        full_end = float(rel_t[-1])
        tolerance = max((full_end - full_start) * 0.001, 1e-6)

        def is_full_range(xlim: Tuple[float, float]) -> bool:
            x0 = min(float(xlim[0]), float(xlim[1]))
            x1 = max(float(xlim[0]), float(xlim[1]))
            return abs(x0 - full_start) <= tolerance and abs(x1 - full_end) <= tolerance

        wave_xlim = self.ax_wave.get_xlim()
        spec_xlim = self.ax_spec.get_xlim()
        if is_full_range(wave_xlim) and is_full_range(spec_xlim):
            self.view_locked = False
            self.manual_wave_xlim = None
            self.manual_wave_ylim = None
            self.manual_spec_xlim = None
            self.manual_spec_ylim = None
            return
        self.view_locked = True
        self.manual_wave_xlim = wave_xlim
        self.manual_wave_ylim = self.ax_wave.get_ylim()
        self.manual_spec_xlim = spec_xlim
        self.manual_spec_ylim = self.ax_spec.get_ylim()

    def _apply_default_spectrogram_settings(self) -> None:
        self.spec_auto_var.set(False)
        self.spec_window_var.set("hann")
        self.spec_display_interp_var.set("nearest")
        self.spec_fragment_mode_var.set("visible_when_paused")
        self.spec_last_seconds_var.set(1.0)
        self.spec_max_freq_var.set(150.0)
        self.spec_clean_var.set("median_clip")
        self.spec_nperseg_var.set(256)
        self.spec_noverlap_ratio_var.set(0.92)
        self.spec_nfft_mult_var.set(4)
        self._request_redraw_only()

    def _apply_auto_spectrogram_settings(self) -> None:
        self.spec_auto_var.set(True)
        self.spec_window_var.set("hann")
        self.spec_display_interp_var.set("nearest")
        self.spec_fragment_mode_var.set("visible_when_paused")
        self.spec_last_seconds_var.set(1.0)
        self.spec_max_freq_var.set(150.0)
        self.spec_clean_var.set("median_clip")
        self._request_redraw_only()

    def _open_spectrogram_settings(self) -> None:
        if self.spec_settings_window is not None:
            try:
                if self.spec_settings_window.winfo_exists():
                    self.spec_settings_window.lift()
                    self.spec_settings_window.focus_force()
                    return
            except Exception:
                pass
        win = tk.Toplevel(self.root)
        self.spec_settings_window = win
        win.title("Spectrogram settings")
        win.geometry("520x520")
        main = ttk.Frame(win, padding=12)
        main.pack(fill=tk.BOTH, expand=True)
        row = 0
        ttk.Checkbutton(main, text="Auto parameters", variable=self.spec_auto_var, command=self._request_redraw_only).grid(row=row, column=0, sticky="w")
        ttk.Button(main, text="Auto", command=self._apply_auto_spectrogram_settings).grid(row=row, column=1, sticky="ew", padx=6)
        ttk.Button(main, text="Default", command=self._apply_default_spectrogram_settings).grid(row=row, column=2, sticky="ew")
        row += 1
        ttk.Label(main, text="Window").grid(row=row, column=0, sticky="w", pady=(10, 2))
        ttk.Combobox(main, textvariable=self.spec_window_var, values=["hann", "blackman", "hamming", "boxcar"], state="readonly").grid(row=row, column=1, columnspan=2, sticky="ew")
        row += 1
        ttk.Label(main, text="Display interpolation").grid(row=row, column=0, sticky="w", pady=(10, 2))
        ttk.Combobox(main, textvariable=self.spec_display_interp_var, values=["nearest", "bilinear", "bicubic"], state="readonly").grid(row=row, column=1, columnspan=2, sticky="ew")
        row += 1
        ttk.Label(main, text="Fragment").grid(row=row, column=0, sticky="w", pady=(10, 2))
        ttk.Combobox(main, textvariable=self.spec_fragment_mode_var, values=["visible_when_paused", "full_signal", "last_seconds"], state="readonly").grid(row=row, column=1, columnspan=2, sticky="ew")
        row += 1
        ttk.Label(main, text="Last seconds").grid(row=row, column=0, sticky="w", pady=(10, 2))
        ttk.Spinbox(main, from_=0.1, to=10.0, increment=0.1, textvariable=self.spec_last_seconds_var).grid(row=row, column=1, columnspan=2, sticky="ew")
        row += 1
        ttk.Label(main, text="Max frequency, Hz").grid(row=row, column=0, sticky="w", pady=(10, 2))
        ttk.Spinbox(main, from_=20.0, to=500.0, increment=5.0, textvariable=self.spec_max_freq_var).grid(row=row, column=1, columnspan=2, sticky="ew")
        row += 1
        ttk.Label(main, text="Cleanup").grid(row=row, column=0, sticky="w", pady=(10, 2))
        ttk.Combobox(main, textvariable=self.spec_clean_var, values=["median_clip", "clip_only", "none"], state="readonly").grid(row=row, column=1, columnspan=2, sticky="ew")
        row += 1
        ttk.Label(main, text="nperseg").grid(row=row, column=0, sticky="w", pady=(10, 2))
        ttk.Spinbox(main, from_=32, to=4096, increment=32, textvariable=self.spec_nperseg_var).grid(row=row, column=1, columnspan=2, sticky="ew")
        row += 1
        ttk.Label(main, text="Overlap ratio").grid(row=row, column=0, sticky="w", pady=(10, 2))
        ttk.Spinbox(main, from_=0.0, to=0.99, increment=0.01, textvariable=self.spec_noverlap_ratio_var).grid(row=row, column=1, columnspan=2, sticky="ew")
        row += 1
        ttk.Label(main, text="nfft multiplier").grid(row=row, column=0, sticky="w", pady=(10, 2))
        ttk.Spinbox(main, from_=1, to=16, increment=1, textvariable=self.spec_nfft_mult_var).grid(row=row, column=1, columnspan=2, sticky="ew")
        row += 1
        ttk.Button(main, text="Apply", command=self._request_redraw_only).grid(row=row, column=0, columnspan=3, sticky="ew", pady=(16, 0))
        for column in range(3):
            main.columnconfigure(column, weight=1)

        def close_window() -> None:
            self.spec_settings_window = None
            win.destroy()

        win.protocol("WM_DELETE_WINDOW", close_window)

    def _get_interpolation_config(self) -> InterpolationConfig:
        return InterpolationConfig(
            enabled=bool(self.interp_enabled_var.get()),
            target_samples_on_window=max(int(self.interp_target_samples_on_window_var.get()), 8),
            window_ms=max(float(self.interp_window_ms_var.get()), 10.0),
            overlap_ratio=min(max(float(self.interp_overlap_ratio_var.get()), 0.0), 0.95),
            window_kind=self.interp_window_kind_var.get(),
            method=self.interp_method_var.get(),
            poly_order=max(1, int(self.interp_poly_order_var.get())),
            post_smoothing=self.interp_post_smoothing_var.get(),
            apply_to_export=bool(self.interp_apply_export_var.get()),
        )

    def _apply_interpolation_defaults(self) -> None:
        self.interp_enabled_var.set(False)
        self.interp_target_samples_on_window_var.set(120)
        self.interp_window_ms_var.set(120.0)
        self.interp_overlap_ratio_var.set(0.6)
        self.interp_window_kind_var.set("hann")
        self.interp_method_var.set("lagrange")
        self.interp_poly_order_var.set(4)
        self.interp_post_smoothing_var.set("none")
        self.interp_apply_export_var.set(False)
        self._refresh_interpolation_source_stats()
        self._request_redraw_only()

    def _refresh_interpolation_source_stats(self) -> None:
        out = self.phyphox.current_output(self.use_acc_var.get(), self.use_gyr_var.get(), None)
        if out is None:
            self.interp_source_stats_var.set("Source signal stats: waiting for data")
            return
        times = np.asarray(out["times"], dtype=float)
        if times.size < 4:
            self.interp_source_stats_var.set("Source signal stats: not enough samples yet")
            return
        dt = np.diff(times)
        dt = dt[np.isfinite(dt) & (dt > 1e-6)]
        if dt.size == 0:
            self.interp_source_stats_var.set("Source signal stats: invalid timestamps")
            return
        fs_values = 1.0 / dt
        window_s = max(float(self.interp_window_ms_var.get()), 10.0) / 1000.0
        valid_start_idx = np.nonzero(times <= (times[-1] - window_s))[0]
        if valid_start_idx.size:
            window_end_idx = np.searchsorted(times, times[valid_start_idx] + window_s, side="right")
            source_points = (window_end_idx - valid_start_idx).astype(float)
        else:
            source_points = fs_values * window_s
        target_samples = max(int(self.interp_target_samples_on_window_var.get()), 8)
        derived_target_fs = min(max(target_samples / max(window_s, 1e-6), float(np.median(fs_values))), 1000.0)
        self.interp_source_stats_var.set(
            "\n".join(
                [
                    f"Target: {target_samples} samples/window ({derived_target_fs:.1f} Hz derived)",
                    (
                        "Source frequency, Hz  "
                        f"min={np.min(fs_values):.2f}  mean={np.mean(fs_values):.2f}  "
                        f"median={np.median(fs_values):.2f}  max={np.max(fs_values):.2f}"
                    ),
                    (
                        "Source samples/window  "
                        f"min={np.min(source_points):.2f}  mean={np.mean(source_points):.2f}  "
                        f"median={np.median(source_points):.2f}  max={np.max(source_points):.2f}"
                    ),
                ]
            )
        )

    def _open_interpolation_settings(self) -> None:
        if self.interp_settings_window is not None:
            try:
                if self.interp_settings_window.winfo_exists():
                    self._refresh_interpolation_source_stats()
                    self.interp_settings_window.lift()
                    self.interp_settings_window.focus_force()
                    return
            except Exception:
                pass
        win = tk.Toplevel(self.root)
        self.interp_settings_window = win
        win.title("Interpolation settings")
        win.geometry("560x420")
        main = ttk.Frame(win, padding=12)
        main.pack(fill=tk.BOTH, expand=True)
        row = 0
        ttk.Checkbutton(main, text="Enable interpolation", variable=self.interp_enabled_var, command=self._request_redraw_only).grid(row=row, column=0, columnspan=2, sticky="w")
        ttk.Button(main, text="Default", command=self._apply_interpolation_defaults).grid(row=row, column=2, sticky="ew")
        row += 1
        ttk.Label(main, text="Target samples on window").grid(row=row, column=0, sticky="w", pady=(10, 2))
        ttk.Entry(main, textvariable=self.interp_target_samples_on_window_var).grid(row=row, column=1, columnspan=2, sticky="ew")
        row += 1
        ttk.Label(main, text="Window length, ms").grid(row=row, column=0, sticky="w", pady=(10, 2))
        ttk.Spinbox(main, from_=10.0, to=1000.0, increment=10.0, textvariable=self.interp_window_ms_var).grid(row=row, column=1, columnspan=2, sticky="ew")
        row += 1
        ttk.Label(main, text="Overlap ratio").grid(row=row, column=0, sticky="w", pady=(10, 2))
        ttk.Spinbox(main, from_=0.0, to=0.95, increment=0.05, textvariable=self.interp_overlap_ratio_var).grid(row=row, column=1, columnspan=2, sticky="ew")
        row += 1
        ttk.Label(main, text="Window function").grid(row=row, column=0, sticky="w", pady=(10, 2))
        ttk.Combobox(main, textvariable=self.interp_window_kind_var, values=["hann", "triangular"], state="readonly").grid(row=row, column=1, columnspan=2, sticky="ew")
        row += 1
        ttk.Label(main, text="Interpolation method").grid(row=row, column=0, sticky="w", pady=(10, 2))
        ttk.Combobox(main, textvariable=self.interp_method_var, values=["lagrange", "pchip", "akima", "linear"], state="readonly").grid(row=row, column=1, columnspan=2, sticky="ew")
        row += 1
        ttk.Label(main, text="Polynomial order").grid(row=row, column=0, sticky="w", pady=(10, 2))
        ttk.Spinbox(main, from_=1, to=8, increment=1, textvariable=self.interp_poly_order_var).grid(row=row, column=1, columnspan=2, sticky="ew")
        row += 1
        ttk.Label(main, text="Post smoothing").grid(row=row, column=0, sticky="w", pady=(10, 2))
        ttk.Combobox(main, textvariable=self.interp_post_smoothing_var, values=["none", "savgol"], state="readonly").grid(row=row, column=1, columnspan=2, sticky="ew")
        row += 1
        ttk.Label(main, textvariable=self.interp_source_stats_var, justify="left", wraplength=500).grid(row=row, column=0, columnspan=3, sticky="ew", pady=(14, 4))
        row += 1
        ttk.Checkbutton(main, text="Apply to export", variable=self.interp_apply_export_var).grid(row=row, column=0, columnspan=3, sticky="w", pady=(12, 0))
        row += 1
        ttk.Button(main, text="Apply", command=self._request_redraw_only).grid(row=row, column=0, columnspan=3, sticky="ew", pady=(16, 0))
        for column in range(3):
            main.columnconfigure(column, weight=1)
        self._refresh_interpolation_source_stats()

        def close_window() -> None:
            self.interp_settings_window = None
            win.destroy()

        win.protocol("WM_DELETE_WINDOW", close_window)

    def _prepare_spectrogram(self, rel_t: np.ndarray, sig: np.ndarray, fs: float) -> Tuple[np.ndarray, float, float, np.ndarray, np.ndarray]:
        spec_sig = sig
        spec_t0 = float(rel_t[0]) if len(rel_t) else 0.0
        mode = self.spec_fragment_mode_var.get()
        if mode == "last_seconds":
            last_sec = max(0.05, float(self.spec_last_seconds_var.get()))
            mask_t = rel_t >= (rel_t[-1] - last_sec)
            if np.count_nonzero(mask_t) >= 32:
                spec_sig = sig[mask_t]
                spec_rel_t = rel_t[mask_t]
                spec_t0 = float(spec_rel_t[0])
        elif mode == "visible_when_paused":
            if (not self.phyphox.is_measuring) and self.view_locked and self.manual_wave_xlim is not None:
                x0, x1 = self.manual_wave_xlim
                mask_t = (rel_t >= min(x0, x1)) & (rel_t <= max(x0, x1))
                if np.count_nonzero(mask_t) >= 32:
                    spec_sig = sig[mask_t]
                    spec_rel_t = rel_t[mask_t]
                    spec_t0 = float(spec_rel_t[0])
        if bool(self.spec_auto_var.get()):
            nperseg = min(1024, max(192, int(fs * 0.30)))
            nperseg = min(nperseg, max(32, len(spec_sig)))
            noverlap = min(int(nperseg * 0.95), nperseg - 1)
            nfft = min(8192, max(1024, 1 << int(np.ceil(np.log2(max(nperseg * 4, 64))))))
        else:
            nperseg = min(max(32, int(self.spec_nperseg_var.get())), max(32, len(spec_sig)))
            overlap_ratio = min(0.99, max(0.0, float(self.spec_noverlap_ratio_var.get())))
            noverlap = min(int(nperseg * overlap_ratio), nperseg - 1)
            mult = max(1, int(self.spec_nfft_mult_var.get()))
            target = max(nperseg, nperseg * mult)
            nfft = 1 << int(np.ceil(np.log2(target)))
        f, tt, sxx = scipy_signal.spectrogram(
            spec_sig,
            fs=fs,
            window=self.spec_window_var.get(),
            nperseg=nperseg,
            noverlap=noverlap,
            nfft=nfft,
            detrend=False,
            scaling="density",
            mode="psd",
        )
        max_f = min(float(self.spec_max_freq_var.get()), fs * 0.5)
        mask = f <= max_f
        f = f[mask]
        sxx = sxx[mask, :]
        sxx_db = 10.0 * np.log10(sxx + 1e-12)
        clean_mode = self.spec_clean_var.get()
        if clean_mode == "median_clip":
            sxx_db = clean_spectrogram_db(sxx_db)
        elif clean_mode == "clip_only":
            lo = float(np.percentile(sxx_db, 10))
            hi = float(np.percentile(sxx_db, 99.5))
            if np.isfinite(lo) and np.isfinite(hi) and hi > lo:
                sxx_db = np.clip(sxx_db, lo, hi)
        return sxx_db, spec_t0, float(rel_t[-1]) if len(rel_t) else 0.0, tt, f

    def _draw_empty_axes(self) -> None:
        self.ax_wave.clear()
        self.ax_spec.clear()
        self.ax_wave.set_title("Filtered principal signal (incremental)")
        self.ax_wave.set_ylabel("Normalized amplitude")
        if self.show_spectrogram_var.get():
            self.ax_spec.set_title("Spectrogram")
            self.ax_spec.set_ylabel("Hz")
            self.ax_spec.set_xlabel("Time, s")
        self.canvas.draw_idle()

    def _update_sensor_controls(self) -> None:
        has_acc = self.phyphox.has_acc()
        has_gyr = self.phyphox.has_gyr()
        self.acc_check.state(["!disabled"] if has_acc else ["disabled"])
        self.gyr_check.state(["!disabled"] if has_gyr else ["disabled"])
        if not has_acc:
            self.use_acc_var.set(False)
        if not has_gyr:
            self.use_gyr_var.set(False)
        if has_acc and not has_gyr:
            self.use_acc_var.set(True)
        if has_gyr and not has_acc:
            self.use_gyr_var.set(True)
        if not self.use_acc_var.get() and not self.use_gyr_var.get():
            if has_acc:
                self.use_acc_var.set(True)
            elif has_gyr:
                self.use_gyr_var.set(True)

    def _apply_sensor_toggle(self) -> None:
        if not self.use_acc_var.get() and not self.use_gyr_var.get():
            if self.phyphox.has_acc():
                self.use_acc_var.set(True)
            elif self.phyphox.has_gyr():
                self.use_gyr_var.set(True)
        self._refresh_action_buttons()
        self._request_redraw_only()

    def _has_any_data(self) -> bool:
        return self.phyphox.has_any_data() or self.capture.has_finished_recording()

    def _refresh_action_buttons(self) -> None:
        record_sensors, record_video = self._get_record_targets()
        ready_to_record = (
            (record_sensors or record_video)
            and (not record_sensors or self.phyphox.connected)
            and (not record_video or (self.capture.connected and self.capture.shared_state.last_frame_shape is not None))
        )
        is_recording = self.phyphox.is_measuring or self.capture.shared_state.recorder.recording
        has_data = self._has_any_data()
        self.play_btn.state(["!disabled"] if ready_to_record and not is_recording else ["disabled"])
        self.pause_btn.state(["!disabled"] if is_recording else ["disabled"])
        self.clear_btn.state(["!disabled"] if has_data else ["disabled"])
        self.compare_btn.state(["!disabled"] if len(self.app_sessions) > 1 else ["disabled"])
        self._set_menu_entry_state(self.file_menu, "Save Session", has_data)
        self._set_menu_entry_state(self.file_menu, "Save Graphs", self.phyphox.has_any_data())
        self._set_menu_entry_state(self.file_menu, "Save Video", self.capture.has_finished_recording() and not is_recording)
        self._set_menu_entry_state(self.connection_menu, "Connect Sensors", record_sensors and not self.phyphox.connected)
        self._set_menu_entry_state(self.connection_menu, "Disconnect Sensors", self.phyphox.connected)
        self._set_menu_entry_state(self.connection_menu, "Connect iPhone", record_video and not self.capture.connecting and not self.capture.connected)
        self._set_menu_entry_state(self.connection_menu, "Disconnect iPhone", self.capture.connected)
        self._set_menu_entry_state(self.view_menu, "Show Video Window", record_video)
        self._refresh_connection_window()

    def _request_redraw_only(self) -> None:
        self.redraw_requested = True
        self._draw()
        if self.interp_settings_window is not None:
            try:
                if self.interp_settings_window.winfo_exists():
                    self._refresh_interpolation_source_stats()
            except Exception:
                pass
        self._refresh_compare_window()
        self.canvas.draw_idle()

    def _draw(self) -> None:
        self.redraw_requested = False
        self.phyphox.update_processing(self.use_acc_var.get(), self.use_gyr_var.get())
        self.ax_wave.clear()
        self.ax_spec.clear()
        self._wave_cursor_artist = None
        self._spec_cursor_artist = None
        self._playback_data_bounds = None
        out = self.phyphox.current_output(self.use_acc_var.get(), self.use_gyr_var.get(), self._get_interpolation_config())
        if out is None:
            self.ax_wave.set_title("Filtered principal signal (incremental): waiting for data")
            self.ax_wave.set_ylabel("Normalized amplitude")
            if self.show_spectrogram_var.get():
                self.ax_spec.set_title("Spectrogram")
                self.ax_spec.set_ylabel("Hz")
                self.ax_spec.set_xlabel("Time, s")
            return
        rel_t = out["rel_t"]
        sig = out["signal"]
        fs = out["fs"]
        if len(rel_t):
            self._playback_data_bounds = (float(rel_t[0]), float(rel_t[-1]))
        if self.use_env_var.get():
            sig = compute_envelope(sig, fs)
        self.ax_wave.plot(rel_t, sig, linewidth=0.9)
        wave_title = f"Envelope: {out['label']}" if self.use_env_var.get() else f"Filtered principal signal (incremental): {out['label']}"
        self.ax_wave.set_title(wave_title)
        self.ax_wave.set_ylabel("Normalized amplitude")
        if self.show_spectrogram_var.get():
            sxx_db, spec_t0, _spec_t1, tt, f = self._prepare_spectrogram(rel_t, sig, fs)
            if tt.size and f.size:
                self.ax_spec.imshow(
                    sxx_db,
                    origin="lower",
                    aspect="auto",
                    interpolation=self.spec_display_interp_var.get(),
                    extent=[spec_t0 + tt[0], spec_t0 + tt[-1], f[0], f[-1]],
                )
            self.ax_spec.set_title("Spectrogram")
            self.ax_spec.set_ylabel("Hz")
            self.ax_spec.set_xlabel("Time, s")
        self._update_playback_cursor_artists()
        if self.phyphox.is_measuring:
            self.ax_wave.relim()
            self.ax_wave.autoscale_view()
            if self.show_spectrogram_var.get():
                self.ax_spec.relim()
                self.ax_spec.autoscale_view()
                self.ax_spec.set_ylim(0, min(150.0, fs * 0.5))
            self.view_locked = False
            self.manual_wave_xlim = None
            self.manual_wave_ylim = None
            self.manual_spec_xlim = None
            self.manual_spec_ylim = None
        elif self.view_locked:
            self._restore_view()

    def _update_status_labels(self) -> None:
        self._set_status_value(
            self.status_var,
            f"Phyphox: {'connected' if self.phyphox.connected else 'disconnected'} | {self.phyphox.status_text}",
            "Phyphox",
        )
        self._set_status_value(self.exp_status_var, self.phyphox.experiment_status, "Experiment")
        self._set_status_value(self.sensor_var, self.phyphox.sensor_status, "Sensors")
        capture_status = self.capture.status_text
        if self.capture.connected:
            if self.capture.shared_state.last_frame_shape is None:
                capture_status = "iPhone capture: connected; waiting for video frames"
            else:
                capture_status = "iPhone capture: receiving video"
        self._set_status_value(self.capture_status_var, capture_status, "Capture")
        self._set_status_value(self.device_status_var, self.capture.device_text, "Device")
        recording_state = "running" if (self.phyphox.is_measuring or self.capture.shared_state.recorder.recording) else "stopped"
        self._set_status_value(self.record_var, f"Recording: {recording_state} | mode={self._describe_record_targets()}", "Recording")
        info_parts = [self.phyphox.info_text]
        if self.capture.shared_state.last_frame_shape is not None:
            height, width = self.capture.shared_state.last_frame_shape[:2]
            info_parts.append(
                f"Capture {width}x{height} | "
                f"input={self.capture.shared_state.last_fps:.1f} fps | "
                f"preview={self.video_window.live_preview_fps:.1f} fps"
            )
        elif self.capture.connected:
            info_parts.append("Capture waiting for video frames")
        info_parts.append(self.video_window.offset_summary_var.get())
        self._set_status_value(self.info_var, " | ".join(part for part in info_parts if part))
        self.video_window.set_capture_info(capture_status, self.capture.device_text)

    def _schedule_update(self) -> None:
        try:
            if self.closed:
                return
            self._update_status_labels()
            self._update_sensor_controls()
            if self.phyphox.is_measuring:
                self._draw()
                self.canvas.draw_idle()
            elif self.redraw_requested:
                self._draw()
                self.canvas.draw_idle()
            if self.video_window.is_playback_mode():
                self._sync_playback_view_range(clamp_cursor=False)
            self._refresh_action_buttons()
            if self.interp_settings_window is not None and self.interp_settings_window.winfo_exists():
                self._refresh_interpolation_source_stats()
        finally:
            if not self.closed:
                self.root.after(250, self._schedule_update)

    def cmd_play(self) -> None:
        record_sensors, record_video = self._get_record_targets()
        if not record_sensors and not record_video:
            self._show_info("Play", "Choose at least one recording target: Sensors or Video")
            return
        if self._current_state_has_data():
            self._show_info(
                "Play",
                "Current session already has data. Create a new session or clear the current one before recording.",
            )
            return
        if record_sensors and not self.phyphox.connected:
            self._show_info("Play", "Phyphox is not connected yet")
            return
        if record_video and not self.capture.connected:
            self._show_info("Play", "iPhone capture is not connected yet")
            return
        if record_video and self.capture.shared_state.last_frame_shape is None:
            self._show_info("Play", "No iPhone frame yet, video recording cannot start")
            return
        started_video = False
        started_sensors = False
        try:
            self.playback_cursor_time = None
            self.video_window.activate_live_mode()
            self.session_controller.start_session()
            if record_sensors:
                # Always start sensor recording from a clean remote phyphox buffer,
                # otherwise a new app session can inherit old samples from the device.
                self.phyphox.clear_measurement()
            if record_video:
                video_start_ts = time.time()
                clip = self.capture.start_recording(start_request_wall_ts=video_start_ts)
                self.session_controller.mark_video_start_request(video_start_ts, clip.clip_id)
                started_video = True
            if record_sensors:
                sensor_start_ts = time.time()
                self.session_controller.mark_sensor_start_request(sensor_start_ts)
                self.phyphox.start_measurement()
                started_sensors = True
            self.redraw_requested = True
            self._append_log(f"Recording started ({self._describe_record_targets()})")
            self._refresh_action_buttons()
            if record_video:
                self.video_window.show()
        except Exception as exc:
            if started_video and self.capture.shared_state.recorder.recording:
                self.capture.stop_recording(stop_request_wall_ts=time.time())
            if started_sensors and self.phyphox.is_measuring:
                try:
                    self.phyphox.stop_measurement()
                except Exception:
                    pass
            self.session_controller.finish_session()
            self._show_error("Play failed", str(exc))

    def cmd_stop(self) -> None:
        errors: List[str] = []
        try:
            if self.phyphox.is_measuring:
                sensor_stop_ts = time.time()
                self.session_controller.mark_sensor_stop_request(sensor_stop_ts)
                self.phyphox.stop_measurement()
        except Exception as exc:
            errors.append(f"phyphox: {exc}")
        try:
            if self.capture.shared_state.recorder.recording:
                video_stop_ts = time.time()
                self.session_controller.mark_video_stop_request(video_stop_ts)
                self.capture.stop_recording(stop_request_wall_ts=video_stop_ts)
        except Exception as exc:
            errors.append(f"video: {exc}")
        finished_session = self.session_controller.finish_session()
        if not errors:
            session_has_video = finished_session is not None and finished_session.video_clip_id is not None
            session_has_sensor = finished_session is not None and finished_session.first_sensor_sample_time_s is not None
            duration_s = self._get_playback_duration() if session_has_sensor else self._get_latest_video_duration()
            if session_has_video and duration_s > 0.0:
                self.video_window.activate_playback_mode(duration_s)
                self.set_playback_cursor(0.0)
            else:
                self.playback_cursor_time = None
                self.video_window.activate_live_mode()
        self.redraw_requested = True
        if not errors:
            self._append_log("Recording stopped")
        self._refresh_action_buttons()
        if errors:
            self._show_error("Stop failed", "\n".join(errors))

    def cmd_clear(self) -> None:
        errors: List[str] = []
        try:
            current_session = self._get_active_app_session()
            self._clear_local_session_state()
            if current_session is not None:
                current_session.has_data = False
                self._clear_session_snapshot_dir(current_session)
            self._append_log("Recording cleared")
            self._refresh_session_tabs()
        except Exception as exc:
            errors.append(f"local state: {exc}")
        self._request_redraw_only()
        if errors:
            self._show_error("Clear failed", "\n".join(errors))

    def _build_export_meta(self, copied_recordings: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        record_sensors, record_video = self._get_record_targets()
        active_session = self._get_active_app_session()
        return {
            "sync_metadata": self.session_controller.export_metadata(),
            "recordings": copied_recordings or [],
            "playback_settings": self.video_window.export_settings(),
            "recording_targets": {"sensors": record_sensors, "video": record_video},
            "interpolation_settings": self._get_interpolation_config().to_dict(),
            "session_title": None if active_session is None else active_session.title,
            "archive_format": {"type": "zip", "version": 1},
        }

    def _export_interpolated_signal(self, out_dir: Path) -> None:
        config = self._get_interpolation_config()
        if not config.enabled or not config.apply_to_export:
            return
        out = self.phyphox.current_output(self.use_acc_var.get(), self.use_gyr_var.get(), config)
        if out is None:
            return
        interp_meta = out.get("meta", {}).get("interpolation", {})
        if not interp_meta.get("applied", False):
            return
        write_csv_rows(
            out_dir / "derived_signal_upsampled.csv",
            ["time_s", "rel_time_s", "signal"],
            zip(out["times"].tolist(), out["rel_t"].tolist(), out["signal"].tolist()),
        )

    def _clear_loaded_session_tempdir(self) -> None:
        if self.loaded_session_tempdir is None:
            return
        try:
            shutil.rmtree(self.loaded_session_tempdir, ignore_errors=True)
        finally:
            self.loaded_session_tempdir = None

    def _clear_local_session_state(self, clear_loaded_archive: bool = True, remove_video_files: bool = True) -> None:
        if self.capture.shared_state.recorder.recording:
            self.capture.stop_recording(stop_request_wall_ts=time.time())
        self.video_window.activate_live_mode()
        if clear_loaded_archive:
            self._clear_loaded_session_tempdir()
        self.phyphox.reset_processing_states()
        self.capture.clear_recordings(remove_files=remove_video_files)
        self.session_controller.clear()
        self.playback_cursor_time = None
        self.view_locked = False
        self.manual_wave_xlim = None
        self.manual_wave_ylim = None
        self.manual_spec_xlim = None
        self.manual_spec_ylim = None
        self.video_window.reset_offset_state()
        self.redraw_requested = True
        self._refresh_action_buttons()

    def save_session_bundle(self, archive_path: Path) -> Path:
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="unified_haptic_session_") as tmp_dir_str:
            tmp_dir = Path(tmp_dir_str)
            recordings = self.capture.export_recordings(tmp_dir)
            self.phyphox.export_data(
                tmp_dir,
                self.use_acc_var.get(),
                self.use_gyr_var.get(),
                self._build_export_meta(recordings),
            )
            self._export_interpolated_signal(tmp_dir)
            self.save_graphs(tmp_dir)
            return create_session_archive(tmp_dir, archive_path)

    def _extract_session_archive(self, archive_path: Path) -> Path:
        self._clear_loaded_session_tempdir()
        extracted_dir, session_dir = extract_session_archive(archive_path)
        self.loaded_session_tempdir = extracted_dir
        return session_dir

    def load_session_bundle(self, archive_path: Path) -> None:
        if self._is_recording_active():
            raise RuntimeError("Stop recording before loading another session")
        active_session = self._get_active_app_session()
        if active_session is None:
            active_session = self._create_app_session()
            self.active_app_session_id = active_session.session_id
        preserve_current_video_files = self._current_video_uses_session_snapshot(active_session)
        if self._current_state_has_data():
            self._save_active_session_snapshot()
            active_session = self._create_app_session(title=archive_path.stem or None)
            self.active_app_session_id = active_session.session_id
        extracted_dir = self._extract_session_archive(archive_path)
        try:
            self._load_session_dir_into_current_state(
                extracted_dir,
                preserve_current_video_files=preserve_current_video_files,
                clear_loaded_archive=False,
            )
            meta_path = extracted_dir / "session_meta.json"
            if meta_path.exists():
                try:
                    meta_preview = json.loads(meta_path.read_text(encoding="utf-8"))
                    loaded_title = str(meta_preview.get("session_title") or "").strip()
                    if loaded_title:
                        active_session.title = loaded_title
                    elif archive_path.stem:
                        active_session.title = archive_path.stem
                except Exception:
                    if archive_path.stem:
                        active_session.title = archive_path.stem
            elif archive_path.stem:
                active_session.title = archive_path.stem
            active_session.has_data = self._current_state_has_data()
            self._export_current_state_to_dir(active_session.snapshot_dir)
            self._load_session_dir_into_current_state(active_session.snapshot_dir)
        finally:
            self._clear_loaded_session_tempdir()
        self._refresh_session_tabs()
        self._refresh_action_buttons()

    def save_graphs(self, out_dir: Path) -> None:
        out_dir.mkdir(parents=True, exist_ok=True)
        self.figure.savefig(out_dir / "dashboard.png", dpi=160, bbox_inches="tight")
        out = self.phyphox.current_output(self.use_acc_var.get(), self.use_gyr_var.get(), self._get_interpolation_config())
        if out is None:
            return
        rel_t = out["rel_t"]
        sig = out["signal"]
        fs = out["fs"]
        if self.use_env_var.get():
            sig = compute_envelope(sig, fs)
        fig = Figure(figsize=(11, 7), dpi=150)
        gs = fig.add_gridspec(2, 1, hspace=0.30)
        ax1 = fig.add_subplot(gs[0, 0])
        ax2 = fig.add_subplot(gs[1, 0], sharex=ax1)
        ax1.plot(rel_t, sig, linewidth=0.9)
        ax1.set_title(f"Envelope: {out['label']}" if self.use_env_var.get() else f"Filtered principal signal (incremental): {out['label']}")
        ax1.set_ylabel("Normalized amplitude")
        if self.playback_cursor_time is not None:
            cursor_x = min(max(self.playback_cursor_time, float(rel_t[0])), float(rel_t[-1]))
            ax1.axvline(cursor_x, color="red", linewidth=1.0, alpha=0.9)
        sxx_db, spec_t0, _spec_t1, tt, f = self._prepare_spectrogram(rel_t, sig, fs)
        if tt.size and f.size:
            ax2.imshow(
                sxx_db,
                origin="lower",
                aspect="auto",
                interpolation=self.spec_display_interp_var.get(),
                extent=[spec_t0 + tt[0], spec_t0 + tt[-1], f[0], f[-1]],
            )
        if self.playback_cursor_time is not None:
            cursor_x = min(max(self.playback_cursor_time, float(rel_t[0])), float(rel_t[-1]))
            ax2.axvline(cursor_x, color="red", linewidth=1.0, alpha=0.9)
        ax2.set_title("Spectrogram")
        ax2.set_ylabel("Hz")
        ax2.set_xlabel("Time, s")
        fig.savefig(out_dir / "combined_graphs.png", dpi=160, bbox_inches="tight")

    def save_session_dialog(self) -> bool:
        active_session = self._get_active_app_session()
        suggested_base = self._sanitize_session_name_for_filename(
            active_session.title if active_session is not None else f"unified_haptic_session_{time.strftime('%Y%m%d_%H%M%S')}"
        )
        default_name = f"{suggested_base}.zip"
        path = filedialog.asksaveasfilename(
            title="Save session archive",
            defaultextension=".zip",
            initialfile=default_name,
            filetypes=[("Session archive", "*.zip")],
        )
        if not path:
            return False
        try:
            archive_path = self.save_session_bundle(Path(path))
            if active_session is not None:
                active_session.title = archive_path.stem
                self._refresh_session_tabs()
                self._refresh_compare_window()
            self._set_status_value(self.info_var, f"Saved session to {archive_path}", "Info")
            self._append_log(f"Session saved: {archive_path}")
            return True
        except Exception as exc:
            self._show_error("Save session", str(exc))
            return False

    def load_session_dialog(self) -> None:
        if self.phyphox.is_measuring or self.capture.shared_state.recorder.recording:
            self._show_info("Load session", "Stop recording before loading a saved session")
            return
        path = filedialog.askopenfilename(
            title="Load session archive",
            filetypes=[("Session archive", "*.zip")],
        )
        if not path:
            return
        try:
            self.phyphox.disconnect()
            self.load_session_bundle(Path(path))
            self._set_status_value(self.info_var, f"Loaded session from {path}", "Info")
            self._append_log(f"Session loaded: {path}")
        except Exception as exc:
            self._show_error("Load session", str(exc))

    def save_graphs_dialog(self) -> None:
        path = filedialog.askdirectory(title="Choose folder for graph export")
        if not path:
            return
        out_dir = Path(path) / f"phyphox_graphs_{time.strftime('%Y%m%d_%H%M%S')}"
        try:
            self.save_graphs(out_dir)
            self._set_status_value(self.info_var, f"Saved graphs to {out_dir}", "Info")
            self._append_log(f"Graphs saved: {out_dir}")
        except Exception as exc:
            self._show_error("Save graphs", str(exc))

    def save_video_dialog(self) -> None:
        label_suffix = ".mp4"
        default_name = f"iphone_capture_{datetime.now().strftime('%Y%m%d_%H%M%S')}{label_suffix}"
        path = filedialog.asksaveasfilename(
            title="Сохранить запись",
            defaultextension=label_suffix,
            initialfile=default_name,
            filetypes=[("MP4 video", "*.mp4")],
        )
        if not path:
            return
        try:
            self.capture.save_latest_recording(path)
            self._set_status_value(self.info_var, f"Saved video to {path}", "Info")
            self._append_log(f"Video saved: {path}")
        except Exception as exc:
            self._show_error("Save video", str(exc))

    def on_close(self) -> None:
        if self.closed:
            return
        if self._is_recording_active():
            should_stop = messagebox.askokcancel(
                "Exit",
                "Recording is in progress. Stop recording and exit?",
            )
            if not should_stop:
                return
            self.cmd_stop()
            if self._is_recording_active():
                return
        if not self._prompt_save_active_session("exiting"):
            return
        self.closed = True
        try:
            self._clear_loaded_session_tempdir()
            if self.log_window is not None and self.log_window.winfo_exists():
                self.log_window.destroy()
            if self.connection_window is not None and self.connection_window.winfo_exists():
                self.connection_window.destroy()
            if self.compare_window is not None and self.compare_window.winfo_exists():
                self.compare_window.destroy()
            for session in self.app_sessions:
                shutil.rmtree(session.snapshot_dir, ignore_errors=True)
            self.video_window.close()
            self.phyphox.disconnect()
            self.capture.stop_capture()
        finally:
            if self.root.winfo_exists():
                self.root.destroy()
