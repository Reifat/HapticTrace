[Русская версия](./../ru/troubleshooting.md)

# Troubleshooting

Common `HapticTrace` issues and the basic checks for each case.

## phyphox

### `Connect Sensors` does nothing

Check:

- the Mac and the sensor device are on the same network
- the URL is complete, including the port
- remote access is enabled in `phyphox`
- the `phyphox` experiment is running

### Errors such as `502`, `connection refused`, `max retries exceeded`

The cause is usually one of the following:

- incorrect URL
- the experiment is not running
- the device is not reachable over the network
- the remote access endpoint is no longer valid

### No sensor data appears on the graph

Check:

- the required sensors are enabled in the experiment
- measurement is actually running
- a sensor-enabled mode is selected
- sensor connection was established successfully

## iPhone / iPad capture

### `Connect iPhone` does not find the device

Check:

- USB connection
- trust relationship between the device and the Mac
- whether macOS sees the device as a capture source
- whether another application is already using the capture device

### No live preview

Check:

- Camera access is enabled for `HapticTrace` in `System Settings > Privacy & Security > Camera`
- the device was reconnected
- other applications using the capture stack are closed
- the connection was reopened from `HapticTrace`
- a video-enabled mode is selected
- the runtime log `~/Library/Logs/HapticTrace/HapticTrace.log` contains `First iPhone video frame received`

### Video does not start recording

Check:

- preview has already produced at least one frame
- the iPhone/iPad is connected
- a video-enabled mode is selected
- macOS permissions are not blocking capture

### The application starts, but capture does not begin

The cause is usually one of the following:

- missing macOS permissions
- capture stack is unavailable for the device
- macOS does not see the device correctly

If the window shows `connected; waiting for video frames`, the device connection was created but the first frame has not arrived yet. Check Camera permission for `HapticTrace` first, then inspect `~/Library/Logs/HapticTrace/HapticTrace.log`.

## Synchronization

### Video and sensor data are visibly misaligned

This is acceptable to some degree because video and sensor data may come from different devices.

What to do:

- open `Offset Settings`
- adjust `manual offset`
- align to a visible screen event or a clear signal peak

### Cursor or playback behaves incorrectly

Check:

- `Reset View` was applied
- the required zoom range was set again
- the current session contains valid data

## Save and load

### Session does not save

Check:

- the directory is writable
- there is enough free disk space
- the session contains data
- the autosave or manual save path is valid

### Session does not load

Check:

- the file is a valid `.zip` session
- the archive is not corrupted
- the archive contains the expected data
- the required playback stack is available in the system

## Basic checks

Before investigating further, check:

- the correct recording mode is selected
- the required devices are connected
- the `phyphox` URL is correct
- macOS has the required permissions
- the current session state matches the intended workflow

## Notes

- `sensor path` and `video path` are independent and may fail separately
- `automatic offset` is an initial estimate, not exact synchronization
