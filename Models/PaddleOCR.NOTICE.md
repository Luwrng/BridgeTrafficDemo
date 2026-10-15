# PaddleOCR PP-OCRv4 model attribution

The local OCR uses the PaddleOCR PP-OCRv4 detection and recognition ONNX models
distributed in `rapidocr-onnxruntime` 1.4.4 from PyPI. RapidOCR is Apache-2.0;
the wheel retains its upstream license files. No inference images are sent to
RapidAI, PaddlePaddle, PyPI, or another external service.

Sources:

- [PaddleOCR project](https://github.com/PaddlePaddle/PaddleOCR)
- [RapidOCR 1.4.4 package](https://pypi.org/project/rapidocr-onnxruntime/1.4.4/)
- [RapidOCR API documentation](https://rapidai.github.io/RapidOCRDocs/v1.4.4/install_usage/api/RapidOCR/)

Model files under `artifacts/ocr-python-deps/rapidocr_onnxruntime/models`:

| File | SHA-256 |
| --- | --- |
| ch_PP-OCRv4_det_infer.onnx | d2a7720d45a54257208b1e13e36a8479894cb74155a5efe29462512d42f49da9 |
| ch_PP-OCRv4_rec_infer.onnx | 48fc40f24f6d2a207a2b1091d3437eb3cc3eb6b676dc3ef9c37384005483683b |
| ch_ppocr_mobile_v2.0_cls_infer.onnx | e47acedf663230f8863ff1ab0e64dd2d82b838fceb5957146dab185a89d6215c |

The direction classifier is included by the package but is disabled for the
YOLO crops. No license plate strings are embedded in application code.
