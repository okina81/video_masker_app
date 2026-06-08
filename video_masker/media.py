import os

import cv2
import numpy as np


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".avi"}


def get_media_type(path):
    ext = os.path.splitext(path)[1].lower()
    if ext in IMAGE_EXTENSIONS:
        return "image"
    if ext in VIDEO_EXTENSIONS:
        return "video"
    return None


def read_image(path):
    data = np.fromfile(path, dtype=np.uint8)
    image = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if image is None:
        raise RuntimeError("画像を読み込めませんでした。")
    return image


def write_image(path, image):
    ext = os.path.splitext(path)[1].lower() or ".png"
    ok, data = cv2.imencode(ext, image)
    if not ok:
        raise RuntimeError("画像を書き出せませんでした。")
    data.tofile(path)


def load_overlay_image(path):
    """オーバーレイ用の画像をアルファ付き（BGRA）で読み込む。

    日本語パス対応のため np.fromfile + imdecode を使う。
    読み込みに失敗した場合は None を返す（呼び出し側で無効化判定）。
    """
    if not path or not os.path.exists(path):
        return None
    try:
        data = np.fromfile(path, dtype=np.uint8)
        img = cv2.imdecode(data, cv2.IMREAD_UNCHANGED)
        if img is None:
            return None
        if img.ndim == 2:  # グレースケール
            img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGRA)
        elif img.shape[2] == 3:  # 透過なし → 不透明アルファを付与
            img = cv2.cvtColor(img, cv2.COLOR_BGR2BGRA)
        return img
    except Exception:
        return None

