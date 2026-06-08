import os
import subprocess
import tempfile

import cv2

from video_masker.masking import MASKERS, make_manual_masker, mask_region
from video_masker.media import read_image, write_image
from video_masker.model import MODEL_PATH, create_scrfd_detector, ensure_model
from video_masker.tracking import TrackedItem


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
        items.append(TrackedItem((x, y, w, h), start_frame))
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


def mask_manual_regions(frame, frame_idx, manual_boxes, manual_masker_fn, track=False, track_items=None):
    if track:
        for item in track_items or []:
            if frame_idx < item.start:
                continue
            if item.tracker is None:
                item.init(frame)
                mask_region(frame, item.current_box, manual_masker_fn)
            else:
                item.update(frame)
                if item.active:
                    mask_region(frame, item.current_box, manual_masker_fn)
        return

    for box in manual_boxes:
        start_frame = box[4] if len(box) > 4 else 0
        if frame_idx < start_frame:
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
    log_cb=None,
):
    masker = MASKERS[mode]
    if manual_masker_fn is None:
        manual_masker_fn = _default_manual_masker
    if use_faces:
        if face_detector is None:
            face_detector = create_face_detector(score, log_cb)
        mask_faces(frame, face_detector, masker, face_margin, scrfd_detector)
    mask_manual_regions(frame, frame_idx, manual_boxes, manual_masker_fn, track, track_items)
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
    progress_cb=None,
    log_cb=None,
    stop_event=None,
):
    masker = MASKERS[mode]
    if manual_masker_fn is None:
        manual_masker_fn = _default_manual_masker
    track_items = build_track_items(manual_boxes) if track else []

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

            if face_detector is not None:
                mask_faces(frame, face_detector, masker, face_margin, scrfd_detector)
            mask_manual_regions(frame, frame_idx, manual_boxes, manual_masker_fn, track, track_items)

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
    progress_cb=None,
    log_cb=None,
):
    masker = MASKERS[mode]
    if manual_masker_fn is None:
        manual_masker_fn = _default_manual_masker
    image = read_image(input_path)

    if use_faces:
        face_detector = create_face_detector(score, log_cb)
        scrfd_detector = create_scrfd_detector(log_cb) if use_scrfd else None
        mask_faces(image, face_detector, masker, face_margin, scrfd_detector)

    for box in manual_boxes:
        mask_region(image, box[:4], manual_masker_fn)

    write_image(output_path, image)
    if progress_cb:
        progress_cb(1, 1)
    return 1
