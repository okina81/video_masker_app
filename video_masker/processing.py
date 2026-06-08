import os
import subprocess
import tempfile

import cv2

from video_masker.masking import MASKERS, make_manual_masker, make_overlay_masker, mask_region
from video_masker.media import load_overlay_image, read_image, write_image
from video_masker.model import MODEL_PATH, create_scrfd_detector, ensure_model
from video_masker.motion import estimate_camera_motion, to_gray
from video_masker.text_masking import TextMaskPipeline
from video_masker.tracking import TrackedItem


def _text_masker_for(mode):
    """テキスト用 masker。キャラオーバーレイ時は塗りつぶしにフォールバック。"""
    return MASKERS.get(mode, MASKERS["fill"])


def build_face_masker(mode, overlay_path=None, overlay_base="blur"):
    """顔マスク用の masker 関数を返す。

    mode="overlay" のときはキャラクター画像オーバーレイ、それ以外は
    MASKERS（モザイク／ぼかし／黒塗り）を返す。画像読み込みは1回だけ。
    """
    if mode == "overlay":
        return make_overlay_masker(load_overlay_image(overlay_path), overlay_base)
    return MASKERS[mode]


def get_ffmpeg_exe():
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        return "ffmpeg"


def build_track_items(manual_boxes):
    items = []
    for box in manual_boxes:
        x, y, w, h = box[:4]
        start_frame = box[4] if len(box) > 4 else 0
        end_frame = box[5] if len(box) > 5 else None
        items.append(TrackedItem((x, y, w, h), start_frame, end_frame))
    return items


def mask_faces(frame, face_detector, masker, margin=0.0, scrfd_detector=None):
    h_, w_ = frame.shape[:2]
    face_detector.setInputSize((w_, h_))
    _, faces = face_detector.detect(frame)

    # YuNet が検出できなかったフレームで SCRFD にフォールバック
    boxes_xywh = []
    if faces is not None and len(faces) > 0:
        for face in faces:
            x, y, face_w, face_h = face[:4].astype(int)
            boxes_xywh.append((x, y, face_w, face_h))
    elif scrfd_detector is not None:
        try:
            bboxes, _ = scrfd_detector.detect(frame, input_size=(640, 640))
            if bboxes is not None:
                for b in bboxes:
                    x1, y1, x2, y2 = int(b[0]), int(b[1]), int(b[2]), int(b[3])
                    boxes_xywh.append((x1, y1, x2 - x1, y2 - y1))
        except Exception:
            pass

    for (x, y, face_w, face_h) in boxes_xywh:
        # YuNet は額が切れやすいので上側を多めに確保する
        pad_side   = int(face_w * (0.15 + margin))
        pad_top    = int(face_h * (0.35 + margin))
        pad_bottom = int(face_h * (0.10 + margin))
        box_x = x - pad_side
        box_y = y - pad_top
        box_w = face_w + 2 * pad_side
        box_h = face_h + pad_top + pad_bottom
        mask_region(frame, (box_x, box_y, box_w, box_h), masker)


def create_face_detector(score, log_cb=None):
    ensure_model(log_cb)
    return cv2.FaceDetectorYN.create(
        MODEL_PATH,
        "",
        (320, 320),
        score_threshold=score,
        nms_threshold=0.3,
        top_k=5000,
    )


def _default_manual_masker(roi):
    import numpy as np
    return np.zeros_like(roi)


def mask_manual_regions(frame, frame_idx, manual_boxes, manual_masker_fn, track=False,
                        track_items=None, camera_motion=(0.0, 0.0)):
    if track:
        for item in track_items or []:
            if frame_idx < item.start:
                continue
            if item.end is not None and frame_idx >= item.end:
                continue
            if item.tracker is None:
                item.init(frame)
                mask_region(frame, item.current_box, manual_masker_fn)
            else:
                item.update(frame, camera_motion)
                if item.active:
                    mask_region(frame, item.current_box, manual_masker_fn)
        return

    for box in manual_boxes:
        start_frame = box[4] if len(box) > 4 else 0
        end_frame = box[5] if len(box) > 5 else None
        if frame_idx < start_frame:
            continue
        if end_frame is not None and frame_idx >= end_frame:
            continue
        mask_region(frame, box[:4], manual_masker_fn)


def mask_frame(
    frame,
    frame_idx,
    mode,
    use_faces,
    manual_boxes,
    score,
    manual_masker_fn=None,
    track=False,
    track_items=None,
    face_detector=None,
    face_margin=0.0,
    scrfd_detector=None,
    face_masker_fn=None,
    text_pipeline=None,
    log_cb=None,
):
    masker = face_masker_fn if face_masker_fn is not None else MASKERS[mode]
    if manual_masker_fn is None:
        manual_masker_fn = _default_manual_masker
    if use_faces:
        if face_detector is None:
            face_detector = create_face_detector(score, log_cb)
        mask_faces(frame, face_detector, masker, face_margin, scrfd_detector)
    mask_manual_regions(frame, frame_idx, manual_boxes, manual_masker_fn, track, track_items)
    if text_pipeline is not None:
        text_masker = _text_masker_for(mode)
        for tbox in text_pipeline.process(frame, frame_idx):
            mask_region(frame, tbox, text_masker)
    return face_detector


