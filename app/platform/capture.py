# Copyright 2026 Nikolai Kolesnikov
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import ctypes
import ctypes.util
import shutil
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from queue import Empty, Queue
from typing import Dict, List, Optional, Sequence, Tuple

import AVFoundation as AVF
import CoreMedia
import CoreMediaIO
import Quartz
import numpy as np
import objc
from Foundation import NSDate, NSObject, NSRunLoop, NSURL
from PIL import Image

from app.core.contracts import SessionTrackerProtocol
from app.core.parsing import parse_optional_float, parse_optional_int


coremediaio_path = ctypes.util.find_library("CoreMediaIO")
if not coremediaio_path:
    raise RuntimeError("CoreMediaIO not found")
cmio = ctypes.CDLL(coremediaio_path)
UInt32 = ctypes.c_uint32
CMIOObjectID = UInt32


class CMIOObjectPropertyAddress(ctypes.Structure):
    _fields_ = [
        ("mSelector", UInt32),
        ("mScope", UInt32),
        ("mElement", UInt32),
    ]


cmio.CMIOObjectSetPropertyData.argtypes = [
    CMIOObjectID,
    ctypes.POINTER(CMIOObjectPropertyAddress),
    UInt32,
    ctypes.c_void_p,
    UInt32,
    ctypes.c_void_p,
]
cmio.CMIOObjectSetPropertyData.restype = ctypes.c_int32
kCMIOObjectSystemObject = 1
kCMIOObjectPropertyScopeGlobal = 0x676C6F62
kCMIOObjectPropertyElementMain = 0
kCMIOHardwarePropertyAllowScreenCaptureDevices = int(CoreMediaIO.kCMIOHardwarePropertyAllowScreenCaptureDevices)


def enable_screen_devices() -> None:
    addr = CMIOObjectPropertyAddress(
        kCMIOHardwarePropertyAllowScreenCaptureDevices,
        kCMIOObjectPropertyScopeGlobal,
        kCMIOObjectPropertyElementMain,
    )
    allow = UInt32(1)
    status = cmio.CMIOObjectSetPropertyData(
        CMIOObjectID(kCMIOObjectSystemObject),
        ctypes.byref(addr),
        UInt32(0),
        None,
        UInt32(ctypes.sizeof(allow)),
        ctypes.byref(allow),
    )
    if status != 0:
        raise RuntimeError(f"CMIOObjectSetPropertyData failed: {status}")


libsystem_path = ctypes.util.find_library("System")
if not libsystem_path:
    raise RuntimeError("libSystem not found")
libsystem = ctypes.CDLL(libsystem_path)
libsystem.dispatch_queue_create.argtypes = [ctypes.c_char_p, ctypes.c_void_p]
libsystem.dispatch_queue_create.restype = ctypes.c_void_p


def create_dispatch_queue(name: str):
    queue = libsystem.dispatch_queue_create(name.encode("utf-8"), None)
    if not queue:
        raise RuntimeError("dispatch_queue_create failed")
    return objc.objc_object(c_void_p=queue)


def safe_str(value) -> str:
    try:
        return str(value)
    except Exception:
        return ""


def looks_like_iphone(dev) -> bool:
    blob = " ".join([
        safe_str(dev.localizedName()).lower(),
        safe_str(dev.uniqueID()).lower(),
        safe_str(dev.modelID()).lower() if hasattr(dev, "modelID") else "",
    ])
    return ("iphone" in blob) or ("ipad" in blob) or ("ios device" in blob)


def find_iphone_device(timeout: float = 20.0):
    deadline = time.time() + timeout
    loop = NSRunLoop.currentRunLoop()
    while time.time() < deadline:
        try:
            for dev in AVF.AVCaptureDevice.devices():
                if looks_like_iphone(dev):
                    return dev
        except Exception:
            pass
        combos = [
            ([AVF.AVCaptureDeviceTypeExternal], AVF.AVMediaTypeMuxed),
            ([AVF.AVCaptureDeviceTypeExternal], AVF.AVMediaTypeVideo),
            ([AVF.AVCaptureDeviceTypeExternal], None),
        ]
        for device_types, media_type in combos:
            try:
                session = AVF.AVCaptureDeviceDiscoverySession.discoverySessionWithDeviceTypes_mediaType_position_(
                    device_types,
                    media_type,
                    AVF.AVCaptureDevicePositionUnspecified,
                )
                for dev in session.devices():
                    if looks_like_iphone(dev):
                        return dev
            except Exception:
                pass
        loop.runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.25))
    return None


