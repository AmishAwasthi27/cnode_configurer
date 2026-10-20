# CNode Configurer: Project Workflow and Live Detection Handoff

This document describes the current project as implemented in this repository and the required behavior for making template-based live detection reliable. It is intended as a handoff to another coding model. Treat source code as authoritative when it differs from older prose in `README.md` or `rules.ai/`.

## 1. Project purpose

`cnode_configurer` is a Windows desktop application built with Python, PySide6/Qt, and OpenCV. It configures and monitors a cnode device. It can also run as a standalone laptop cnode: the laptop webcam is streamed locally, while the desktop app performs camera-frame inference locally.

Main entry point: `main.py`.

The application is not a web app. The GUI, camera stream client, local inference worker, and (in standalone mode) webcam MJPEG server all run on the laptop.

## 2. Start-up and runtime services

Install dependencies and launch from the project root:

```powershell
python -m pip install -r requirements.txt
python main.py
```

To open Live Detection and start its configured feed:

```powershell
python main.py --live-detection
```

On first use the connection manager stores device/service settings in `connections.json` beside the application. The default local service ports include:

| Service | Default port | Role |
| --- | ---: | --- |
| Local camera MJPEG | 8060 | Webcam frames exposed at `/camera/<camera_id>` |
| Detection MJPEG | 8061 | Handler-side detection feed; not used by the desktop local inference path |
| cnode_manager API | 8000 | Optional Django API for remote workflows, profiles, and templates |
| Supervisor | 9001 | Device/service status probe |
| RabbitMQ | 5672 | Dashboard status updates; optional `pika` dependency |

`ConnectionManager` distinguishes selecting/connecting to a host from individual ports being reachable. It periodically probes configured ports in a worker thread. In standalone laptop mode, the configured host should be `localhost` (or `127.0.0.1`). `MainWindow._ensure_local_cnode()` starts the laptop's `LocalCameraServer` and establishes the local connection. The local camera server opens the configured camera source with OpenCV, applies mirror/rotation settings, JPEG-encodes frames, and publishes the latest JPEG on port 8060. Default webcam source is index 0; the capture loop targets 15 FPS.

## 3. Main application areas

- **Dashboard**: monitoring/status UI; RabbitMQ-backed status stream is optional. It is not the inference implementation used by the Live Detection page.
- **Live Detection**: consumes the camera MJPEG feed, runs local YOLOX category proposals, then tries local template/object matching, and displays accepted matches over the feed.
- **Maintenance → Workflows**: configure workflows for a remote `cnode_manager`, with a limited local workflow fallback.
- **Maintenance → Detection Profiles**: create/configure the profile and its template images. When the API is unavailable, this page can use the embedded local manager. When the API is available, profile operations use the remote API.
- **Maintenance → Cameras**: configure a camera source and its local or handler-side controls.
- **Production/Maintenance mode**: mode switching chooses a workflow and updates app state. In standalone local mode, a local workflow can be selected without an external cnode. Remote mode switching uses the API/handler.
- **Updates / I/O Test**: supporting device-management pages, not involved in local template inference.

Relevant files:

| File | Responsibility |
| --- | --- |
| `app/main_window.py` | App composition, navigation, mode switching, local webcam server lifecycle |
| `app/managers/connection_manager.py` | Active host, service ports, connection state, asynchronous port probes |
| `app/managers/api_client.py` | Optional cnode_manager API and its availability state |
| `app/managers/local_camera_server.py` | OpenCV webcam capture and local MJPEG HTTP server |
| `app/managers/local_camera_config.py` | Local camera source/transform settings in `data/local_cameras.json` |
| `app/managers/local_manager.py` | Embedded local workflows, profiles, templates, and template matching |
| `app/widgets/mjpeg_view.py` | Background HTTP/MJPEG client; emits QImages to the GUI |
| `app/pages/live_detection.py` | Detection UI, inference thread, stream connection, result rendering |
| `app/detection/vision.py` | YOLOX ONNX decoder, COCO labels, reference matcher |
| `app/detection/store.py` | Separate local registered-object gallery persistence |
| `models/object_detection_yolox_2022nov_int8.onnx` | Bundled INT8 YOLOX COCO category proposal model |

## 4. Camera configuration and stream lifecycle

1. The user configures/selects a camera under Maintenance → Cameras. In local mode, `local_camera_config` persists camera settings under `data/local_cameras.json`.
2. Starting local cnode mode starts `LocalCameraServer` on `127.0.0.1:8060`.
3. The server captures from the configured OpenCV source, applies mirror/rotation, and exposes an MJPEG endpoint such as `http://127.0.0.1:8060/camera/camera1`.
4. `LiveDetectionPage.start_detection()` connects to the active configured host if necessary, then sets an `MJPEGView` stream source using the active camera stream port and camera ID.
5. `MJPEGView` uses its own `QThread` to read the multipart MJPEG response and emits each decoded QImage to the Qt UI thread.
6. The page converts a copy of each eligible QImage to BGR and sends it through a queued signal to `_InferenceWorker` on another QThread.
7. `_busy` allows at most one inference job at a time. Camera frames arriving while inference is busy are dropped. This is intentional backpressure; the view itself should continue showing every received camera frame. The top status FPS counts camera frames, not completed detections.
8. The result signal updates the overlay and the results list. An empty list is displayed as “No supported objects detected in this frame.”

