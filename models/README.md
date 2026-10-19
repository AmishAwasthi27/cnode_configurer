# Local category detector

`object_detection_yolox_2022nov_int8.onnx` is the OpenCV Zoo YOLOX COCO object
detection model, INT8 quantized. It is loaded by OpenCV DNN on CPU. The model
recognizes the 80 COCO classes, including `bottle`.

- Upstream model card and demo: <https://huggingface.co/opencv/object_detection_yolox>
- OpenCV Zoo model source: <https://github.com/opencv/opencv_zoo/tree/main/models/object_detection_yolox>
- License: Apache-2.0 (see the upstream model directory's `LICENSE`).

The bundled weights are 9,079,452 bytes. Replace this file only with a compatible
YOLOX COCO ONNX model or update `YoloXDetector` and the corresponding model
metadata together.
