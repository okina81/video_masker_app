import cv2
import numpy as np

# 何フレーム連続で見失ったら再初期化を試みるか
_REINIT_AFTER = 5
# 再初期化後も見失い続けたら何フレームでマスクを消すか
_GIVE_UP_AFTER = 30


def create_tracker():
    if hasattr(cv2, "legacy") and hasattr(cv2.legacy, "TrackerCSRT_create"):
        return cv2.legacy.TrackerCSRT_create()
    return cv2.TrackerCSRT_create()


def _make_kalman(box):
    """box = (x, y, w, h) で初期化した Kalman フィルターを返す。状態は (cx, cy, w, h, vx, vy)。"""
    kf = cv2.KalmanFilter(6, 4)
    # 状態遷移: 等速モデル
    kf.transitionMatrix = np.array([
        [1, 0, 0, 0, 1, 0],
        [0, 1, 0, 0, 0, 1],
        [0, 0, 1, 0, 0, 0],
        [0, 0, 0, 1, 0, 0],
        [0, 0, 0, 0, 1, 0],
        [0, 0, 0, 0, 0, 1],
    ], dtype=np.float32)
    # 観測行列: cx, cy, w, h だけ観測
    kf.measurementMatrix = np.array([
        [1, 0, 0, 0, 0, 0],
        [0, 1, 0, 0, 0, 0],
        [0, 0, 1, 0, 0, 0],
        [0, 0, 0, 1, 0, 0],
    ], dtype=np.float32)
    kf.processNoiseCov = np.eye(6, dtype=np.float32) * 1e-2
    kf.measurementNoiseCov = np.eye(4, dtype=np.float32) * 1e-1
    kf.errorCovPost = np.eye(6, dtype=np.float32)

    x, y, w, h = box
    cx, cy = x + w / 2, y + h / 2
    kf.statePost = np.array([[cx], [cy], [w], [h], [0], [0]], dtype=np.float32)
    return kf


def _box_from_state(state):
    cx, cy, w, h = (float(state[i].flat[0]) for i in range(4))
    return (cx - w / 2, cy - h / 2, w, h)


def _measurement(box):
    x, y, w, h = box
    return np.array([[x + w / 2], [y + h / 2], [w], [h]], dtype=np.float32)


class TrackedItem:
    """CSRT + Kalman フィルターで一つの手動範囲を追従する。"""

    def __init__(self, box, start_frame):
        self.init_box = tuple(int(v) for v in box)
        self.start = start_frame
        self.tracker = None
        self.kalman = None
        self.lost_count = 0
        self.current_box = box
        self.active = True  # False になったらマスクを描かない

    def init(self, frame):
        self.tracker = create_tracker()
        self.tracker.init(frame, self.init_box)
        self.kalman = _make_kalman(self.init_box)
        self.current_box = self.init_box
        self.lost_count = 0
        self.active = True

    def update(self, frame):
        if self.tracker is None or self.kalman is None:
            return

        # Kalman 予測
        predicted = self.kalman.predict()

        ok, box = self.tracker.update(frame)

        if ok:
            self.lost_count = 0
            self.current_box = box
            self.kalman.correct(_measurement(box))
            self.active = True
        else:
            self.lost_count += 1

            if self.lost_count <= _REINIT_AFTER:
                # Kalman 予測値でマスクを継続
                self.current_box = _box_from_state(predicted)
                self.active = True
            elif self.lost_count <= _GIVE_UP_AFTER:
                # 再初期化を試みる（Kalman 予測位置付近で再トライ）
                pred_box = _box_from_state(predicted)
                x, y, w, h = (int(v) for v in pred_box)
                new_tracker = create_tracker()
                try:
                    new_tracker.init(frame, (x, y, w, h))
                    ok2, box2 = new_tracker.update(frame)
                    if ok2:
                        self.tracker = new_tracker
                        self.current_box = box2
                        self.kalman.correct(_measurement(box2))
                        self.lost_count = 0
                        self.active = True
                        return
                except Exception:
                    pass
                # 再初期化失敗 → Kalman 予測で継続
                self.current_box = pred_box
                self.active = True
            else:
                # 諦め: マスクを消す
                self.active = False