def process_video(
    input_path,
    output_path,
    mode,
    use_faces,
    manual_boxes,
    score,
    manual_masker_fn=None,
    track=False,
    face_margin=0.0,
    use_scrfd=False,
    overlay_path=None,
    mask_text=False,
    ocr_interval=15,
    ocr_min_conf=45,
    ocr_lang="jpn+eng",
    progress_cb=None,
    log_cb=None,
    stop_event=None,
):
    masker = build_face_masker(mode, overlay_path)
    if manual_masker_fn is None:
        manual_masker_fn = _default_manual_masker
    track_items = build_track_items(manual_boxes) if track else []

    text_pipeline = None
    text_masker = None
    if mask_text:
        text_pipeline = TextMaskPipeline(ocr_interval, ocr_min_conf, ocr_lang)
        text_masker = _text_masker_for(mode)
        if not text_pipeline.ocr_available and log_cb:
            log_cb("文字検出（Tesseract）が見つからないため、テキストのかくしは行いません。")

    cap = cv2.VideoCapture(input_path)
    cap.set(cv2.CAP_PROP_ORIENTATION_AUTO, 1)
    if not cap.isOpened():
        raise RuntimeError("この動画を開けませんでした。別の動画でためしてください。")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)

    ret, first = cap.read()
    if not ret:
        raise RuntimeError("動画を読み込めませんでした。")
    height, width = first.shape[:2]
    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)

    tmp_video = tempfile.mktemp(suffix=".mp4")
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(tmp_video, fourcc, fps, (width, height))

    face_detector = None
    scrfd_detector = None
    if use_faces:
        ensure_model(log_cb)
        face_detector = cv2.FaceDetectorYN.create(
            MODEL_PATH,
            "",
            (320, 320),
            score_threshold=score,
            nms_threshold=0.3,
            top_k=5000,
        )
        if use_scrfd:
            scrfd_detector = create_scrfd_detector(log_cb)

    # 追従時のみカメラ動き補正のため前フレームのグレースケールを保持
    prev_gray = None

    frame_idx = 0
    cancelled = False
    try:
        while True:
            if stop_event and stop_event.is_set():
                cancelled = True
                break

            ret, frame = cap.read()
            if not ret:
                break

            camera_motion = (0.0, 0.0)
            if track:
                cur_gray = to_gray(frame)
                camera_motion = estimate_camera_motion(prev_gray, cur_gray)
                prev_gray = cur_gray

            if face_detector is not None:
                mask_faces(frame, face_detector, masker, face_margin, scrfd_detector)
            mask_manual_regions(frame, frame_idx, manual_boxes, manual_masker_fn,
                                track, track_items, camera_motion)
            if text_pipeline is not None:
                for tbox in text_pipeline.process(frame, frame_idx):
                    mask_region(frame, tbox, text_masker)

            writer.write(frame)
            frame_idx += 1
            if progress_cb and frame_idx % 5 == 0:
                progress_cb(frame_idx, total)
    finally:
        cap.release()
        writer.release()

    if cancelled:
        if os.path.exists(tmp_video):
            os.remove(tmp_video)
        return frame_idx

    if log_cb:
        log_cb("もうすぐ完成です（音声をあわせています）…")

    cmd = [
        get_ffmpeg_exe(),
        "-y",
        "-i",
        tmp_video,
        "-i",
        input_path,
        "-c:v",
        "copy",
        "-map",
        "0:v:0",
        "-map",
        "1:a:0?",
        "-shortest",
        output_path,
    ]
    try:
        subprocess.run(cmd, check=True)
    finally:
        if os.path.exists(tmp_video):
            os.remove(tmp_video)

    return frame_idx


def process_image(
    input_path,
    output_path,
    mode,
    use_faces,
    manual_boxes,
    score,
    manual_masker_fn=None,
    face_margin=0.0,
    use_scrfd=False,
    overlay_path=None,
    mask_text=False,
    ocr_min_conf=45,
    ocr_lang="jpn+eng",
    progress_cb=None,
    log_cb=None,
):
    masker = build_face_masker(mode, overlay_path)
    if manual_masker_fn is None:
        manual_masker_fn = _default_manual_masker
    image = read_image(input_path)

    if use_faces:
        face_detector = create_face_detector(score, log_cb)
        scrfd_detector = create_scrfd_detector(log_cb) if use_scrfd else None
        mask_faces(image, face_detector, masker, face_margin, scrfd_detector)

    for box in manual_boxes:
        mask_region(image, box[:4], manual_masker_fn)

    if mask_text:
        pipeline = TextMaskPipeline(min_conf=ocr_min_conf, lang=ocr_lang)
        if pipeline.ocr_available:
            text_masker = _text_masker_for(mode)
            for tbox in pipeline.detect_single(image):
                mask_region(image, tbox, text_masker)
        elif log_cb:
            log_cb("文字検出（Tesseract）が見つからないため、テキストのかくしは行いません。")

    write_image(output_path, image)
    if progress_cb:
        progress_cb(1, 1)
    return 1
