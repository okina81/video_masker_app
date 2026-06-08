import os
import ssl
import tempfile
import urllib.error
import urllib.request


MODEL_URL = (
    "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/"
    "face_detection_yunet_2023mar.onnx"
)
MODEL_PATH = "face_detection_yunet_2023mar.onnx"

# SCRFD モデル名（insightface model_zoo で管理）
SCRFD_MODEL_NAME = "scrfd_10g_bnkps"


class ModelPreparationError(RuntimeError):
    """顔検出モデルの準備に失敗したときのユーザー向けエラー。"""


def _ssl_context():
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def ensure_model(log_cb=None):
    if os.path.exists(MODEL_PATH):
        return
    if log_cb:
        log_cb("はじめての準備をしています（少しお待ちください）…")
    tmp_path = None
    try:
        with urllib.request.urlopen(MODEL_URL, context=_ssl_context(), timeout=60) as response:
            fd, tmp_path = tempfile.mkstemp(prefix="face_model_", suffix=".onnx")
            with os.fdopen(fd, "wb") as tmp_file:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    tmp_file.write(chunk)
        os.replace(tmp_path, MODEL_PATH)
    except (ssl.SSLError, urllib.error.URLError, OSError) as exc:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise ModelPreparationError(
            "顔検出モデルの準備に失敗しました。インターネット接続を確認して、"
            "start_mac.command からもう一度起動してください。"
        ) from exc


def create_scrfd_detector(log_cb=None):
    """SCRFD 検出器を生成して返す。insightface が未インストールの場合は None。"""
    try:
        from insightface.model_zoo import get_model
        if log_cb:
            log_cb("SCRFD モデルを準備しています（初回のみダウンロードが必要です）…")
        det = get_model(SCRFD_MODEL_NAME, download=True, download_zip=True)
        det.prepare(ctx_id=-1, input_size=(640, 640))
        return det
    except Exception as exc:
        if log_cb:
            log_cb(f"SCRFD の読み込みに失敗しました（YuNet のみで処理します）: {exc}")
        return None
