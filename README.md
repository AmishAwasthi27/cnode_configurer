# cnode_configurer

PySide6 (QtWidgets) desktop application used to configure and monitor a cnode device running
[`cnode_handler`](../cnode_handler/README.md). It ships with a **Dashboard** for live
monitoring, **Live Detection** for local category and reference-object recognition,
a **Configure** section (Workflows, Detection Profiles, Cameras), and an **Updates**
page driven by the planned DAKPAL / cnode_manager services.

## Quick start

```bash
cd cnode_configurer
python -m pip install -r requirements.txt
python main.py
```

To open directly into the running camera detection screen:

```bash
python main.py --live-detection
```

The app targets **Python 3.10+**. `pika` is optional — without it the live RabbitMQ
status feed is disabled, but every other page works.

At startup the app immediately selects this laptop as its local cnode and starts the
local webcam MJPEG server on port 8060. It probes the saved remote manager endpoint in
the background; a reachable remote cnode replaces local mode, while a missing or
unreachable remote leaves the laptop active without a connection prompt. The local
detection workflow does not depend on a separate detection server on port 8061.

## Configuration

A `connections.json` file is created next to the app on first launch. It stores the
target device host and all service ports:

| Service | Default port |
|---------|--------------|
| Camera stream (MJPEG) | 8060 |
| Detection stream (MJPEG) | 8061 |
| Supervisor | 9001 |
| RabbitMQ | 5672 |
| SSH | 22 |
| cnode_manager (Django API) | 8000 |

Open **Connection → Configure connection…** (or click the connection chip in the top
bar) to edit the host, ports, and RMQ credentials.

## Layout

- **Menu bar** — Connection, Settings, Help, About.
- **Top bar** — Logo, connection status, current mode chip, mode switch, notifications,
  account. Its background turns *grayish navy blue* in **Production** mode and
  *amber yellow* in **Maintenance** mode.
- **Sidebar** — Dashboard, Live Detection, Configure ▾ (Workflows, Detection Profiles, Cameras — only
  when Maintenance mode is active), Updates.
- **Status bar** — Live summary of reachable services.

## Pages

### Dashboard
- Frozen header with **Live Inputs** (left) and **Live Outputs** (right), fed from
  the RabbitMQ `moderindustry.status` stream.
- Two rows, one per camera, each showing the MJPEG detection stream plus a text
  readout of the latest `MatchResult` entries.

### Live Detection
- Click **Start live detection**. For a local camera, leave the connection host as
  `localhost`; the app starts its single webcam capture service and reads the stream.
- The bundled OpenCV Zoo YOLOX model recognizes the 80 COCO classes, including
  bottles and bananas. Live Detection accepts a model proposal only when it passes
  the visual identity matcher. Its category pre-filter is inferred from recognizable
  COCO class prefixes in the profile's template names; if no category can be
  inferred, no category pre-filter is applied. Live inference currently chooses the
  default local template-matching profile (or the first local profile with templates)
  for the active local workflow; it does not yet follow the profile card selected in
  Maintenance, and profiles saved only to a remote manager are not read by this local
  inference path.
- Select a detection and save a name to register it. Entering the same name later
  adds another reference image to that object. Reference images and profile settings
  persist under the local `data/` folder.
- The matcher reports **Likely exact visual match** only when local features align
  geometrically; similar-category objects with weak evidence remain **identity
  unverified**. Visual matching cannot guarantee physical identity in every view.
- The legacy workflow/profile pages still use cnode_manager and the configured
  handler services. Live Detection is a separate local inference path for a
  workstation camera or a reachable cnode MJPEG feed.

### Workflows (Maintenance)
- Left panel lists workflows, right panel shows a flow chart of
  **Trigger → Detection → Detection profiles → Relay outputs** and an editor for
  the workflow's settings.

### Detection Profiles (Maintenance)
- Grid of profile cards (algorithm, match method, threshold, template paths).

### Cameras (Maintenance)
- Left list of cameras, right panel with the live feed plus source / resize /
  mirror / rotate controls.
- **Perspective transforms are per-workflow-per-camera** and live on the
  Workflows page; each workflow's camera row has its own 4-point picker plus
  `dst_width` / `dst_height` output-size selectors.

### Updates
- Device firmware and app software rows with check/install actions. Installation
  calls out to the cnode_manager service, which uses DAKPAL under the hood.

