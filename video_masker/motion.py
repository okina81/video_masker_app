"""カメラの動き推定（オプティカルフロー）とシーンチェンジ検出。

手動範囲の追従補完や、テキスト追従パイプラインで利用する。
"""

import cv2
import numpy as np


def to_gray(frame):
    if frame.ndim == 2:
        return frame
    return cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)


def estimate_camera_motion(prev_gray, cur_gray, max_points=200):
    """前フレームから現フレームへのカメラ全体の平行移動量 (dx, dy) を推定する。

    疎なオプティカルフロー（Lucas-Kanade）で特徴点を追跡し、移動量の中央値を
    返す。特徴点が取れない場合は (0.0, 0.0)。
    """
    if prev_gray is None or cur_gray is None:
        return (0.0, 0.0)
    if prev_gray.shape != cur_gray.shape:
        return (0.0, 0.0)

    pts = cv2.goodFeaturesToTrack(
        prev_gray, maxCorners=max_points, qualityLevel=0.01,
        minDistance=8, blockSize=7,
    )
    if pts is None or len(pts) < 6:
        return (0.0, 0.0)

    nxt, status, _ = cv2.calcOpticalFlowPyrLK(prev_gray, cur_gray, pts, None)
    if nxt is None or status is None:
        return (0.0, 0.0)

    status = status.reshape(-1).astype(bool)
    good_old = pts.reshape(-1, 2)[status]
    good_new = nxt.reshape(-1, 2)[status]
    if len(good_old) < 6:
        return (0.0, 0.0)

    deltas = good_new - good_old
    dx = float(np.median(deltas[:, 0]))
    dy = float(np.median(deltas[:, 1]))
    return (dx, dy)


class SceneChangeDetector:
    """連続フレームの色ヒストグラム相関でカット（場面転換）を検出する。

    correlation が threshold を下回ったらシーンチェンジとみなす。
    """

    def __init__(self, threshold=0.6):
        self.threshold = threshold
        self._prev_hist = None

    @staticmethod
    def _hist(frame):
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        hist = cv2.calcHist([hsv], [0, 1], None, [50, 60], [0, 180, 0, 256])
        cv2.normalize(hist, hist, 0, 1, cv2.NORM_MINMAX)
        return hist

    def update(self, frame):
        """フレームを与え、直前との比較でシーンチェンジなら True を返す。"""
        hist = self._hist(frame)
        if self._prev_hist is None:
            self._prev_hist = hist
            return False
        corr = cv2.compareHist(self._prev_hist, hist, cv2.HISTCMP_CORREL)
        self._prev_hist = hist
        return corr < self.threshold

    def reset(self):
        self._prev_hist = None
