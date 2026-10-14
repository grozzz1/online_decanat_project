"""Conservative visual candidates, not authenticity checks or calibrated probabilities.

Coordinates are [x1, y1, x2, y2] in the supplied RGB page, numbered from one.
OCR words use the same coordinate frame. OpenCV preprocessing never changes OCR input.
"""
from difflib import SequenceMatcher
from math import pi
from pathlib import Path
import re

import cv2
import numpy as np
from PIL import Image


MAX_SIDE = 1600
ACCEPT_SCORE = 0.42
CONFIDENT_SCORE = 0.70


def _normalized(text):
    return re.sub(r"[^а-яa-z]", "", text.lower().replace("ё", "е"))


def _matches(text, choices):
    return any(SequenceMatcher(None, text, choice).ratio() >= 0.78 for choice in choices)


def find_signature_anchors(words, width, height):
    """Group OCR lines; explicit student/applicant + signature labels determine role."""
    lines = {}
    for word in words:
        if word.get("confidence", 0) >= 25:
            lines.setdefault(tuple(word.get("line", (0, 0, 0))), []).append(word)
    anchors = []
    for line in lines.values():
        # Limit context to neighbouring words; unrelated labels on a wide row must not merge.
        line.sort(key=lambda word: word["bbox"][0])
        for index, word in enumerate(line):
            value = _normalized(word["text"])
            signature = _matches(value, ("подпись", "подписъ", "signature"))
            person = _matches(value, ("студент", "студента", "заявитель", "заявителя"))
            if not signature and not person:
                continue
            x1, y1, x2, y2 = word["bbox"]
            text_height = max(1, y2 - y1)
            nearby = [other for other in line[max(0, index - 2):index + 3]
                      if max(0, other["bbox"][0] - x2, x1 - other["bbox"][2]) < text_height * 6]
            values = [_normalized(other["text"]) for other in nearby]
            student = any(_matches(v, ("студент", "студента", "заявитель", "заявителя")) for v in values)
            has_signature = any(_matches(v, ("подпись", "signature")) for v in values)
            staff = any(_matches(v, ("ректор", "декан", "сотрудник", "преподаватель")) for v in values)
            if not signature and (not person or y1 < height * .40):
                continue
            box = [min(other["bbox"][0] for other in nearby), min(other["bbox"][1] for other in nearby),
                   max(other["bbox"][2] for other in nearby), max(other["bbox"][3] for other in nearby)]
            margin = max(width * .35, text_height * 12)
            specimen = any(_matches(v, ("образец", "пример", "sample", "specimen")) for v in values)
            anchors.append({"bbox": box, "specimen": specimen,
                            "role": "student" if student and has_signature and not staff and not specimen else "unknown",
                            "roi": [max(0, box[0] - margin), max(0, box[1] - text_height * 8),
                                    min(width, box[2] + margin), min(height, box[3] + text_height * 8)]})
    return anchors


def _overlap(a, b):
    area = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))
    smaller = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))
    return area / max(1, smaller)


def _deduplicate(detections):
    kept = []
    for candidate in sorted(detections, key=lambda item: item["confidence"], reverse=True):
        if not any(candidate["type"] == other["type"] and candidate["page"] == other["page"]
                   and _overlap(candidate["bbox"], other["bbox"]) > .55 for other in kept):
            kept.append(candidate)
    return kept[:40]


def _candidate(kind, box, score, page, evidence, role=None):
    result = {"type": kind, "detected": True, "bbox": [int(v) for v in box],
              "confidence": round(float(min(.95, score)), 3), "page": page,
              "status": "detected" if score >= CONFIDENT_SCORE else "uncertain",
              "evidence": {key: round(float(value), 3) for key, value in evidence.items()}}
    if role is not None:
        result["role"] = role
        result["role_status"] = "contextual" if role == "student" else "unknown"
    return result