## Architecture

```text
cnode_configurer/
├── main.py                # QApplication entry point
├── requirements.txt
├── connections.json       # auto-generated on first run
└── app/
    ├── constants.py       # ports, paths, accent colors
    ├── state.py           # AppState (mode, accent) with signals
    ├── theme.py           # QSS builder + apply_theme()
    ├── main_window.py     # Menu + top bar + sidebar + stacked pages
    ├── managers/          # Connection, Notification, Account, Status (RMQ), API client
    ├── widgets/           # TopBar, Sidebar, Card, MJPEGView, popup dialogs
    └── pages/             # Dashboard, Live Detection, Workflows, Detection Profiles, Cameras, Updates
```

### Managers

| Manager | Responsibility |
|---------|----------------|
| `ConnectionManager` | Loads/saves `connections.json`, probes every service port every 5 s, exposes `connection_changed` / `status_changed` signals. |
| `NotificationManager` | In-memory notification list + unread counter. |
| `AccountManager` | Simple login state (username + role). |
| `StatusManager` | Background `pika` consumer for `moderindustry.status`. Gracefully no-ops when `pika` is not installed or the broker is offline. |
| `CnodeManagerAPI` | JSON HTTP client pointing at the cnode_manager Django service on port 8000. All methods return `None` on failure so the UI can fall back to demo data. |

### MJPEG streaming

`app/widgets/mjpeg_view.py` implements a `QThread`-backed reader for
`multipart/x-mixed-replace` streams. It targets the endpoints exposed by
`cnode_handler.camera_streamer` (paths like `/camera/camera1` on port 8060 and
`/detection/camera1` on port 8061).

## Live camera freeze: cause and fix

The local webcam capture loop previously compared each frame pixel-for-pixel with the
previous frame. If two consecutive frames were identical for two seconds, it treated
that as a stalled camera, released the webcam, cleared the latest JPEG, waited, and
reopened the device. A valid still scene can produce identical frames, so the loop
could repeatedly clear the stream. During those gaps, `/camera/<camera_id>` returned
HTTP 503 (“No frame available from the PC webcam”), and Live Detection appeared
frozen or disconnected.

The fix removes the pixel-equality watchdog. The capture loop now keeps publishing
valid frames even when the scene is unchanged, and only reopens the camera when
`VideoCapture.read()` fails or the camera settings change. Successful camera opens
and failed reads are logged for diagnosis.

The MJPEG viewer also used to queue every decoded frame for GUI presentation. If the
UI was busy, it could lag behind the live camera. It now stores only the newest frame
in a thread-safe mailbox and presents it on the GUI thread at up to about 30 FPS,
discarding stale intermediate frames. The stream worker's stop signal now calls
`QThread.quit()` directly, avoiding a GUI-thread wait cycle during stream shutdown.

The local-cnode startup fallback is also implemented: startup selects the laptop and
starts its local camera/backend immediately, then probes a configured remote manager
asynchronously. Local mode suppresses creation of a `cnode_manager_sdk` client for
`localhost:8000`, so local workflow/profile pages use `LocalManager` instead of
mistaking an unresponsive API server for an available manager.

### Camera verification performed

On Windows, OpenCV camera index `0` successfully opened through DirectShow and returned
`1280×720` frames. The project camera server then kept JPEG frames available over
several seconds. The relaunched application was opened directly into Live Detection;
its local endpoint returned HTTP 200 with
`multipart/x-mixed-replace; boundary=frame`, and four successive JPEG frames included
three distinct images. This verifies changing camera frames at the stream endpoint;
it does not by itself prove that every object template will match.

## Tested flows

- App starts in local laptop cnode mode and probes a configured remote manager in the
  background; unreachable remotes leave the local camera and local manager active.
- Switching to Maintenance mode reveals the Configure subsections and re-applies the
  amber accent across the UI.
- Connection dialog changes are persisted to `connections.json` and immediately re-probe.
- Detection unit tests cover reference persistence, additional views, category outcomes,
  and loading/running the bundled YOLOX ONNX model.

During the camera-freeze investigation, the app logged that `cnode_manager_sdk` could
not import `CNodeManager` and entered offline mode. This does not prevent laptop-local
camera streaming or `LocalManager` operation; remote manager-backed features require a
working SDK installation and reachable service.
