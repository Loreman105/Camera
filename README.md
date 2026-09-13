# Local Security Camera (prototype)

A deliberately small, local-only Windows security-camera prototype. Each camera is
opened at 3840×2160 for capture; the detector is always given that original frame.
Only the recorder creates a 256×144 low-activity derivative.

## Run

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python start_camera.py
```

Click **Settings** to choose the discovered OpenCV camera indexes and recording
folder. `settings.json`, logs, and recordings are local. Install `ultralytics` to
enable YOLO detection; until then the UI explicitly shows that detection is
unavailable rather than claiming a false result.

FFmpeg is optional for this first prototype. OpenCV/MJPEG is used as a dependable
fallback. If `ffmpeg` is found, its compatible hardware encoders are reported in
the log for the next recorder iteration.

## Storage defaults

New configurations record to `D:\Recordings`. Automatic oldest-first cleanup is
enabled: recording pauses at 20% free space, then completed recordings are removed
until the drive returns to 25% free. Active files are never selected for deletion.
Low-activity recording is 144p; only confirmed-person activity uses 4K. For a
substantial further reduction in 4K event storage, install FFmpeg with an H.265
hardware encoder (NVENC, Quick Sync, or AMD AMF); the current OpenCV fallback is
intended for reliability, not maximum compression.

Recording segments are organized as:

```text
D:\Recordings\
  Camera 1 - PTZ\
    Active\
      2026-09-13\
        14-30-00_low.mp4
  Camera 2 - Webcam\
    Active\
      2026-09-13\
        14-30-00_4k.mp4
```

## Experimental USB PTZ home return (Camera 1)

The default configuration attempts a USB/UVC reconnect after 300 seconds without
a detected person. Before using it, use the Tongveo/Tenveo camera's on-screen menu
to save the desired view as **preset 1** and set **USB Setup → Wakeup Pos** to
**preset 1** (and enable UVC standby if required by its firmware). The application cannot
send a standard PTZ move over USB; this relies on the camera recalling its own
wakeup preset and briefly interrupts the Camera 1 feed. Set
`ptz_usb_wakeup_enabled` to `false` in `settings.json` to disable it.

Camera 1's live preview displays the preset target and countdown. `UVC reset SENT`
means that the application made the reconnect request; USB provides no position
feedback, so it does not prove that the camera physically reached the preset.

## Behaviour verified by code

`CameraWorker._update_detection` uses independent, per-camera consecutive-frame
counters (default 20). `SegmentRecorder.write` receives the original 4K frame and
only resizes it when the state is `LOW`. `StorageMonitor` gates recording at
`free_percent > minimum_free_percent` while capture and detection threads continue.