def _stamp_candidates(rgb, ink, page):
    height, width = ink.shape
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    preferred = cv2.inRange(hsv, (85, 45, 30), (165, 255, 245))
    # Saturated red/green inks are also considered, with a lower colour bonus.
    coloured = cv2.inRange(hsv, (0, 55, 25), (179, 255, 245))
    detections = []
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    for mask, colour in ((coloured, True), (ink, False)):
        closed = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
        contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for contour in contours:
            x, y, w, h = cv2.boundingRect(contour)
            if not (.035 * min(width, height) <= min(w, h) and max(w, h) <= .35 * max(width, height)):
                continue
            if not .50 <= w / h <= 2.0 or len(contour) < 12:
                continue
            area, perimeter = cv2.contourArea(contour), cv2.arcLength(contour, True)
            if area <= 0 or perimeter <= 0:
                continue
            roundness = min(1, 4 * pi * area / perimeter ** 2)
            ellipse = cv2.fitEllipse(contour)
            (cx, cy), (ew, eh), angle = ellipse
            if min(ew, eh) <= 0:
                continue
            points = contour[:, 0, :].astype(float) - (cx, cy)
            radians = np.deg2rad(angle)
            px = points[:, 0] * np.cos(radians) + points[:, 1] * np.sin(radians)
            py = -points[:, 0] * np.sin(radians) + points[:, 1] * np.cos(radians)
            radius = np.sqrt((px / (ew / 2)) ** 2 + (py / (eh / 2)) ** 2)
            fit = max(0, 1 - float(np.mean(np.abs(radius - 1))) * 4)
            if fit < .68 or roundness < .38 or area / (w * h) < .45:
                continue
            inset = mask[y + h // 4:y + 3 * h // 4, x + w // 4:x + 3 * w // 4]
            interior = cv2.countNonZero(inset) / max(1, inset.size)
            density = cv2.countNonZero(mask[y:y + h, x:x + w]) / (w * h)
            if density > .65:  # solid logos / coloured blobs
                continue
            blue = cv2.countNonZero(preferred[y:y + h, x:x + w]) / max(1, cv2.countNonZero(mask[y:y + h, x:x + w]))
            # Interior ink gives evidence of lettering or nested rings; plain circles remain uncertain.
            score = .20 + .18 * roundness + .18 * fit + .18 * min(1, interior / .08)
            score += (.13 if blue > .5 else .07) if colour else 0
            if interior < .008:
                score = min(score, .55)
            detections.append(_candidate("stamp", [x, y, x + w, y + h], score, page,
                                         {"circularity": roundness, "ellipse_fit": fit,
                                          "interior_ink": interior, "colour": colour, "preferred_ink": blue}))
    # Broken monochrome rings: Hough proposes circles, annular angular coverage verifies them.
    circles = cv2.HoughCircles(cv2.GaussianBlur(ink, (5, 5), 1), cv2.HOUGH_GRADIENT, dp=1.5,
                               minDist=max(30, min(width, height) // 12), param1=100, param2=45,
                               minRadius=max(15, int(min(width, height) * .025)),
                               maxRadius=max(16, int(min(width, height) * .16)))
    if circles is not None:
        gx = cv2.Sobel(ink, cv2.CV_32F, 1, 0, ksize=3)
        gy = cv2.Sobel(ink, cv2.CV_32F, 0, 1, ksize=3)
        for cx, cy, radius in circles[0][:30]:
            x1, y1 = max(0, int(cx - radius)), max(0, int(cy - radius))
            x2, y2 = min(width, int(cx + radius) + 1), min(height, int(cy + radius) + 1)
            yy, xx = np.nonzero(ink[y1:y2, x1:x2])
            dx, dy = xx + x1 - cx, yy + y1 - cy
            distances = np.sqrt(dx * dx + dy * dy)
            ring = (distances > radius * .88) & (distances < radius * 1.10)
            angles = ((np.arctan2(dy[ring], dx[ring]) + pi) / (2 * pi) * 24).astype(int) % 24
            coverage = len(np.unique(angles)) / 24
            # Text rows can fill every angular bin. A true rim also has radial edge normals.
            edge_y, edge_x = np.nonzero((gx[y1:y2, x1:x2] ** 2 + gy[y1:y2, x1:x2] ** 2) > 10000)
            edx, edy = edge_x + x1 - cx, edge_y + y1 - cy
            edge_radius = np.sqrt(edx ** 2 + edy ** 2)
            rim = (edge_radius > radius * .94) & (edge_radius < radius * 1.06)
            vx, vy = gx[edge_y + y1, edge_x + x1], gy[edge_y + y1, edge_x + x1]
            alignment = np.abs(vx * edx + vy * edy) / np.maximum(1, np.sqrt(vx ** 2 + vy ** 2) * edge_radius)
            radial_fraction = np.count_nonzero(rim & (alignment > .90)) / max(1, np.count_nonzero(rim))
            interior = np.count_nonzero(distances < radius * .6) / max(1, pi * (radius * .6) ** 2)
            if coverage >= .75 and radial_fraction >= .65 and .012 <= interior <= .50:
                detections.append(_candidate("stamp", [x1, y1, x2, y2], .43 + .15 * coverage, page,
                                             {"ring_coverage": coverage, "radial_edges": radial_fraction,
                                              "interior_ink": interior, "hough": 1}))
    return _deduplicate(detections)


def _signature_candidates(ink, words, stamps, anchors, page):
    height, width = ink.shape
    remaining = ink.copy()
    horizontal = cv2.morphologyEx(remaining, cv2.MORPH_OPEN, np.ones((1, max(25, width // 18)), np.uint8))
    vertical = cv2.morphologyEx(remaining, cv2.MORPH_OPEN, np.ones((max(25, height // 18), 1), np.uint8))
    rules = cv2.bitwise_or(horizontal, vertical)
    remaining[rules > 0] = 0
    # Restore intersections locally, without restoring the entire blank rule.
    support = cv2.dilate(remaining, np.ones((5, 3), np.uint8))
    remaining[(rules > 0) & (support > 0)] = ink[(rules > 0) & (support > 0)]
    _, labels, ink_stats, _ = cv2.connectedComponentsWithStats(remaining, connectivity=8)
    for word in words:
        if word.get("confidence", 0) >= 55:
            x1, y1, x2, y2 = word["bbox"]
            # OCR sometimes reads a piece of handwriting as a word. Preserve it
            # only when an irregular connected stroke extends beyond that box.
            ids = np.unique(labels[max(0, y1):y2, max(0, x1):x2])
            text_height = max(1, y2 - y1)
            handwriting = any(
                stat[cv2.CC_STAT_WIDTH] > text_height * 3
                and stat[cv2.CC_STAT_HEIGHT] > text_height * 1.4
                and (stat[cv2.CC_STAT_TOP] < y1 - text_height * .2
                     or stat[cv2.CC_STAT_TOP] + stat[cv2.CC_STAT_HEIGHT] > y2 + text_height * .2)
                for index in ids if index for stat in [ink_stats[index]])
            if handwriting and any(_overlap(word["bbox"], a["roi"]) > .9
                                   and _overlap(word["bbox"], a["bbox"]) < .1 for a in anchors):
                continue
            remaining[max(0, y1 - 2):min(height, y2 + 2), max(0, x1 - 2):min(width, x2 + 2)] = 0
    for stamp in stamps:
        x1, y1, x2, y2 = stamp["bbox"]
        remaining[y1:y2, x1:x2] = 0
    # Group on the full page before applying contextual ROIs. Cropping first
    # created partial candidates which could win deduplication over a full box.
    rois = [[0, 0, width, height]]
    detections = []
    for roi in rois:
        rx1, ry1, rx2, ry2 = [int(v) for v in roi]
        crop = remaining[ry1:ry2, rx1:rx2]
        if not crop.size:
            continue
        grouped = cv2.dilate(crop, np.ones((max(3, height // 300), max(5, width // 85)), np.uint8))
        contours, _ = cv2.findContours(grouped, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for contour in contours:
            x, y, w, h = cv2.boundingRect(contour)
            # Dilation joins nearby components; its artificial border is not ink.
            yy, xx = np.nonzero(crop[y:y + h, x:x + w])
            if not len(xx):
                continue
            x, y, w, h = x + int(xx.min()), y + int(yy.min()), int(xx.max() - xx.min() + 1), int(yy.max() - yy.min() + 1)
            if not (width * .04 < w < width * .48 and height * .012 < h < height * .14 and 1.3 < w / h < 12):
                continue
            piece = crop[y:y + h, x:x + w]
            count, _, stats, _ = cv2.connectedComponentsWithStats(piece, connectivity=8)
            components = [stat for stat in stats[1:] if stat[cv2.CC_STAT_AREA] >= 4]
            if not components or len(components) > 35:
                continue
            density = cv2.countNonZero(piece) / (w * h)
            longest = max(stat[cv2.CC_STAT_WIDTH] for stat in components) / w
            heights = [stat[cv2.CC_STAT_HEIGHT] for stat in components]
            variation = float(np.std(heights) / max(1, np.mean(heights)))
            # Separate regular glyphs, isolated characters, and line remnants are weak evidence.
            if not .025 < density < .42 or longest < .45:
                continue
            occupied_rows = np.count_nonzero(np.count_nonzero(piece, axis=1) > 0) / h
            if occupied_rows < .65:
                continue
            box = [x + rx1, y + ry1, x + rx1 + w, y + ry1 + h]
            nearby = [anchor for anchor in anchors if _overlap(box, anchor["roi"]) > .75
                      and _overlap(box, anchor["bbox"]) < .20]
            nearby.sort(key=lambda a: abs((a["bbox"][0] + a["bbox"][2]) / 2 - (box[0] + box[2]) / 2) / width
                        + abs((a["bbox"][1] + a["bbox"][3]) / 2 - (box[1] + box[3]) / 2) / height)
            role = nearby[0]["role"] if nearby else "unknown"
            # Conflicting labels cannot establish student ownership.
            if nearby and any(a["role"] != role and _overlap(nearby[0]["bbox"], a["bbox"]) > .5 for a in nearby[1:]):
                role = "unknown"
            score = .25 + .20 * longest + .10 * min(1, variation) + .10 * occupied_rows
            score += .18 if nearby else 0
            if not nearby:
                score = min(score, .60)
            if nearby and nearby[0].get("specimen"):
                score = min(score, .60)
            if box[1] < height * .45 and not nearby:
                continue
            padding = max(2, round(max(width, height) * .002))
            box = [max(0, box[0] - padding), max(0, box[1] - padding),
                   min(width, box[2] + padding), min(height, box[3] + padding)]
            clipped = box[0] == 0 or box[1] == 0 or box[2] == width or box[3] == height
            if clipped:
                score = min(score, .60)
            detections.append(_candidate("signature", box, score, page,
                                         {"connected_width": longest, "height_variation": variation,
                                          "ink_density": density, "occupied_rows": occupied_rows,
                                          "label_context": bool(nearby), "boundary_clipped": clipped}, role))
    return _deduplicate(detections)


def summarize_visuals(detections, pages, warnings=None):
    def best(kind, role=None):
        choices = [item for item in detections if item["type"] == kind and (role is None or item.get("role") == role)]
        if choices:
            return dict(max(choices, key=lambda item: item["confidence"]))
        return {"detected": False, "bbox": None, "confidence": 0.0, "page": None, "status": "not_detected"}
    unknown = any(item["type"] == "signature" and item.get("role") == "unknown" for item in detections)
    student = best("signature", "student")
    if not student["detected"] and unknown:
        student["status"] = "unknown"
    return {"stamp": best("stamp"), "student_signature": student, "signature": best("signature"),
            "detections": detections, "pages": pages, "warnings": warnings or [],
            "score_kind": "heuristic_v1", "authenticity_verified": False}


def analyze_document_visuals(image: Image.Image, ocr_words=None, page=1, debug_path=None) -> dict:
    """Analyze one PIL RGB page independently; OCR is optional, role then remains unknown."""
    if not isinstance(image, Image.Image) or min(image.size) < 32:
        raise ValueError("Ожидается изображение PIL размером не меньше 32 × 32.")
    original_width, original_height = image.size
    scale = min(1, MAX_SIDE / max(image.size))
    width, height = max(32, round(original_width * scale)), max(32, round(original_height * scale))
    rgb = np.asarray(image.convert("RGB").resize((width, height), Image.Resampling.LANCZOS))
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    contrast = cv2.createCLAHE(clipLimit=2, tileGridSize=(8, 8)).apply(gray)
    denoised = cv2.GaussianBlur(contrast, (3, 3), 0)
    ink = cv2.adaptiveThreshold(denoised, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 31, 12)
    sx, sy = width / original_width, height / original_height
    words = [{**word, "bbox": [round(word["bbox"][0] * sx), round(word["bbox"][1] * sy),
                               round(word["bbox"][2] * sx), round(word["bbox"][3] * sy)]} for word in (ocr_words or [])]
    anchors = find_signature_anchors(words, width, height)
    stamps = _stamp_candidates(rgb, ink, page)
    detections = [item for item in stamps + _signature_candidates(ink, words, stamps, anchors, page)
                  if item["confidence"] >= ACCEPT_SCORE]
    for item in detections:
        box = item["bbox"]
        item["bbox"] = [max(0, int(np.floor(box[0] / sx))), max(0, int(np.floor(box[1] / sy))),
                        min(original_width, int(np.ceil(box[2] / sx))), min(original_height, int(np.ceil(box[3] / sy)))]
    result = summarize_visuals(detections, [{"page": page, "width": original_width, "height": original_height,
                                           "analysis_status": "complete"}])
    if debug_path is not None:
        save_debug_image(image, result, debug_path, page)
    return result


def save_debug_image(image, result, path, page=1):
    canvas = cv2.cvtColor(np.asarray(image.convert("RGB")), cv2.COLOR_RGB2BGR)
    for detection in result["detections"]:
        if detection["page"] != page:
            continue
        x1, y1, x2, y2 = detection["bbox"]
        colour = (0, 180, 0) if detection["type"] == "stamp" else (230, 100, 0)
        if detection["status"] == "uncertain":
            colour = (0, 200, 230)
        cv2.rectangle(canvas, (x1, y1), (x2, y2), colour, 2)
        label = f"{detection['type']} {detection.get('role', '')} {detection['confidence']:.2f}"
        cv2.putText(canvas, label, (x1, max(15, y1 - 5)), cv2.FONT_HERSHEY_SIMPLEX, .5, colour, 1)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    # imencode + pathlib support non-ASCII Windows paths.
    ok, encoded = cv2.imencode(".png", canvas)
    if not ok:
        raise OSError("Не удалось сохранить отладочное изображение.")
    target.write_bytes(encoded.tobytes())
