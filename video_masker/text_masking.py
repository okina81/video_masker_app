"""OCR を使ったテキスト（個人情報）マスキングのパイプライン。

設計方針（テキスト追従パイプライン）:
- OCR を N フレームおきに実行して「真の座標」を供給する
- CSRT は OCR 間の短期追跡を担当する
- Kalman フィルターは BBox の平滑化・予測補完を担当する
- IoU でトラッカーと検出結果を紐付ける
- オプティカルフローでカメラの動き量を推定しオフセット補正する
- マスク領域を少し膨張（dilate）させて漏れを防ぐ
- シーンチェンジ検出で全トラッカーをリセットし即再検出する

Tesseract / pytesseract が無い環境では検出が空になり、安全に無効化される。
"""

import cv2
import numpy as np

from video_masker.motion import SceneChangeDetector, estimate_camera_motion, to_gray
from video_masker.tracking import (
    _box_from_state,
    _make_kalman,
    _measurement,
    create_tracker,
)


def is_ocr_available():
    """pytesseract と Tesseract 本体が使えるかどうかを返す。"""
    try:
        import pytesseract

        pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False


def detect_text_boxes(frame, min_conf=45, lang="jpn+eng"):
    """1フレームに対し OCR を実行し、文字領域の (x, y, w, h) 一覧を返す。

    pytesseract / Tesseract が無い、または言語データが無い場合は空リスト。
    """
    try:
        import pytesseract
        from pytesseract import Output
    except Exception:
        return []

    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    try:
        data = pytesseract.image_to_data(rgb, lang=lang, output_type=Output.DICT)
    except Exception:
        # 指定言語データが無い場合などは英語のみで再試行
        try:
            data = pytesseract.image_to_data(rgb, output_type=Output.DICT)
        except Exception:
            return []

    boxes = []
    n = len(data.get("text", []))
    for i in range(n):
        txt = (data["text"][i] or "").strip()
        if not txt:
            continue
        conf_raw = data["conf"][i]
        try:
            conf = float(conf_raw)
        except (TypeError, ValueError):
            conf = -1.0
        if conf < min_conf:
            continue
        boxes.append((int(data["left"][i]), int(data["top"][i]),
                      int(data["width"][i]), int(data["height"][i])))
    return boxes


def _iou(a, b):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ax2, ay2 = ax + aw, ay + ah
    bx2, by2 = bx + bw, by + bh
    ix1, iy1 = max(ax, bx), max(ay, by)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0, ix2 - ix1), max(0, iy2 - iy1)
    inter = iw * ih
    if inter == 0:
        return 0.0
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


def _dilate_box(box, ratio, frame_w, frame_h):
    x, y, w, h = box
    dx, dy = int(w * ratio), int(h * ratio)
    nx, ny = max(0, x - dx), max(0, y - dy)
    nw = min(frame_w - nx, w + 2 * dx)
    nh = min(frame_h - ny, h + 2 * dy)
    return (nx, ny, nw, nh)


class _TextTrack:
    """1つの文字領域を CSRT + Kalman で追従する内部状態。"""

    _GIVE_UP_AFTER = 12

    def __init__(self, frame, box):
        self.tracker = create_tracker()
        self.tracker.init(frame, tuple(int(v) for v in box))
        self.kalman = _make_kalman(box)
        self.box = tuple(int(v) for v in box)
        self.lost = 0
        self.alive = True

    def predict_and_track(self, frame, camera_motion):
        predicted = self.kalman.predict()
        ok, box = self.tracker.update(frame)
        if ok:
            self.lost = 0
            self.box = tuple(int(v) for v in box)
            self.kalman.correct(_measurement(box))
        else:
            self.lost += 1
            dx, dy = camera_motion
            px, py, pw, ph = _box_from_state(predicted)
            self.box = (int(px + dx), int(py + dy), int(pw), int(ph))
            if self.lost > self._GIVE_UP_AFTER:
                self.alive = False

    def reinit(self, frame, box):
        """OCR 検出で正しい位置に補正し直す。"""
        self.tracker = create_tracker()
        self.tracker.init(frame, tuple(int(v) for v in box))
        self.kalman.correct(_measurement(box))
        self.box = tuple(int(v) for v in box)
        self.lost = 0
        self.alive = True


class TextMaskPipeline:
    """動画フレーム列に対し、追従つきのテキストマスク領域を供給する。"""

    def __init__(self, ocr_interval=15, min_conf=45, lang="jpn+eng",
                 dilate_ratio=0.12, scene_threshold=0.6, iou_match=0.3):
        self.ocr_interval = max(1, ocr_interval)
        self.min_conf = min_conf
        self.lang = lang
        self.dilate_ratio = dilate_ratio
        self.iou_match = iou_match
        self.scene = SceneChangeDetector(scene_threshold)
        self.tracks = []
        self.prev_gray = None
        self._ocr_ok = is_ocr_available()

    @property
    def ocr_available(self):
        return self._ocr_ok

    def _run_ocr_and_associate(self, frame):
        detections = detect_text_boxes(frame, self.min_conf, self.lang)
        used = [False] * len(self.tracks)
        for det in detections:
            best_iou, best_idx = 0.0, -1
            for i, tr in enumerate(self.tracks):
                if used[i] or not tr.alive:
                    continue
                score = _iou(det, tr.box)
                if score > best_iou:
                    best_iou, best_idx = score, i
            if best_idx >= 0 and best_iou >= self.iou_match:
                self.tracks[best_idx].reinit(frame, det)
                used[best_idx] = True
            else:
                self.tracks.append(_TextTrack(frame, det))

    def process(self, frame, frame_idx):
        """フレームを処理し、マスクすべき (x, y, w, h) のリストを返す。"""
        if not self._ocr_ok:
            return []

        h, w = frame.shape[:2]
        cur_gray = to_gray(frame)
        camera_motion = estimate_camera_motion(self.prev_gray, cur_gray)
        self.prev_gray = cur_gray
        scene_changed = self.scene.update(frame)

        if scene_changed:
            # カット時は全トラッカーを破棄して即再検出
            self.tracks = []

        # 既存トラッカーを短期追跡
        for tr in self.tracks:
            tr.predict_and_track(frame, camera_motion)
        self.tracks = [tr for tr in self.tracks if tr.alive]

        # 定期 OCR or カット直後 or まだ何も無いとき
        if scene_changed or not self.tracks or frame_idx % self.ocr_interval == 0:
            self._run_ocr_and_associate(frame)

        return [_dilate_box(tr.box, self.dilate_ratio, w, h)
                for tr in self.tracks if tr.alive]

    def detect_single(self, frame):
        """静止画用: 1回だけ OCR して膨張済みボックスを返す。"""
        if not self._ocr_ok:
            return []
        h, w = frame.shape[:2]
        boxes = detect_text_boxes(frame, self.min_conf, self.lang)
        return [_dilate_box(b, self.dilate_ratio, w, h) for b in boxes]
