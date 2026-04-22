[Русская версия](./../ru/usage.md)

# Usage

This document describes how to use `HapticTrace` for recording, synchronizing, and analyzing haptic-response sessions.

## Overview

`HapticTrace` supports three recording modes:

- **`Sensors + Video`** — synchronized recording of sensor data and screen capture
- **`Sensors Only`** — sensor capture without video
- **`Video Only`** — screen capture without sensor data

The primary workflow is `Sensors + Video`: sensor data is captured from a device running `phyphox`, while screen activity and haptic playback are captured from the target iPhone/iPad.

## Recommended setup

A typical synchronized setup uses:

- a **sensor device** running `phyphox` with `accelerometer` and `gyroscope`
- a **target iPhone/iPad** running the application under test
- a **Mac** running `HapticTrace`

For stable measurements, both mobile devices should be fixed rigidly relative to each other for the entire recording. Any relative motion introduces parasitic sensor input.

## Recording workflow

### 1. Prepare the sensor device

On the sensor device:

1. Open `phyphox`.
2. Create or load an experiment with:
   - `accelerometer`
   - `gyroscope`
3. Enable remote access.
4. Copy the experiment URL.

### 2. Prepare the target device

1. Connect the target iPhone/iPad to the Mac through USB.
2. Make sure macOS detects it as a screen capture source.
3. Launch the application under test on the target device.

### 3. Connect devices in HapticTrace

1. Launch `HapticTrace`.
2. Open `Connection -> Connection Settings`.
3. Select the required **Recording mode**.
4. Enter the `phyphox` URL if the selected mode uses sensors.
5. Click **Connect Sensors** if the selected mode uses sensors.
6. Click **Connect iPhone** if the selected mode uses video.

When both data sources are available, the application is ready to record.

### 4. Record

1. Press **Play** in the main window.
2. Reproduce the required haptic scenario in the application under test.
3. Press **Stop** when recording is complete.

After `Stop`, the session becomes available for playback and analysis.

## Main analysis workflow

After recording, the common workflow is:

1. review the measured signal
2. inspect the spectrogram if needed
3. play back the recorded video
4. verify synchronization between signal and on-screen events
5. adjust offset if required
6. save the session for later comparison or export

## Main window

The main window is the primary workspace for signal inspection.

It includes:

- session tabs when multiple sessions are open
- transport buttons such as `Play`, `Stop`, `Clear`, `Compare`, `Reset View`
- signal display
- optional spectrogram
- signal display toggles such as `Accelerometer`, `Gyroscope`, `Envelope`
- standard matplotlib navigation tools for zoom and pan

Use the main window to inspect the recorded signal, zoom into specific regions, and move the playback cursor across the timeline.

## Video Playback window

The `Video Playback` window is used for both preview and recorded playback.

Before recording, it shows live preview from the connected iPhone/iPad.  
After recording, it switches to playback mode and displays the recorded video with a shared timeline.

Main controls:

- `Play/Pause`
- `Restart`
- `Step -`
- `Step +`
- playback speed
- `Offset Settings`

In `Sensors Only` mode, the playback window is not used.

## Connection window

The `Connection` window is used to configure the recording pipeline.

It provides:

- recording mode selection
- `phyphox` URL input
- sensor connection controls
- iPhone/iPad connection controls
- connection and recording status

Use this window whenever you need to switch between `Sensors + Video`, `Sensors Only`, and `Video Only`, or reconnect devices.

## Log window

The `Log` window contains runtime information:

- connection status
- recording status
- save/load messages
- errors and diagnostic events

Use it when you need to understand the current state of the session or diagnose unexpected behavior during normal operation.

## Playback and synchronization

After recording, `HapticTrace` synchronizes the signal view, spectrogram, and recorded video on a shared timeline.

### Cursor behavior

- the video timeline and graph cursor are linked
- moving the cursor updates the current analysis position
- double-clicking on the graph or spectrogram moves playback to the selected time

### Visible-range playback

