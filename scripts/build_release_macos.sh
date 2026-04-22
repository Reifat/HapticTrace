#!/bin/bash
# Copyright 2026 Nikolai Kolesnikov
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BUILD_DIR="$ROOT_DIR/build"
BUILD_VENV="$BUILD_DIR/.release-build-venv"
PY2APP_DIST="$BUILD_DIR/py2app-dist"
PY2APP_WORK="$BUILD_DIR/py2app-work"
PY2APP_PROJECT="$BUILD_DIR/py2app-project"
TOOLS_DIR="$BUILD_DIR/tools"
SYFT_DIR="$TOOLS_DIR/syft"
RELEASE_DIR="$BUILD_DIR/release"
RELEASE_REQUIREMENTS_DIR="$RELEASE_DIR/requirements"
RELEASE_SCRIPTS_DIR="$RELEASE_DIR/scripts"
RELEASE_COMPLIANCE_DIR="$RELEASE_DIR/compliance"
APP_NAME="HapticTrace"
BUNDLE_ID="com.nikolaikolesnikov.haptictrace"
APP_BUNDLE="$PY2APP_DIST/$APP_NAME.app"
FINAL_APP="$RELEASE_DIR/$APP_NAME.app"
INFO_PLIST="$FINAL_APP/Contents/Info.plist"
SBOM_PATH="$RELEASE_COMPLIANCE_DIR/sbom.runtime.cdx.json"
BUILD_INFO_PATH="$RELEASE_DIR/BUILD_INFO.txt"
ARCHIVE_PATH="$BUILD_DIR/${APP_NAME}-macos-release.zip"
CODESIGN_IDENTITY="${HAPTIC_CODESIGN_IDENTITY:--}"
SYFT_VERSION="${HAPTIC_SYFT_VERSION:-1.42.2}"
APP_ENTRY="$ROOT_DIR/scripts/HapticTrace.py"
RELEASE_TOOLS_IN="$ROOT_DIR/scripts/requirements-release.in"
RELEASE_TOOLS_LOCK="$ROOT_DIR/scripts/requirements-release.lock"

log() {
  printf '\n[%s] %s\n' "build-release" "$1"
}

require_file() {
  if [ ! -f "$1" ]; then
    echo "required file is missing: $1" >&2
    exit 1
  fi
}

if [ "$(uname -s)" != "Darwin" ]; then
  echo "this release builder currently supports macOS only" >&2
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 not found" >&2
  exit 1
fi

if ! command -v codesign >/dev/null 2>&1; then
  echo "codesign not found" >&2
  exit 1
fi

if ! command -v ditto >/dev/null 2>&1; then
  echo "ditto not found" >&2
  exit 1
fi

if ! command -v curl >/dev/null 2>&1; then
  echo "curl not found" >&2
  exit 1
fi

if ! command -v tar >/dev/null 2>&1; then
  echo "tar not found" >&2
  exit 1
fi

require_file "$ROOT_DIR/requirements/runtime.lock"
require_file "$ROOT_DIR/requirements/runtime.in"
require_file "$APP_ENTRY"
require_file "$RELEASE_TOOLS_IN"
require_file "$RELEASE_TOOLS_LOCK"
require_file "$ROOT_DIR/LICENSE"
require_file "$ROOT_DIR/NOTICE"
require_file "$ROOT_DIR/THIRD_PARTY_NOTICES.md"
require_file "$ROOT_DIR/readme.md"
require_file "$ROOT_DIR/app/__main__.py"

log "Resetting build directory"
python3 - "$BUILD_DIR" <<'PY'
from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path

build_dir = Path(sys.argv[1])

for _ in range(5):
    if build_dir.exists():
        shutil.rmtree(build_dir, ignore_errors=True)
    if not build_dir.exists():
        break
    time.sleep(0.2)
else:
    raise SystemExit(f"failed to remove build directory: {build_dir}")
PY
mkdir -p \
  "$RELEASE_REQUIREMENTS_DIR" \
  "$RELEASE_SCRIPTS_DIR" \
  "$RELEASE_COMPLIANCE_DIR" \
  "$TOOLS_DIR" \
  "$SYFT_DIR" \
  "$PY2APP_PROJECT"

case "$(uname -m)" in
  arm64|aarch64)
    SYFT_ARCH="arm64"
    ;;
  x86_64)
    SYFT_ARCH="amd64"
    ;;
  *)
    echo "unsupported macOS architecture for syft download: $(uname -m)" >&2
    exit 1
    ;;
esac