The stream and inference are distinct: a live image proves camera delivery, not that the category model or matcher accepted an object.

## 5. Current inference algorithm (as implemented)

`LiveDetectionPage` loads `models/object_detection_yolox_2022nov_int8.onnx` into OpenCV DNN with CPU backend/target. `YoloXDetector`:

1. Converts BGR input to RGB.
2. Resizes/letterboxes it to 640×640.
3. Runs ONNX inference and decodes YOLOX grid/stride outputs into boxes and scores.
4. Maps winning class indices to the 80 COCO classes, including `banana`, `bottle`, `person`, etc.
5. Applies confidence and non-maximum suppression per class.

For each inference frame, `_InferenceWorker.infer()` currently:

1. Calls the model using `min(self.confidence, 0.15)`. The visible “Category confidence” spin box is initialized at 0.8, but the proposal threshold passed to YOLOX is capped at 0.15. Thus the shown setting is not the actual category proposal threshold.
2. Reads `local_manager.active_workflow()` and obtains profiles only from the embedded local manager. It does not query `api_client` and does not receive the profile currently selected on the Detection Profiles UI.
3. Keeps local profiles whose algorithm is `template_matching` and that have at least one local template. It chooses the default one; if none is default, it chooses the first. If there are multiple workflows/profiles, this selection can differ from what the user expects.
4. Tries to infer COCO categories from template names using `LocalManager._category_from_name()`. The name must begin with the normalized category token: `banana A` maps to `banana`; `ripe banana` maps to no category. Detections are filtered only when at least one template name in the chosen profile maps to a category. If none map, no category filter is applied; the old `bottle` UI default is not a fallback filter.
5. For every remaining YOLOX box, crops the corresponding BGR pixels from the frame.
6. If a local template profile exists, calls `local_manager.match_template(profile.id, crop, category=...)`. This uses the profile's own `data.match_threshold` (default 0.5) and its `match_method` (default `TM_CCOEFF_NORMED`). The Live Detection “Visual match threshold” spin box is bypassed in this branch.
7. `match_template()` reads each image in that local profile, checks category names when inferable, scans fixed scales `(0.25, 0.33, 0.42, 0.5, 0.6, 0.72, 0.85, 1.0, 1.15, 1.3, 1.5)`, runs OpenCV `matchTemplate`, and accepts the highest normalized score only if it reaches the profile threshold.
8. If there is no local profile with templates, the worker instead uses `ReferenceMatcher` over the separate `ReferenceStore` gallery and its ORB features/homography. In that branch the Live Detection “Visual match threshold” is used.
9. The worker discards every category proposal that fails template/reference matching. It emits only accepted matches. Therefore “No supported objects detected” conflates “YOLOX proposed no box”, “category filter excluded all boxes”, and “all template scores were under threshold.”

Template/profile files persist under `data/local_manager/` (JSON and copied template images). The separate “Register selected detection” feature stores crops under `data/detection_references/` and index JSON. These are two distinct galleries and are not interchangeable in the current worker logic.

## 6. Concrete implementation mismatches / likely causes

These are code-level findings, not guesses about a particular runtime frame:

1. **Remote/local profile split:** `DetectionProfilesPage` uses remote `cnode_manager` data whenever `api_client.available` is true; `LiveDetectionPage` always reads profiles/templates from `local_manager`. A profile/template can appear saved in the UI yet be invisible to inference if it was saved remotely.
2. **Selected profile is not passed to inference:** the worker chooses default/first local profile on every frame. The selected workflow/profile in the maintenance UI does not reliably determine which profile the detector uses.
3. **Category threshold mismatch:** the visible setting defaults to 0.8, but category proposals use `min(confidence, 0.15)`. It neither means an 80% YOLO confidence threshold nor serves as an honest indication of the current proposal threshold.
4. **Category inference/name convention:** templates infer their category only if the template name starts with a COCO class string. A banana template named without that prefix has no inferred category; after the category-filter fix, it will not be hidden by a default bottle filter, though the template matcher may still fail to identify it.
5. **Wrong visual threshold control:** local profile template matching reads `profile.data.match_threshold` (often 0.5), while the Live Detection visual threshold control (0.32 default) is only used by the separate ORB reference-gallery branch.
6. **Different matching algorithms:** the profile-template path uses raw multi-scale template matching, which is sensitive to changed orientation, illumination, crop boundaries, background, and object scale. The separate reference gallery uses ORB plus geometric verification. The UI/feature wording can make these paths appear to be one system when they are separate.
7. **Missing stage diagnostics:** the worker removes all failed proposals before emitting results. The user cannot tell which stage rejected a candidate. The worker currently emits generic exceptions only.
8. **Potential visual overlay time skew:** camera display updates independently while a slower inference result can paint boxes from an older source frame onto the current displayed image. This can make boxes look offset; it is separate from whether the candidate was detected.
9. **FPS text is not detection FPS:** it counts camera frames before inference's busy check, so “14 camera FPS” does not establish that YOLOX processed or matched 14 frames each second.

