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