SYFT_TARBALL="syft_${SYFT_VERSION}_darwin_${SYFT_ARCH}.tar.gz"
SYFT_TARBALL_URL="https://github.com/anchore/syft/releases/download/v${SYFT_VERSION}/${SYFT_TARBALL}"
SYFT_CHECKSUMS_URL="https://github.com/anchore/syft/releases/download/v${SYFT_VERSION}/syft_${SYFT_VERSION}_checksums.txt"
SYFT_TARBALL_PATH="$SYFT_DIR/$SYFT_TARBALL"
SYFT_CHECKSUMS_PATH="$SYFT_DIR/syft_checksums.txt"
SYFT_BIN="$SYFT_DIR/syft"

log "Creating release build environment"
python3 -m venv "$BUILD_VENV"
"$BUILD_VENV/bin/python" -m pip install --upgrade pip >/dev/null
"$BUILD_VENV/bin/pip" install -r "$ROOT_DIR/requirements/runtime.lock"
"$BUILD_VENV/bin/pip" install -r "$RELEASE_TOOLS_LOCK"
PYTHON_VERSION_MM="$("$BUILD_VENV/bin/python" - <<'PY'
import sys

print(f"{sys.version_info.major}.{sys.version_info.minor}")
PY
)"
BUILD_SITE_PACKAGES="$("$BUILD_VENV/bin/python" - <<'PY'
import sysconfig

print(sysconfig.get_path("purelib"))
PY
)"

log "Preparing py2app project"
python3 - "$ROOT_DIR" "$PY2APP_PROJECT/setup.py" "$BUNDLE_ID" <<'PY'
from __future__ import annotations

import sys
from pathlib import Path

root_dir = Path(sys.argv[1])
setup_path = Path(sys.argv[2])
bundle_id = sys.argv[3]

setup_path.write_text(
    f"""from __future__ import annotations

import sys
from pathlib import Path

from setuptools import setup

ROOT_DIR = Path({root_dir.as_posix()!r})
sys.path.insert(0, str(ROOT_DIR))

OPTIONS = {{
    "argv_emulation": False,
    "packages": [
        "app",
        "requests",
        "urllib3",
        "charset_normalizer",
        "idna",
        "certifi",
        "numpy",
        "scipy",
        "matplotlib",
        "PIL",
    ],
    "includes": [
        "tkinter",
        "matplotlib.backends.backend_tkagg",
        "PIL._tkinter_finder",
        "AVFoundation",
        "CoreMedia",
        "CoreMediaIO",
        "Quartz",
        "objc",
        "Foundation",
    ],
    "excludes": ["pytest", "_pytest"],
    "plist": {{
        "CFBundleDisplayName": "HapticTrace",
        "CFBundleName": "HapticTrace",
        "CFBundleIdentifier": {bundle_id!r},
        "CFBundleShortVersionString": "0.0.0",
        "NSCameraUsageDescription": "HapticTrace accesses video capture devices to record iPhone and iPad screens.",
        "NSHighResolutionCapable": True,
    }},
}}

setup(
    app=[str(ROOT_DIR / "scripts" / "HapticTrace.py")],
    options={{"py2app": OPTIONS}},
)
""",
    encoding="utf-8",
)
PY

log "Building the macOS app bundle with py2app"
env PYTHONPATH="$ROOT_DIR${PYTHONPATH:+:$PYTHONPATH}" \
  "$BUILD_VENV/bin/python" "$PY2APP_PROJECT/setup.py" py2app \
  --dist-dir "$PY2APP_DIST" \
  --bdist-base "$PY2APP_WORK"

if [ ! -d "$APP_BUNDLE" ]; then
  echo "expected app bundle was not produced: $APP_BUNDLE" >&2
  exit 1
fi

log "Staging release contents"
ditto "$APP_BUNDLE" "$FINAL_APP"
cp "$ROOT_DIR/LICENSE" "$RELEASE_DIR/"
cp "$ROOT_DIR/NOTICE" "$RELEASE_DIR/"
cp "$ROOT_DIR/THIRD_PARTY_NOTICES.md" "$RELEASE_DIR/"
cp "$ROOT_DIR/readme.md" "$RELEASE_DIR/README.md"
cp "$ROOT_DIR/requirements/runtime.in" "$RELEASE_REQUIREMENTS_DIR/"
cp "$ROOT_DIR/requirements/runtime.lock" "$RELEASE_REQUIREMENTS_DIR/"
cp "$ROOT_DIR/scripts/build_release_macos.sh" "$RELEASE_SCRIPTS_DIR/"
cp "$ROOT_DIR/scripts/HapticTrace.py" "$RELEASE_SCRIPTS_DIR/"
cp "$RELEASE_TOOLS_IN" "$RELEASE_SCRIPTS_DIR/"
cp "$RELEASE_TOOLS_LOCK" "$RELEASE_SCRIPTS_DIR/"