If the signal view is zoomed, playback follows the currently visible time range. This is useful when inspecting a short event or a single haptic burst.

### Reset View

`Reset View` restores the full time range and clears the current zoom/pan state for analysis.

## Offset adjustment

Synchronization uses two components:

- **automatic offset** — initial estimated alignment
- **manual offset** — user adjustment applied on top of the automatic estimate

Open **Offset Settings** from the playback window to refine synchronization.

Typical use cases:

- the haptic event appears slightly early or late relative to the video
- you want to align a specific visible UI event with a measured peak
- automatic alignment is close, but not precise enough for analysis

The offset controls allow live adjustment, so the result can be inspected immediately in both the video and the signal views.

## Spectrogram

The spectrogram provides a frequency-domain view of the recorded signal.

Use it when you need to:

- inspect the energy distribution of a haptic event
- compare bursts with different spectral content
- analyze short transients that are less obvious in the time-domain signal

### Spectrogram settings

`Tools -> Spectrogram Settings` provides control over:

- window function
- display interpolation
- analyzed fragment
- last-seconds mode
- maximum displayed frequency
- cleanup mode
- `nperseg`
- overlap ratio
- `nfft` multiplier

These settings are intended for analysis quality tuning. They do not change the original recorded data.

## Interpolation

`Tools -> Interpolation Settings` controls derived interpolation of the processed signal for analysis purposes.

This is useful when you want a denser signal representation for:

- spectrogram generation
- more stable zoomed-in analysis
- export based on the interpolated view

Interpolation is applied as an analysis layer. The original processed signal remains unchanged.

Available parameters include:

- enable/disable interpolation
- target samples on window
- window length
- overlap ratio
- window function
- interpolation method
- polynomial order
- post smoothing
- apply to export

## Session management

### New and current sessions

At application start, a new empty session is created automatically.

Important behavior:

- recording starts only in an empty session
- `Clear` affects only the active session
- switching tabs preserves per-session state
- `Compare` becomes useful when multiple sessions are open

### Save Session

`Save Session` stores the current session as a single `.zip` archive.

A saved session may include:

- sensor data
- processed signal
- graphs
- recorded video
- session metadata
- synchronization metadata

This makes the session portable and easy to reopen later.

### Load Session

`Load Session` restores a previously saved `.zip` session.

Behavior:

- if the current session is empty, the archive is loaded into it
- if the current session already contains data, a new session tab is created

### Rename Session

Use `Edit -> Rename Session` to rename the current session.

### Autosave

If the application is closed while session data exists, autosave stores the session archive in the configured autosave directory.

## Compare mode

`Compare` is intended for multi-session analysis.

It allows you to compare sessions in different forms:

- signal comparison
- spectrogram comparison
- overlay view
- split view

Typical use cases:

- compare different haptic patterns
- compare the same scenario across builds
- inspect timing or intensity differences between sessions

## Saving outputs

In addition to full session save/load, the application supports saving selected outputs:

- **Save Graphs** — export graph images
- **Save Video** — export the recorded video file

Use these commands when you need standalone artifacts without saving the full session bundle.

## Controls

### Main graph area

- double-click on the graph or spectrogram — move playback to selected time
- mouse wheel — zoom
- left mouse drag — pan
- `Reset View` — restore full view

### Video playback

- `Space` — play/pause
- `Left` — previous step
- `Right` — next step

## Menu overview

### File

Used for session lifecycle and exports:

- new session
- close session
- save/load session
- save graphs
- save video
- exit

### Edit

- rename current session

### Connection

Used for connecting or disconnecting sensors and the target iPhone/iPad.

### View

Used for visibility of the playback window, spectrogram, and log window.

### Tools

Used for advanced signal-analysis settings such as spectrogram and interpolation.

## Notes

- perfect hardware-level synchronization is not expected because sensors and video may come from different devices
- automatic offset is an initial estimate, not an exact guarantee
- manual alignment is often required for precise inspection
- older recordings produced through legacy video pipelines may behave differently during playback
