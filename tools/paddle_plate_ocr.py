"""Read YOLO plate crops with PaddleOCR PP-OCRv4 models through ONNX Runtime.

RapidOCR supplies the local PP-OCR detector/recognizer and corrects each text
line's perspective. No plate list or character substitution is used.
"""
import re
import numpy as np
from rapidocr_onnxruntime import RapidOCR

PLATE_PATTERN = re.compile(r"\d{2}[A-Z]{1,2}\d{4,6}")
PREFIX_PATTERN = re.compile(r"\d{2}[A-Z]{1,2}\d?")


def normalize(text):
    return re.sub(r"[^A-Z0-9]", "", text.upper())


def plate_candidates(lines):
    """Combine only adjacent, aligned plate lines in the same YOLO crop."""
    observations = []
    for polygon, text, confidence in lines or []:
        points = np.asarray(polygon, dtype=float)
        x1, y1 = points.min(axis=0); x2, y2 = points.max(axis=0)
        observations.append({"text": text, "value": normalize(text), "confidence": float(confidence),
                             "box": [x1, y1, x2, y2], "cy": (y1 + y2) / 2})
    observations.sort(key=lambda item: (item["cy"], item["box"][0]))
    candidates = []
    for index, top in enumerate(observations):
        if PLATE_PATTERN.fullmatch(top["value"]) and top["confidence"] >= .70:
            candidates.append((top["value"], top["text"], top["confidence"]))
        if not PREFIX_PATTERN.fullmatch(top["value"]):
            continue
        for bottom in observations[index + 1:]:
            if not re.fullmatch(r"\d{4,5}", bottom["value"]):
                continue
            tx1, ty1, tx2, ty2 = top["box"]; bx1, by1, bx2, by2 = bottom["box"]
            height = max(ty2 - ty1, by2 - by1, 1)
            overlap = max(0, min(tx2, bx2) - max(tx1, bx1))
            aligned = overlap / max(1, min(tx2 - tx1, bx2 - bx1)) >= .5
            if not aligned or not .35 * height <= bottom["cy"] - top["cy"] <= 2 * height:
                continue
            plate = top["value"] + bottom["value"]
            confidence = min(top["confidence"], bottom["confidence"])
            if PLATE_PATTERN.fullmatch(plate) and confidence >= .70:
                candidates.append((plate, top["text"] + " / " + bottom["text"], confidence))
    return sorted(candidates, key=lambda item: item[2], reverse=True)


class PaddlePlateOCR:
    def __init__(self):
        self.engine = RapidOCR(intra_op_num_threads=2, inter_op_num_threads=1,
                               det_limit_side_len=320, det_limit_type="max", text_score=.5)

    def read(self, image):
        # RapidOCR's numpy input is BGR; the browser JPEG/Pillow image is RGB.
        pixels = np.asarray(image.convert("RGB"))[:, :, ::-1].copy()
        lines, _ = self.engine(pixels, use_cls=False)
        candidates = plate_candidates(lines)
        if candidates and len({item[0] for item in candidates}) == 1:
            return candidates[0]
        return None, " | ".join(item[1] for item in lines or []), 0
