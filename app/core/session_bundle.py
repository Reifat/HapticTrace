# Copyright 2026 Nikolai Kolesnikov
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Tuple


def create_session_archive(source_dir: Path, archive_path: Path) -> Path:
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for file_path in sorted(source_dir.rglob("*")):
            if file_path.is_file():
                archive.write(file_path, file_path.relative_to(source_dir))
    return archive_path


def extract_session_archive(archive_path: Path, prefix: str = "unified_haptic_session_loaded_") -> Tuple[Path, Path]:
    extracted_dir = Path(tempfile.mkdtemp(prefix=prefix))
    root_dir = extracted_dir.resolve()
    try:
        with zipfile.ZipFile(archive_path, "r") as archive:
            for member in archive.infolist():
                member_path = (extracted_dir / member.filename).resolve()
                if root_dir not in member_path.parents and member_path != root_dir:
                    raise RuntimeError("Session archive contains an invalid file path")
            archive.extractall(extracted_dir)
    except Exception:
        shutil.rmtree(extracted_dir, ignore_errors=True)
        raise

    meta_at_root = extracted_dir / "session_meta.json"
    if meta_at_root.exists():
        return extracted_dir, extracted_dir

    meta_candidates = sorted(extracted_dir.rglob("session_meta.json"))
    if len(meta_candidates) == 1:
        return extracted_dir, meta_candidates[0].parent
    if len(meta_candidates) > 1:
        raise RuntimeError("Multiple session_meta.json files found in the session archive")
    return extracted_dir, extracted_dir