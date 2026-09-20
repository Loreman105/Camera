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

At startup, choose a mode from the dropdown:

- **One computer**: capture, detect, and process recordings on this computer.
- **Record**: capture recordings while leaving detection and verification to another computer.
- **Process**: enter the recorder computer's LAN URL and process its completed recordings on this computer.

On the first launch, choose the folder where recordings should be saved. The
choice is stored in `settings.json` and reused on later launches. You can change
it later under **Settings**; restart the application after saving. `settings.json`,
logs, and recordings are local. Install `ultralytics` to
enable YOLO detection; until then the UI explicitly shows that detection is
unavailable rather than claiming a false result.

YOLO automatically uses CUDA GPU 0 with FP16 inference when the installed
PyTorch build supports CUDA. Set `inference_device` to `"cpu"` or a GPU index
such as `"0"` in `settings.json` to override automatic selection. A CUDA-enabled
PyTorch build and compatible graphics driver are required; otherwise the app
falls back to CPU and logs the reason.

For recording, `video_encoder` defaults to `"auto"`: when FFmpeg exposes
`h264_nvenc`, recordings are encoded by NVIDIA NVENC. Set it to `"nvidia"` to
prefer NVENC explicitly, or `"cpu"` to force the portable OpenCV writer. If
FFmpeg, NVENC, or the driver is unavailable, the app logs the reason and falls
back to OpenCV rather than stopping capture.

## View video over the LAN

The remote viewer is enabled in `settings.json` and serves the newest frame
from each camera as an MJPEG stream. Start the application, find the camera
PC's private address with `ipconfig`, then open this URL from another device on
the same network:

```text
http://<camera-pc-lan-ip>:8765/
```

For example, if the camera PC has address `192.168.1.50`, open
`http://192.168.1.50:8765/`. Individual streams are available at `/stream/0`
and `/stream/1`; `/snapshot` returns the first camera's current JPEG frame.
Allow TCP port `8765` through Windows Firewall when prompted. The server binds
to `0.0.0.0`, so it accepts LAN connections; keep the network private because
the viewer has no login.

## Process recordings on a second computer

Install the project and its dependencies, copy `yolo11n.pt` to the second
computer, and connect both computers to the same LAN. On the camera computer,
run recording-only mode to start capture without local detection:

```powershell
python start_camera.py --recording-only
```

On the processing computer, run this from the project folder:

```powershell
python process_recordings.py --server http://<camera-pc-lan-ip>:8765
```

The same operation is available through the startup dropdown by choosing
**Process** and entering that URL.

Multiple processor computers can run at the same time. The recorder assigns
each completed clip to one processor and reports active processor heartbeats;
the Process window also reports the connected count. In Settings, set
**Processing GPUs** to `auto`, `all`, or an explicit list such as `0,1`. `all`
creates one processing worker per detected CUDA GPU on that computer.

The processor downloads completed clips one at a time, checks them with its
local YOLO model, and sends the result back. Clips with a person remain in
`Active`; person-free 4K clips are converted to the configured low-resolution
format in `Inactive`. Open clips are skipped and become available after the
recorder closes them. The processor command can be run again later to process
new clips.

## Storage defaults

New configurations record to `D:\Recordings`. Automatic oldest-first cleanup is
enabled: recording pauses at 20% free space, then completed recordings are removed
until the drive returns to 25% free. Active files are never selected for deletion.
Low-activity recording is 144p; only confirmed-person activity uses 4K. For a
substantial further reduction in 4K event storage, install FFmpeg with an H.265
hardware encoder (NVENC, Quick Sync, or AMD AMF); the current OpenCV fallback is
intended for reliability, not maximum compression.

Completed recordings are periodically sampled again with the same YOLO model.
This second pass also runs over recordings already present when the application
starts and moves each file to `Active` or `Inactive` based on the sampled frames.
Files remain unchanged when YOLO is unavailable or cannot open the recording.

Use **DOUBLE-CHECK RECORDINGS** as a toggle to exhaustively inspect every frame
in completed `Active/*.mp4` recordings beneath `D:\Recordings`. While it is
running, live detection and routine background verification pause on every
camera so the manual pass has exclusive use of the inference hardware. Open
segments are excluded. Clips containing a person remain in `Active`; clips with
no person are transcoded to 256×144 in `Inactive`, then their original active
4K file is deleted. The button changes to **STOP DOUBLE-CHECK** while running
and can be pressed again to cancel between frames.

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
