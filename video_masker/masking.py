import cv2
import numpy as np


def apply_mosaic(roi, factor=0.08):
    h, w = roi.shape[:2]
    if h == 0 or w == 0:
        return roi
    small = cv2.resize(
        roi,
        (max(1, int(w * factor)), max(1, int(h * factor))),
        interpolation=cv2.INTER_LINEAR,
    )
    return cv2.resize(small, (w, h), interpolation=cv2.INTER_NEAREST)


def apply_blur(roi):
    h, w = roi.shape[:2]
    if h == 0 or w == 0:
        return roi
    k = max(15, (min(h, w) // 2) | 1)
    return cv2.GaussianBlur(roi, (k, k), 0)


def apply_fill(roi):
    return np.zeros_like(roi)


def apply_fill_color(roi, color_bgr):
    result = np.empty_like(roi)
    result[:] = color_bgr
    return result


def _light_tint(color_bgr, ratio=0.18):
    """選択色を白と混ぜた淡いカラーを返す（背景用）"""
    return tuple(int(c * ratio + 255 * (1 - ratio)) for c in color_bgr)


def _gradient_bg(h, w, color_bgr):
    """上が淡く下が濃い縦グラデーション背景を numpy 配列で返す"""
    color = np.array(color_bgr, dtype=np.float32)
    white = np.array([255, 255, 255], dtype=np.float32)
    # 上端: color を 10% 混合、下端: 50% 混合
    alphas = np.linspace(0.10, 0.50, h, dtype=np.float32).reshape(h, 1, 1)
    bg = (color * alphas + white * (1 - alphas)).astype(np.uint8)
    return np.broadcast_to(bg, (h, w, 3)).copy()


def apply_hatch(roi, color_bgr, spacing=10):
    """淡いグラデーション背景に選択色の斜線を重ねる"""
    h, w = roi.shape[:2]
    result = _gradient_bg(h, w, color_bgr)
    for offset in range(-(h + w), h + w, spacing):
        x0 = max(0, offset)
        y0 = max(0, -offset)
        x1 = min(w, h + offset)
        y1 = min(h, w - offset)
        if x0 < x1 and y0 < y1:
            cv2.line(result, (x0, y0), (x1, y1), color_bgr, 2)
    return result


def apply_check(roi, color_bgr, grid=16):
    """選択色と淡いカラーで市松模様を描く"""
    result = np.empty_like(roi)
    tint = _light_tint(color_bgr)
    h, w = result.shape[:2]
    for gy in range(0, h, grid):
        for gx in range(0, w, grid):
            y2 = min(gy + grid, h)
            x2 = min(gx + grid, w)
            color = color_bgr if (gy // grid + gx // grid) % 2 == 0 else tint
            result[gy:y2, gx:x2] = color
    return result


def apply_overlay(roi, overlay_bgra, base="blur"):
    """キャラクター画像（透過PNG）を範囲に重ねる。

    透過部分から元画像（顔など）が見えないよう、まず下地を隠してから
    アスペクト比を保ったキャラ画像を中央に合成する。
    overlay_bgra が None のときは下地処理のみ行う。
    """
    h, w = roi.shape[:2]
    if h == 0 or w == 0:
        return roi

    # 下地: 透過部分の漏れ防止のため元画像をぼかす／モザイク化する
    result = (apply_mosaic(roi) if base == "mosaic" else apply_blur(roi)).copy()
    if overlay_bgra is None:
        return result

    oh, ow = overlay_bgra.shape[:2]
    if oh == 0 or ow == 0:
        return result

    # contain: アスペクト比を保って枠内に収める
    scale = min(w / ow, h / oh)
    nw, nh = max(1, int(round(ow * scale))), max(1, int(round(oh * scale)))
    resized = cv2.resize(overlay_bgra, (nw, nh), interpolation=cv2.INTER_AREA)
    ox, oy = (w - nw) // 2, (h - nh) // 2

    rgb = resized[:, :, :3].astype(np.float32)
    alpha = resized[:, :, 3:4].astype(np.float32) / 255.0
    region = result[oy:oy + nh, ox:ox + nw].astype(np.float32)
    blended = rgb * alpha + region * (1 - alpha)
    result[oy:oy + nh, ox:ox + nw] = blended.astype(np.uint8)
    return result


def make_overlay_masker(overlay_bgra, base="blur"):
    return lambda roi: apply_overlay(roi, overlay_bgra, base)


MASKERS = {"mosaic": apply_mosaic, "blur": apply_blur, "fill": apply_fill}

MANUAL_COLORS = {
    "黒": (0, 0, 0),
    "白": (255, 255, 255),
    "赤": (0, 0, 200),
    "青": (200, 0, 0),
    "緑": (0, 160, 0),
    "黄": (0, 200, 200),
}


def make_manual_masker(color_name, design_name):
    color_bgr = MANUAL_COLORS.get(color_name, (0, 0, 0))
    if design_name == "斜線":
        return lambda roi: apply_hatch(roi, color_bgr)
    elif design_name == "チェック":
        return lambda roi: apply_check(roi, color_bgr)
    else:
        return lambda roi: apply_fill_color(roi, color_bgr)


def mask_region(frame, box, masker):
    x, y, w, h = [int(v) for v in box]
    frame_h, frame_w = frame.shape[:2]
    x, y = max(0, x), max(0, y)
    w, h = min(w, frame_w - x), min(h, frame_h - y)
    if w <= 0 or h <= 0:
        return
    frame[y:y + h, x:x + w] = masker(frame[y:y + h, x:x + w])