log "Copying compiled helper modules needed by bundled dependencies"
python3 - "$BUILD_SITE_PACKAGES" "$FINAL_APP/Contents/Resources/lib/python${PYTHON_VERSION_MM}" <<'PY'
from __future__ import annotations

import shutil
import sys
from pathlib import Path

source_root = Path(sys.argv[1])
bundle_lib_root = Path(sys.argv[2])

for source_path in source_root.rglob("*__mypyc*.so"):
    destination_path = bundle_lib_root / source_path.relative_to(source_root)
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_path, destination_path)
PY

log "Adding bundle metadata required for camera-style capture prompts"
python3 - "$INFO_PLIST" <<'PY'
from __future__ import annotations

import plistlib
import sys
from pathlib import Path

path = Path(sys.argv[1])
with path.open("rb") as fh:
    data = plistlib.load(fh)

data["CFBundleDisplayName"] = "HapticTrace"
data["CFBundleIdentifier"] = "com.nikolaikolesnikov.haptictrace"
data["NSCameraUsageDescription"] = (
    "HapticTrace accesses video capture devices to record iPhone and iPad screens."
)
data["NSHighResolutionCapable"] = True

with path.open("wb") as fh:
    plistlib.dump(data, fh, sort_keys=False)
PY

log "Codesigning the app bundle"
codesign --force --deep --sign "$CODESIGN_IDENTITY" --timestamp=none "$FINAL_APP"
codesign --verify --deep --strict "$FINAL_APP"

log "Downloading pinned syft release"
curl -L --fail --silent --show-error "$SYFT_TARBALL_URL" -o "$SYFT_TARBALL_PATH"
curl -L --fail --silent --show-error "$SYFT_CHECKSUMS_URL" -o "$SYFT_CHECKSUMS_PATH"
python3 - "$SYFT_TARBALL_PATH" "$SYFT_CHECKSUMS_PATH" "$SYFT_TARBALL" <<'PY'
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

tarball_path = Path(sys.argv[1])
checksums_path = Path(sys.argv[2])
target_name = sys.argv[3]

expected = None
for line in checksums_path.read_text(encoding="utf-8").splitlines():
    parts = line.split()
    if len(parts) >= 2 and parts[-1] == target_name:
        expected = parts[0]
        break

if expected is None:
    raise SystemExit(f"checksum entry not found for {target_name}")

actual = hashlib.sha256(tarball_path.read_bytes()).hexdigest()
if actual != expected:
    raise SystemExit(
        "syft download checksum mismatch: "
        f"expected {expected}, got {actual}"
    )
PY
tar -xzf "$SYFT_TARBALL_PATH" -C "$SYFT_DIR" syft
chmod +x "$SYFT_BIN"

log "Generating release SBOM from the final app bundle"
"$SYFT_BIN" "dir:$FINAL_APP" -o "cyclonedx-json=$SBOM_PATH"

log "Writing build metadata"
python3 - "$ROOT_DIR" "$BUILD_INFO_PATH" "$CODESIGN_IDENTITY" "$SYFT_VERSION" <<'PY'
from __future__ import annotations

import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

root = Path(sys.argv[1])
output_path = Path(sys.argv[2])
codesign_identity = sys.argv[3]
syft_version = sys.argv[4]

try:
    git_commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        text=True,
    ).strip()
except Exception:
    git_commit = "unknown"

output_path.write_text(
    "\n".join(
        [
            "HapticTrace release build",
            f"Built at (UTC): {datetime.now(timezone.utc).isoformat()}",
            f"Git commit: {git_commit}",
            f"Platform: {platform.platform()}",
            f"Python: {platform.python_version()}",
            f"Codesign identity: {codesign_identity}",
            f"Syft version: {syft_version}",
            "",
            "The app bundle is ad-hoc signed unless a Developer ID identity was",
            "provided via HAPTIC_CODESIGN_IDENTITY before running the build script.",
        ]
    )
    + "\n",
    encoding="utf-8",
)
PY

log "Smoke-testing the built app entrypoint"
"$FINAL_APP/Contents/MacOS/$APP_NAME" --help >/dev/null

log "Creating the release archive"
ditto -c -k --sequesterRsrc --keepParent "$RELEASE_DIR" "$ARCHIVE_PATH"

log "Release build completed"
printf '%s\n' \
  "App bundle: $FINAL_APP" \
  "SBOM: $SBOM_PATH" \
  "Archive: $ARCHIVE_PATH"