def samplebuffer_to_bgra(sample_buffer):
    image_buffer = AVF.CMSampleBufferGetImageBuffer(sample_buffer)
    if image_buffer is None:
        return None
    Quartz.CVPixelBufferLockBaseAddress(image_buffer, 0)
    try:
        width = Quartz.CVPixelBufferGetWidth(image_buffer)
        height = Quartz.CVPixelBufferGetHeight(image_buffer)
        bytes_per_row = Quartz.CVPixelBufferGetBytesPerRow(image_buffer)
        base_address = Quartz.CVPixelBufferGetBaseAddress(image_buffer)
        if base_address is None:
            return None
        mv = base_address.as_buffer(bytes_per_row * height)
        arr = np.frombuffer(mv, dtype=np.uint8).reshape((height, bytes_per_row // 4, 4))
        return arr[:, :width, :].copy()
    finally:
        Quartz.CVPixelBufferUnlockBaseAddress(image_buffer, 0)


def build_file_url(path: str):
    return NSURL.fileURLWithPath_(path)


@dataclass
class AssetVideoInfo:
    duration_s: float = 0.0
    fps: float = 0.0
    frame_count: int = 0
    frame_size: Optional[Tuple[int, int]] = None


def _safe_video_seconds(cm_time) -> float:
    try:
        seconds = float(CoreMedia.CMTimeGetSeconds(cm_time))
    except Exception:
        return 0.0
    return seconds if np.isfinite(seconds) and seconds > 0.0 else 0.0


def read_video_asset_info(path: str) -> AssetVideoInfo:
    asset = AVF.AVURLAsset.alloc().initWithURL_options_(build_file_url(path), None)
    duration_s = _safe_video_seconds(asset.duration())
    tracks = asset.tracksWithMediaType_(AVF.AVMediaTypeVideo)
    if not tracks:
        return AssetVideoInfo(duration_s=duration_s)
    track = tracks[0]
    fps = float(track.nominalFrameRate() or 0.0)
    natural_size = track.naturalSize()
    frame_size = (
        max(1, int(round(abs(float(natural_size.width))))),
        max(1, int(round(abs(float(natural_size.height))))),
    )
    frame_count = 0
    if duration_s > 0.0 and fps > 0.0:
        frame_count = max(1, int(round(duration_s * fps)))
    return AssetVideoInfo(
        duration_s=duration_s,
        fps=fps,
        frame_count=frame_count,
        frame_size=frame_size,
    )


class AVFoundationPlaybackVideo:
    def __init__(self, path: str) -> None:
        self.path = path
        self.asset = AVF.AVURLAsset.alloc().initWithURL_options_(build_file_url(path), None)
        self.info = read_video_asset_info(path)
        tracks = self.asset.tracksWithMediaType_(AVF.AVMediaTypeVideo)
        if not tracks:
            raise RuntimeError(f"Video track not found: {path}")
        self.track = tracks[0]
        self.generator = AVF.AVAssetImageGenerator.alloc().initWithAsset_(self.asset)
        self.generator.setAppliesPreferredTrackTransform_(True)
        self.generator.setRequestedTimeToleranceBefore_(CoreMedia.kCMTimeZero)
        self.generator.setRequestedTimeToleranceAfter_(CoreMedia.kCMTimeZero)
        self.timescale = max(int(self.asset.naturalTimeScale() or 600), 600)
        self.max_seek_time_s = self._compute_max_seek_time_s()
        self._reader = None
        self._reader_output = None
        self._reader_start_time_s: Optional[float] = None
        self._reader_last_pts_s: Optional[float] = None
        self._bitmap_context = None
        self._bitmap_view = None
        self._bitmap_size: Optional[Tuple[int, int]] = None
        self._configured_max_size: Optional[Tuple[int, int]] = None
        self._configured_exact_timing = True

    def close(self) -> None:
        self._reset_sequential_reader()
        self.generator = None
        self.asset = None
        self.track = None
        self._bitmap_context = None
        self._bitmap_view = None
        self._bitmap_size = None
        self._configured_max_size = None
        self._configured_exact_timing = True

    def configure_frame_extraction(
        self,
        max_size: Optional[Tuple[int, int]] = None,
        exact_timing: bool = True,
        fps_hint: Optional[float] = None,
    ) -> None:
        if self.generator is None:
            return
        normalized_size = None
        previous_size = self._configured_max_size
        previous_exact_timing = self._configured_exact_timing
        if max_size is not None:
            width = max(1, int(max_size[0]))
            height = max(1, int(max_size[1]))
            normalized_size = (width, height)
        if normalized_size != self._configured_max_size:
            try:
                if normalized_size is None:
                    natural_size = self.info.frame_size or (1, 1)
                    size_value = Quartz.CGSizeMake(float(natural_size[0]), float(natural_size[1]))
                else:
                    size_value = Quartz.CGSizeMake(float(normalized_size[0]), float(normalized_size[1]))
                self.generator.setMaximumSize_(size_value)
                self._configured_max_size = normalized_size
            except Exception:
                self._configured_max_size = normalized_size
        if exact_timing != self._configured_exact_timing:
            tolerance = CoreMedia.kCMTimeZero
            if not exact_timing:
                effective_fps = float(fps_hint or self.info.fps or 60.0)
                effective_fps = min(max(effective_fps, 1.0), 240.0)
                tolerance = CoreMedia.CMTimeMakeWithSeconds(0.5 / effective_fps, self.timescale)
            self.generator.setRequestedTimeToleranceBefore_(tolerance)
            self.generator.setRequestedTimeToleranceAfter_(tolerance)
            self._configured_exact_timing = exact_timing
        if (
            normalized_size != previous_size
            or exact_timing != previous_exact_timing
        ):
            self._reset_sequential_reader()

    def _reset_sequential_reader(self) -> None:
        if self._reader is not None:
            try:
                self._reader.cancelReading()
            except Exception:
                pass
        self._reader = None
        self._reader_output = None
        self._reader_start_time_s = None
        self._reader_last_pts_s = None

    def _sequential_tolerance_s(self) -> float:
        effective_fps = float(self.info.fps or 60.0)
        effective_fps = min(max(effective_fps, 1.0), 240.0)
        return 0.5 / effective_fps

    def _ensure_sequential_reader(self, start_time_s: float) -> None:
        start_time_s = self.clamp_time_s(start_time_s)
        tolerance_s = self._sequential_tolerance_s()
        should_restart = self._reader is None or self._reader_output is None
        if not should_restart and self._reader_last_pts_s is not None:
            if start_time_s + tolerance_s < self._reader_last_pts_s:
                should_restart = True
            elif start_time_s - self._reader_last_pts_s > 1.0:
                should_restart = True
        if not should_restart:
            return
        self._reset_sequential_reader()
        if self.asset is None or self.track is None:
            raise RuntimeError("Playback asset has been released")
        reader, err = AVF.AVAssetReader.alloc().initWithAsset_error_(self.asset, None)
        if reader is None:
            raise RuntimeError(f"Cannot create AVAssetReader: {err or 'unknown error'}")
        reader.setPreparesMediaDataForRealTimeConsumption_(False)
        remaining_s = max(self.info.duration_s - start_time_s, 0.001)
        start_cm_time = CoreMedia.CMTimeMakeWithSeconds(start_time_s, self.timescale)
        duration_cm_time = CoreMedia.CMTimeMakeWithSeconds(remaining_s, self.timescale)
        reader.setTimeRange_(CoreMedia.CMTimeRangeMake(start_cm_time, duration_cm_time))
        output_settings = {
            Quartz.kCVPixelBufferPixelFormatTypeKey: Quartz.kCVPixelFormatType_32BGRA,
        }
        output = AVF.AVAssetReaderTrackOutput.alloc().initWithTrack_outputSettings_(self.track, output_settings)
        if output is None:
            raise RuntimeError("Cannot create AVAssetReaderTrackOutput")
        output.setAlwaysCopiesSampleData_(False)
        output.setAppliesPreferredTrackTransform_(True)
        if not reader.canAddOutput_(output):
            raise RuntimeError("AVAssetReader cannot add track output")
        reader.addOutput_(output)
        if not reader.startReading():
            reader_error = None
            try:
                reader_error = reader.error()
            except Exception:
                reader_error = None
            raise RuntimeError(f"AVAssetReader start failed: {reader_error or 'unknown error'}")
        self._reader = reader
        self._reader_output = output
        self._reader_start_time_s = start_time_s
        self._reader_last_pts_s = None

    def _read_sample_buffer_at_time_sequential(self, time_s: float):
        target_time_s = self.clamp_time_s(time_s)
        tolerance_s = self._sequential_tolerance_s()
        self._ensure_sequential_reader(target_time_s)
        output = self._reader_output
        if output is None:
            raise RuntimeError("Sequential playback reader is not initialized")
        while True:
            sample_buffer = output.copyNextSampleBuffer()
            if sample_buffer is None:
                self._reset_sequential_reader()
                return None
            pts_s = _safe_video_seconds(CoreMedia.CMSampleBufferGetPresentationTimeStamp(sample_buffer))
            self._reader_last_pts_s = pts_s
            if pts_s + tolerance_s < target_time_s:
                continue
            return sample_buffer

    def read_frame_at_time(self, time_s: float) -> np.ndarray:
        if self.generator is None:
            raise RuntimeError("Playback asset has been released")
        target_time_s = self.clamp_time_s(time_s)
        if self._configured_exact_timing:
            cm_time = CoreMedia.CMTimeMakeWithSeconds(target_time_s, self.timescale)
            cg_image, err = self.generator.copyCGImageAtTime_actualTime_error_(cm_time, None, None)
            if cg_image is None:
                raise RuntimeError(f"Cannot decode video frame: {err or 'unknown error'}")
        else:
            cg_image = None
            err = None
            sample_buffer = self._read_sample_buffer_at_time_sequential(target_time_s)
            if sample_buffer is None:
                cm_time = CoreMedia.CMTimeMakeWithSeconds(target_time_s, self.timescale)
                cg_image, err = self.generator.copyCGImageAtTime_actualTime_error_(cm_time, None, None)
                if cg_image is None:
                    raise RuntimeError(f"Cannot decode video frame: {err or 'unknown error'}")
        if self._configured_exact_timing or cg_image is not None:
            return self._cgimage_to_bgra(cg_image)
        return self._scale_bgra_to_configured_size(samplebuffer_to_bgra(sample_buffer))

    def clamp_time_s(self, time_s: float) -> float:
        clamped = max(float(time_s), 0.0)
        if self.max_seek_time_s > 0.0:
            clamped = min(clamped, self.max_seek_time_s)
        return clamped

    def _compute_max_seek_time_s(self) -> float:
        if self.info.frame_count > 0 and self.info.fps > 0.0:
            return max(0.0, (self.info.frame_count - 1) / self.info.fps)
        if self.info.duration_s > 0.0:
            epsilon = 1.0 / float(max(self.timescale, 1))
            return max(0.0, self.info.duration_s - epsilon)
        return 0.0

    def _scale_bgra_to_configured_size(self, frame_bgra: np.ndarray) -> np.ndarray:
        if self._configured_max_size is None:
            return frame_bgra
        height, width = frame_bgra.shape[:2]
        max_width = max(1, int(self._configured_max_size[0]))
        max_height = max(1, int(self._configured_max_size[1]))
        if width <= max_width and height <= max_height:
            return frame_bgra
        scale = min(max_width / max(width, 1), max_height / max(height, 1))
        if scale >= 1.0:
            return frame_bgra
        target_width = max(1, int(round(width * scale)))
        target_height = max(1, int(round(height * scale)))
        working_frame = np.ascontiguousarray(frame_bgra)
        frame_image = Image.frombuffer("RGBA", (width, height), working_frame, "raw", "BGRA", 0, 1)
        resampling = getattr(Image, "Resampling", Image)
        resized_image = frame_image.resize((target_width, target_height), resample=resampling.NEAREST)
        resized_bgra = resized_image.tobytes("raw", "BGRA")
        return np.frombuffer(resized_bgra, dtype=np.uint8).copy().reshape((target_height, target_width, 4))

    def _cgimage_to_bgra(self, cg_image) -> np.ndarray:
        width = int(Quartz.CGImageGetWidth(cg_image))
        height = int(Quartz.CGImageGetHeight(cg_image))
        self._ensure_bitmap_context(width, height)
        if self._bitmap_context is None or self._bitmap_view is None:
            raise RuntimeError("Cannot create bitmap context for video frame")
        Quartz.CGContextDrawImage(self._bitmap_context, Quartz.CGRectMake(0, 0, width, height), cg_image)
        return np.frombuffer(self._bitmap_view, dtype=np.uint8, count=width * height * 4).copy().reshape((height, width, 4))

    def _ensure_bitmap_context(self, width: int, height: int) -> None:
        if self._bitmap_context is not None and self._bitmap_size == (width, height):
            return
        color_space = Quartz.CGColorSpaceCreateDeviceRGB()
        bitmap_info = Quartz.kCGBitmapByteOrder32Little | Quartz.kCGImageAlphaPremultipliedFirst
        context = Quartz.CGBitmapContextCreate(None, width, height, 8, width * 4, color_space, bitmap_info)
        if context is None:
            raise RuntimeError("CGBitmapContextCreate failed")
        data = Quartz.CGBitmapContextGetData(context)
        if data is None:
            raise RuntimeError("CGBitmapContextGetData failed")
        self._bitmap_context = context
        self._bitmap_view = data.as_buffer(width * height * 4)
        self._bitmap_size = (width, height)


@dataclass
class VideoClipMetadata:
    clip_id: str
    temp_path: str
    start_request_wall_ts: float
    stop_request_wall_ts: Optional[float] = None
    first_frame_wall_ts: Optional[float] = None
    frame_count: int = 0
    fps_hint: float = 30.0
    frame_size: Optional[Tuple[int, int]] = None
    duration_s: Optional[float] = None


def _build_asset_writer_settings(
    frame_size: Tuple[int, int],
    writer_settings: Optional[Dict[str, object]] = None,
) -> Dict[str, object]:
    width, height = frame_size
    normalized: Dict[str, object] = {}
    if writer_settings:
        try:
            normalized.update(dict(writer_settings))
        except Exception:
            pass
    normalized[AVF.AVVideoCodecKey] = normalized.get(AVF.AVVideoCodecKey) or AVF.AVVideoCodecTypeH264
    normalized[AVF.AVVideoWidthKey] = int(width)
    normalized[AVF.AVVideoHeightKey] = int(height)
    return normalized


class Recorder:
    STOP_TIMEOUT_S = 10.0
    WRITE_READY_TIMEOUT_S = 0.25

    def __init__(self) -> None:
        self.writer = None
        self.writer_input = None
        self.temp_path: Optional[str] = None
        self.recording = False
        self.lock = threading.Lock()
        self.frame_size: Optional[Tuple[int, int]] = None
        self.fps = 30.0
        self.last_finished_path: Optional[str] = None
        self.current_clip: Optional[VideoClipMetadata] = None
        self.finished_clips: List[VideoClipMetadata] = []
        self._started_writing = False
        self._recording_error: Optional[str] = None
        self._pending_stop_request_wall_ts: Optional[float] = None

    def start(
        self,
        frame_shape: Tuple[int, ...],
        fps_hint: float = 30.0,
        start_request_wall_ts: Optional[float] = None,
        writer_settings: Optional[Dict[str, object]] = None,
    ) -> VideoClipMetadata:
        with self.lock:
            if self.recording:
                raise RuntimeError("Video recording is already running")
            height, width = frame_shape[:2]
            self.frame_size = (width, height)
            self.fps = max(1.0, float(fps_hint))
            tmp_dir = Path(tempfile.gettempdir())
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            self.temp_path = str(tmp_dir / f"iphone_capture_{stamp}.mp4")
            Path(self.temp_path).unlink(missing_ok=True)
            video_settings = _build_asset_writer_settings(self.frame_size, writer_settings)
            writer, err = AVF.AVAssetWriter.alloc().initWithURL_fileType_error_(
                build_file_url(self.temp_path),
                AVF.AVFileTypeMPEG4,
                None,
            )
            if writer is None:
                self.temp_path = None
                self.frame_size = None
                raise RuntimeError(f"Не удалось создать mp4 writer: {err}")
            writer_input = AVF.AVAssetWriterInput.alloc().initWithMediaType_outputSettings_(
                AVF.AVMediaTypeVideo,
                video_settings,
            )
            if writer_input is None:
                self.temp_path = None
                self.frame_size = None
                raise RuntimeError("Не удалось создать video input для AVAssetWriter")
            writer_input.setExpectsMediaDataInRealTime_(True)
            if not writer.canAddInput_(writer_input):
                self.temp_path = None
                self.frame_size = None
                raise RuntimeError("AVAssetWriter cannot add video input")
            writer.addInput_(writer_input)
            self.writer = writer
            self.writer_input = writer_input
            self._started_writing = False
            self.current_clip = VideoClipMetadata(
                clip_id=uuid.uuid4().hex[:8],
                temp_path=self.temp_path,
                start_request_wall_ts=start_request_wall_ts or time.time(),
                fps_hint=self.fps,
                frame_size=self.frame_size,
            )
            self.recording = True
            self._recording_error = None
            self._pending_stop_request_wall_ts = None
            clip = self.current_clip
        if clip is None:
            raise RuntimeError("Не удалось запустить запись видео")
        return clip

    def append_sample_buffer(self, sample_buffer, frame_wall_ts: Optional[float] = None) -> bool:
        with self.lock:
            if (
                not self.recording
                or self.current_clip is None
                or self.writer is None
                or self.writer_input is None
            ):
                return False
            if not CoreMedia.CMSampleBufferIsValid(sample_buffer):
                return False
            if not self._started_writing:
                if not self.writer.startWriting():
                    self._recording_error = safe_str(self.writer.error()) or "AVAssetWriter failed to start"
                    self.recording = False
                    raise RuntimeError(self._recording_error)
                first_pts = CoreMedia.CMSampleBufferGetPresentationTimeStamp(sample_buffer)
                self.writer.startSessionAtSourceTime_(first_pts)
                self._started_writing = True
            deadline = time.monotonic() + self.WRITE_READY_TIMEOUT_S
            while not self.writer_input.isReadyForMoreMediaData():
                if time.monotonic() >= deadline:
                    return False
                time.sleep(0.001)
            if not self.writer_input.appendSampleBuffer_(sample_buffer):
                self._recording_error = safe_str(self.writer.error()) or "AVAssetWriter rejected sample buffer"
                self.recording = False
                raise RuntimeError(self._recording_error)
            self.current_clip.frame_count += 1
            if self.current_clip.first_frame_wall_ts is None:
                self.current_clip.first_frame_wall_ts = frame_wall_ts or time.time()
            return True

    def _finalize_recording(self, output_path: Optional[str], error_text: Optional[str]) -> Optional[str]:
        remove_path: Optional[str] = None
        info: Optional[AssetVideoInfo] = None
        clip_to_finalize: Optional[VideoClipMetadata] = None
        completed_path: Optional[str] = None
        actual_path = output_path
        with self.lock:
            clip_to_finalize = self.current_clip
            actual_path = actual_path or self.temp_path
        if actual_path and not error_text and Path(actual_path).exists():
            try:
                info = read_video_asset_info(actual_path)
            except Exception:
                info = None
        with self.lock:
            if error_text:
                self._recording_error = str(error_text)
                remove_path = actual_path
            if clip_to_finalize is not None:
                clip_to_finalize.stop_request_wall_ts = self._pending_stop_request_wall_ts or time.time()
                if actual_path:
                    clip_to_finalize.temp_path = actual_path
                if info is not None:
                    if info.duration_s > 0.0:
                        clip_to_finalize.duration_s = info.duration_s
                    if clip_to_finalize.frame_count <= 0 and info.frame_count > 0:
                        clip_to_finalize.frame_count = info.frame_count
                    if info.frame_size is not None:
                        clip_to_finalize.frame_size = info.frame_size
                if (
                    clip_to_finalize.frame_count > 0
                    and clip_to_finalize.duration_s is not None
                    and clip_to_finalize.duration_s > 0.0
                ):
                    clip_to_finalize.fps_hint = clip_to_finalize.frame_count / clip_to_finalize.duration_s
                elif info is not None and info.fps > 0.0:
                    clip_to_finalize.fps_hint = info.fps
                if not error_text and actual_path and Path(actual_path).exists():
                    self.last_finished_path = actual_path
                    self.finished_clips.append(clip_to_finalize)
                    completed_path = actual_path
            self.writer = None
            self.writer_input = None
            self._started_writing = False
            self.current_clip = None
            self.temp_path = None
            self.frame_size = None
            self.fps = 30.0
            self.recording = False
            self._pending_stop_request_wall_ts = None
        if remove_path:
            try:
                Path(remove_path).unlink(missing_ok=True)
            except Exception:
                pass
        return completed_path

    def stop(self, stop_request_wall_ts: Optional[float] = None) -> Optional[str]:
        with self.lock:
            if not self.recording and self.current_clip is None:
                return None
            writer = self.writer
            writer_input = self.writer_input
            current_path = self.temp_path
            started_writing = self._started_writing
            self.recording = False
            self._pending_stop_request_wall_ts = stop_request_wall_ts or time.time()
            preexisting_error = self._recording_error
        if writer is None or writer_input is None:
            completed_path = self._finalize_recording(current_path, preexisting_error or "AVAssetWriter is unavailable")
        elif not started_writing:
            completed_path = self._finalize_recording(current_path, preexisting_error)
        else:
            writer_input.markAsFinished()
            done = threading.Event()
            writer.finishWritingWithCompletionHandler_(lambda: done.set())
            if not done.wait(self.STOP_TIMEOUT_S):
                raise RuntimeError("Timed out waiting for AVAssetWriter to finish")
            writer_error = None
            if writer.status() != AVF.AVAssetWriterStatusCompleted:
                writer_error = safe_str(writer.error()) or f"AVAssetWriter finished with status {writer.status()}"
            completed_path = self._finalize_recording(current_path, preexisting_error or writer_error)
        with self.lock:
            err = self._recording_error
            self._recording_error = None
        if err:
            raise RuntimeError(err)
        return completed_path

    def has_finished_recording(self) -> bool:
        with self.lock:
            return any(Path(clip.temp_path).exists() for clip in self.finished_clips)

    def save_as(self, destination_path: str) -> None:
        with self.lock:
            if not self.last_finished_path or not Path(self.last_finished_path).exists():
                raise RuntimeError("Нет готовой записи для сохранения")
            source_path = self.last_finished_path
        shutil.copy2(source_path, destination_path)

    def copy_all_to(self, out_dir: Path) -> List[Dict[str, object]]:
        out_dir.mkdir(parents=True, exist_ok=True)
        copied: List[Dict[str, object]] = []
        with self.lock:
            clips = list(self.finished_clips)
        for index, clip in enumerate(clips, start=1):
            src = Path(clip.temp_path)
            if not src.exists():
                continue
            dst_name = "screen_recording.mp4" if len(clips) == 1 else f"screen_recording_{index:02d}.mp4"
            dst = out_dir / dst_name
            shutil.copy2(src, dst)
            copied.append({
                "clip_id": clip.clip_id,
                "path": str(dst),
                "temp_path": clip.temp_path,
                "start_request_wall_ts": clip.start_request_wall_ts,
                "stop_request_wall_ts": clip.stop_request_wall_ts,
                "first_frame_wall_ts": clip.first_frame_wall_ts,
                "frame_count": clip.frame_count,
                "fps_hint": clip.fps_hint,
                "frame_size": clip.frame_size,
                "duration_s": clip.duration_s,
            })
        return copied

    def clear(self, remove_files: bool = True) -> None:
        with self.lock:
            clip_paths: List[str] = []
            if self.temp_path:
                clip_paths.append(self.temp_path)
            clip_paths.extend(clip.temp_path for clip in self.finished_clips if clip.temp_path)
            self.recording = False
            self.temp_path = None
            self.frame_size = None
            self.current_clip = None
            self.finished_clips = []
            self.last_finished_path = None
            self.writer = None
            self.writer_input = None
            self._started_writing = False
            self._recording_error = None
            self._pending_stop_request_wall_ts = None
        if remove_files:
            for clip_path in clip_paths:
                try:
                    Path(clip_path).unlink(missing_ok=True)
                except Exception:
                    pass

    def load_finished_clips(self, clip_records: Sequence[Dict[str, object]]) -> None:
        self.clear(remove_files=False)
        loaded_clips: List[VideoClipMetadata] = []
        for index, item in enumerate(clip_records, start=1):
            if not isinstance(item, dict):
                continue
            temp_path = str(item.get("temp_path") or "")
            if not temp_path or not Path(temp_path).exists():
                continue
            frame_size_raw = item.get("frame_size")
            frame_size = None
            if isinstance(frame_size_raw, (list, tuple)) and len(frame_size_raw) == 2:
                frame_size = (int(frame_size_raw[0]), int(frame_size_raw[1]))
            loaded_clips.append(
                VideoClipMetadata(
                    clip_id=str(item.get("clip_id") or f"loaded_{index:02d}"),
                    temp_path=temp_path,
                    start_request_wall_ts=float(item.get("start_request_wall_ts") or time.time()),
                    stop_request_wall_ts=parse_optional_float(item.get("stop_request_wall_ts")),
                    first_frame_wall_ts=parse_optional_float(item.get("first_frame_wall_ts")),
                    frame_count=parse_optional_int(item.get("frame_count")) or 0,
                    fps_hint=float(item.get("fps_hint") or 30.0),
                    frame_size=frame_size,
                    duration_s=parse_optional_float(item.get("duration_s")),
                )
            )
        with self.lock:
            self.finished_clips = loaded_clips
            self.last_finished_path = loaded_clips[-1].temp_path if loaded_clips else None


class SharedState:
    def __init__(self) -> None:
        self.preview_queue = Queue(maxsize=2)
        self.recorder = Recorder()
        self.last_frame_shape = None
        self.capture_started = False
        self.last_error: Optional[Exception] = None
        self.frames_total = 0
        self.start_ts: Optional[float] = None
        self.last_fps = 0.0
        self.lock = threading.Lock()
        self.session_tracker: Optional[SessionTrackerProtocol] = None

    def set_session_tracker(self, tracker: SessionTrackerProtocol) -> None:
        self.session_tracker = tracker

    def push_preview(self, frame_bgra: np.ndarray) -> None:
        try:
            while True:
                self.preview_queue.get_nowait()
        except Empty:
            pass
        try:
            self.preview_queue.put_nowait(frame_bgra)
        except Exception:
            pass

    def update_fps(self) -> None:
        with self.lock:
            self.frames_total += 1
            if self.start_ts is None:
                self.start_ts = time.time()
                return
            elapsed = time.time() - self.start_ts
            if elapsed > 0:
                self.last_fps = self.frames_total / elapsed


class VideoDelegate(NSObject):
    def initWithState_(self, shared_state):
        self = objc.super(VideoDelegate, self).init()
        if self is None:
            return None
        self.shared_state = shared_state
        self.last_report_ts = time.time()
        return self

    def captureOutput_didOutputSampleBuffer_fromConnection_(self, output, sampleBuffer, connection) -> None:
        try:
            frame_bgra = samplebuffer_to_bgra(sampleBuffer)
            if frame_bgra is None:
                return
            frame_wall_ts = time.time()
            self.shared_state.last_frame_shape = frame_bgra.shape
            self.shared_state.update_fps()
            self.shared_state.push_preview(frame_bgra)
            if self.shared_state.recorder.recording:
                wrote_frame = self.shared_state.recorder.append_sample_buffer(sampleBuffer, frame_wall_ts=frame_wall_ts)
                if wrote_frame and self.shared_state.session_tracker is not None:
                    self.shared_state.session_tracker.mark_video_frame(frame_wall_ts)
            now = time.time()
            if now - self.last_report_ts >= 1.0:
                height, width = frame_bgra.shape[:2]
                print(f"{width}x{height} | fps={self.shared_state.last_fps:.1f}")
                self.last_report_ts = now
        except Exception as exc:
            self.shared_state.last_error = exc


class IphoneCaptureService:
    def __init__(self) -> None:
        self.shared_state = SharedState()
        self.session = None
        self.output = None
        self.delegate = None
        self.queue = None
        self.capture_thread: Optional[threading.Thread] = None
        self.connected = False
        self.connecting = False
        self.status_text = "iPhone capture: disconnected"
        self.device_text = "iPhone device: unknown"

    def set_session_tracker(self, tracker: SessionTrackerProtocol) -> None:
        self.shared_state.set_session_tracker(tracker)

    def connect_async(self) -> bool:
        if self.connecting or self.connected:
            return False
        self.connecting = True
        self.status_text = "iPhone capture: connecting..."
        self.capture_thread = threading.Thread(target=self._connect_worker, daemon=True)
        self.capture_thread.start()
        return True

    def _connect_worker(self) -> None:
        try:
            enable_screen_devices()
            device = find_iphone_device(timeout=20.0)
            if device is None:
                raise RuntimeError("iPhone/iPad capture device not found")
            self.session = AVF.AVCaptureSession.alloc().init()
            inp, err = AVF.AVCaptureDeviceInput.deviceInputWithDevice_error_(device, None)
            if inp is None:
                raise RuntimeError(f"Cannot create AVCaptureDeviceInput: {err}")
            if not self.session.canAddInput_(inp):
                raise RuntimeError("Cannot add input to session")
            self.session.addInput_(inp)
            self.output = AVF.AVCaptureVideoDataOutput.alloc().init()
            self.output.setAlwaysDiscardsLateVideoFrames_(True)
            self.output.setVideoSettings_({
                Quartz.kCVPixelBufferPixelFormatTypeKey: Quartz.kCVPixelFormatType_32BGRA,
            })
            self.delegate = VideoDelegate.alloc().initWithState_(self.shared_state)
            self.queue = create_dispatch_queue("iphone.capture.queue")
            self.output.setSampleBufferDelegate_queue_(self.delegate, self.queue)
            if not self.session.canAddOutput_(self.output):
                raise RuntimeError("Cannot add video output to session")
            self.session.addOutput_(self.output)
            self.session.startRunning()
            self.shared_state.capture_started = True
            self.connected = True
            self.status_text = "iPhone capture: connected"
            self.device_text = f"iPhone device: {safe_str(device.localizedName())} | {safe_str(device.uniqueID())}"
        except Exception as exc:
            self.connected = False
            self.status_text = f"iPhone capture: error: {exc}"
            self.shared_state.last_error = exc
        finally:
            self.connecting = False

    def stop_capture(self) -> None:
        if self.session is not None and self.session.isRunning():
            self.session.stopRunning()
        self.session = None
        self.output = None
        self.delegate = None
        self.queue = None
        self.connected = False
        self.connecting = False
        self.status_text = "iPhone capture: disconnected"
        self.device_text = "iPhone device: unknown"
        self.shared_state.last_frame_shape = None
        self.shared_state.last_fps = 0.0
        self.shared_state.frames_total = 0
        self.shared_state.start_ts = None

    def start_recording(self, start_request_wall_ts: Optional[float] = None) -> VideoClipMetadata:
        if self.shared_state.last_frame_shape is None:
            raise RuntimeError("Нет кадра с iPhone, запись пока нельзя начать")
        fps_hint = self.shared_state.last_fps if self.shared_state.last_fps > 1.0 else 30.0
        writer_settings = None
        if self.output is not None:
            try:
                writer_settings = self.output.recommendedVideoSettingsForVideoCodecType_assetWriterOutputFileType_(
                    AVF.AVVideoCodecTypeH264,
                    AVF.AVFileTypeMPEG4,
                )
            except Exception:
                writer_settings = None
        return self.shared_state.recorder.start(
            self.shared_state.last_frame_shape,
            fps_hint=fps_hint,
            start_request_wall_ts=start_request_wall_ts,
            writer_settings=writer_settings,
        )

    def stop_recording(self, stop_request_wall_ts: Optional[float] = None) -> Optional[str]:
        return self.shared_state.recorder.stop(stop_request_wall_ts=stop_request_wall_ts)

    def has_finished_recording(self) -> bool:
        return self.shared_state.recorder.has_finished_recording()

    def save_latest_recording(self, destination_path: str) -> None:
        self.shared_state.recorder.save_as(destination_path)

    def export_recordings(self, out_dir: Path) -> List[Dict[str, object]]:
        return self.shared_state.recorder.copy_all_to(out_dir)

    def clear_recordings(self, remove_files: bool = True) -> None:
        self.shared_state.recorder.clear(remove_files=remove_files)

    def load_recordings(self, clip_records: Sequence[Dict[str, object]]) -> None:
        self.shared_state.recorder.load_finished_clips(clip_records)