The screenshot showing a person with “No supported objects detected” is consistent with the stream working and no object passing the downstream filters. It cannot establish from pixels alone which inference stage rejected that frame.

## 7. Required end-to-end behavior for the fix

The intended use is: in Maintenance, add/select a camera, create a template-matching detection profile, save a banana reference/template, start Live Detection, point a banana at the camera, see a correctly labeled banana box/result, and then switch to Production without requiring a physical external cnode when local-laptop mode is selected.

Implementation must make the above one coherent data path:

1. **One source of truth:** use the actual selected workflow/profile and its associated templates for inference. Clearly define local-laptop mode versus remote cnode mode. In local mode, Detection Profiles saves where Live Detection reads. In remote mode, either pass/fetch remote profile and image bytes safely or explicitly copy/sync them to local inference. Never silently save to one manager and infer from another.
2. **Explicit selection:** send a stable workflow/profile identifier to the worker when selection changes. Do not choose arbitrary default/first profile per frame. Display the active profile name and source (local/API) on the detection page.
3. **Category semantics:** make category a real profile field or store it explicitly on each template. Do not rely on a template display name prefix. If category filtering is enabled, show the active accepted categories and the class proposals considered. If the user wants banana, banana must not be filtered by a stale default `bottle` value.
4. **Honest confidence controls:** separate and label detector proposal confidence from visual identity threshold. Set each threshold from its control and persist/load it consistently; no hidden hard cap that contradicts the displayed value. Preserve a reasonable low proposal threshold if needed to get candidates, then use the visual matcher as the acceptance gate.
5. **Matcher suitability:** for generic banana recognition, do not assume pixel template matching can recognize every banana pose. Use a suitable trained/category model for category proposal, then a robust appearance matcher for exact registered-object identity. Keep category detection and registered-instance identity distinct in the UI/results. Calibrate thresholds using real camera views and negative examples to avoid random matches.
6. **Stage-level explainability:** expose diagnostic states/counters for (a) frame received, (b) inference completed, (c) category proposals with class/confidence/box, (d) candidates removed by category filter, (e) template score per candidate/profile/template, and (f) final accepted match/reason for rejection. Do not make the empty result message conceal all causes.
7. **Frame/result synchronization:** carry a frame sequence number or timestamp from stream to inference result and render each result against its own frame, or explicitly state that overlays are delayed. Keep camera display live while inference runs; stale frames must not accumulate in an unbounded queue.
8. **Production local mode:** when local laptop mode is selected, Production workflow selection and mode transition must operate using the local workflow store and local camera server and must not require an external cnode. Remote mode may require an available remote handler/API and should say exactly which service is missing.
9. **Tests/evidence:** add unit tests for category mapping, correct workflow/profile selection, local-vs-remote source consistency, category filtering, threshold wiring, no-match/match behavior, and model decode. Add a repeatable camera/sample-image smoke path that logs every stage. Verify local webcam MJPEG freshness separately from inference results. Do not claim banana success without a representative image/live camera test and observable accepted match.

## 8. Suggested debugging sequence for another model

1. Read `README.md`, `app/pages/live_detection.py`, `app/pages/detection_profiles.py`, `app/managers/local_manager.py`, `app/managers/api_client.py`, `app/managers/local_camera_server.py`, and `app/detection/vision.py` before editing.
2. Determine whether the app is in local mode (`api_client.available == false`) or API mode. Log/display that state and the store from which the selected profile is loaded.
3. Verify selected camera source and confirm `/camera/<id>` delivers new frames with changing timestamps/images.
4. On a known banana frame, log raw YOLOX output class proposals, confidence values, and boxes before filters. Confirm a banana proposal reaches the next stage.
5. Log active workflow ID/name, selected profile ID/name, template names/categories/paths, and effective detector and matcher thresholds.
6. Log each category-filter decision, each template score, and the exact final acceptance/rejection reason.
7. Correct the data-source/profile/category/threshold issues before tuning image matching. Otherwise visual threshold tuning cannot fix a template that inference never loads or a banana box filtered out before matching.
8. Validate a positive banana example and a negative non-banana example, then validate continuous camera-frame freshness and Production transition in local mode.

## 9. Key acceptance criteria

- With no external cnode installed, laptop local mode can start the local webcam and show a continuously changing camera image.
- A banana template created in the active selected profile is demonstrably the exact template loaded by Live Detection.
- Showing the target banana produces a banana category proposal, that proposal survives category filtering, and the template/identity matcher accepts it with a visible score/name/box.
- Showing an unrelated object does not yield a banana identity match.
- UI thresholds correspond to the thresholds the inference worker actually uses.
- When no match occurs, the UI/log identifies the failed stage rather than reporting only an ambiguous empty state.
- Camera display remains live even when inference cannot keep up; inference drops stale work instead of freezing/queueing old frames.
- In local mode, switching to Production uses the laptop as the cnode and does not demand an external cnode connection.
