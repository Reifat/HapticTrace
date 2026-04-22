# Third-Party Notices

This file documents the third-party Python packages declared by the project
runtime/dev bootstrap inputs and by the pinned Python release-build tooling.
These packages are not vendored in this repository; they are resolved and
downloaded by `pip` at install time from pinned lockfiles.

The direct dependency sets are declared in `requirements/runtime.in` and
`requirements/dev.in`. Exact versions are pinned in `requirements/runtime.lock`
and `requirements/dev.lock`. The Python release-build tooling is declared in
`scripts/requirements-release.in` and pinned in
`scripts/requirements-release.lock`.

## Direct Runtime Dependencies

- `requests` 2.33.1 — `Apache-2.0`
  - Current direct runtime requirements: `certifi` 2026.2.25 (`MPL-2.0`),
    `charset-normalizer` 3.4.7 (`MIT`), `idna` 3.12 (`BSD-3-Clause`),
    `urllib3` 2.6.3 (`MIT`)
- `numpy` 2.4.4 — `BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0`
- `scipy` 1.17.1 — `BSD-style`
  - Current direct runtime requirements: `numpy` 2.4.4
    (`BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0`)
  - Bundled notices declared by upstream metadata: `BSD-3-Clause-Open-MPI`,
    `BSD-3-Clause`
- `Pillow` 12.2.0 — `MIT-CMU`
- `matplotlib` 3.10.8 — `Matplotlib license (PSF-style)`
  - Current direct runtime requirements: `contourpy` 1.3.3 (`BSD-style`),
    `cycler` 0.12.1 (`BSD-style`), `fonttools` 4.62.1 (`MIT`),
    `kiwisolver` 1.5.0 (`BSD-style`), `numpy` 2.4.4
    (`BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0`), `packaging` 26.1
    (`Apache-2.0 OR BSD-2-Clause`), `Pillow` 12.2.0 (`MIT-CMU`),
    `pyparsing` 3.3.2 (`MIT`), `python-dateutil` 2.9.0.post0
    (`BSD-style; Apache-2.0`)
  - Bundled notices declared by upstream metadata: `BSD-style`, `Qhull`,
    `Bitstream-Charter`, `BaKoMa Fonts Licence`, `STIX Fonts License`
- `pyobjc` 12.1 — `MIT`
  - Meta-package. In the current environment it resolves to `pyobjc-core` 12.1
    (`MIT`) and a large family of `pyobjc-framework-*` packages under `MIT`.
  - This repository also declares the specific framework wheels
    `pyobjc-framework-AVFoundation`, `pyobjc-framework-Quartz`, and
    `pyobjc-framework-CoreMediaIO` directly.
- `pyobjc-framework-AVFoundation` 12.1 — `MIT`
  - Current direct runtime requirements: `pyobjc-core` 12.1 (`MIT`),
    `pyobjc-framework-Cocoa` 12.1 (`MIT`), `pyobjc-framework-CoreAudio` 12.1
    (`MIT`), `pyobjc-framework-CoreMedia` 12.1 (`MIT`),
    `pyobjc-framework-Quartz` 12.1 (`MIT`)
- `pyobjc-framework-Quartz` 12.1 — `MIT`
  - Current direct runtime requirements: `pyobjc-core` 12.1 (`MIT`),
    `pyobjc-framework-Cocoa` 12.1 (`MIT`)
- `pyobjc-framework-CoreMediaIO` 12.1 — `MIT`
  - Current direct runtime requirements: `pyobjc-core` 12.1 (`MIT`),
    `pyobjc-framework-Cocoa` 12.1 (`MIT`)

## Direct Development/Test Dependency

- `pytest` 9.0.3 — `MIT`
  - Current direct runtime requirements for the test tool: `iniconfig` 2.3.0
    (`MIT`), `packaging` 26.1 (`Apache-2.0 OR BSD-2-Clause`), `pluggy` 1.6.0
    (`MIT`), `Pygments` 2.20.0 (`BSD-2-Clause`)

## Direct Release Build Dependency

- `py2app` 0.28.10 — `MIT or PSF License`
  - Current direct runtime requirements for the Python build tool: `altgraph`
    0.17.5 (`MIT`), `modulegraph` 0.19.7 (`MIT`), `macholib` 1.16.4 (`MIT`),
    `packaging` 26.1 (`Apache-2.0 OR BSD-2-Clause`)
  - Current transitive requirement via `modulegraph`: `setuptools` 82.0.1
    (`MIT`)

## Additional Notes

- This file is informational and complements `LICENSE` and `NOTICE`; it does
  not replace the upstream license texts distributed with third-party packages.
- Runtime installs are pinned by `requirements/runtime.lock`; developer/test
  installs are pinned by `requirements/dev.lock`.
- Python release-build installs are pinned by `scripts/requirements-release.lock`.
- Release builds generate `build/release/compliance/sbom.runtime.cdx.json`
  from the final app bundle. The build script downloads a pinned `syft`
  release binary (`Apache-2.0`) from GitHub Releases for SBOM generation.
- The repository does not vendor third-party source code. If a future release
  changes the dependency graph or release tooling, regenerate the relevant
  lockfiles and the SBOM before distribution.