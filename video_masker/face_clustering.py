"""顔の収集・同一人物クラスタリング・個別マスク選択のための処理。

- insightface(ArcFace) で顔の埋め込みベクトルを取り出す
- scipy の階層クラスタリングで同一人物をまとめる
- ユーザーが人物ごとにマスクの ON/OFF を選べるようにする
- 書き出し時は、各顔をクラスタ重心と照合し ON の人物だけを隠す

insightface が無い環境では embeddings_available() が False を返し、
顔一覧（人物選択）機能は自動的に無効化される。
"""

import cv2
import numpy as np

from video_masker.media import get_media_type, read_image


def embeddings_available():
    """insightface の FaceAnalysis が import できるかどうか。"""
    try:
        from insightface.app import FaceAnalysis  # noqa: F401
        return True
    except Exception:
        return False


def _normalize(vectors):
    arr = np.asarray(vectors, dtype=np.float32)
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    return arr / (norms + 1e-8)


def cluster_embeddings(embeddings, distance_threshold=0.6):
    """埋め込みベクトル列を同一人物ごとにまとめ、各要素のクラスタ番号(0始まり)を返す。

    コサイン距離の平均連結（average linkage）でクラスタリングする。
    """
    embs = np.asarray(embeddings, dtype=np.float32)
    n = len(embs)
    if n == 0:
        return []
    if n == 1:
        return [0]

    from scipy.cluster.hierarchy import fcluster, linkage
    from scipy.spatial.distance import pdist

    normed = _normalize(embs)
    dists = pdist(normed, metric="cosine")
    z = linkage(dists, method="average")
    labels = fcluster(z, t=distance_threshold, criterion="distance")
    return [int(label) - 1 for label in labels]


def cosine_distance(a, b):
    a = np.asarray(a, dtype=np.float32)
    b = np.asarray(b, dtype=np.float32)
    denom = (np.linalg.norm(a) * np.linalg.norm(b)) + 1e-8
    return 1.0 - float(np.dot(a, b) / denom)


def nearest_cluster(embedding, centroids):
    """埋め込みに最も近いクラスタ重心の (index, distance) を返す。"""
    if centroids is None or len(centroids) == 0:
        return (-1, float("inf"))
    best_idx, best_dist = -1, float("inf")
    for i, c in enumerate(centroids):
        d = cosine_distance(embedding, c)
        if d < best_dist:
            best_idx, best_dist = i, d
    return (best_idx, best_dist)


class FaceAnalyzer:
    """insightface をラップして検出＋埋め込みを返す。生成失敗時は usable=False。"""

    def __init__(self, log_cb=None):
        self.app = None
        self.usable = False
        try:
            from insightface.app import FaceAnalysis
            if log_cb:
                log_cb("顔認識モデルを準備しています（初回のみダウンロードが必要です）…")
            self.app = FaceAnalysis(name="buffalo_l",
                                    allowed_modules=["detection", "recognition"])
            # det_thresh を下げて検出漏れを減らす（取りこぼし対策）
            try:
                self.app.prepare(ctx_id=-1, det_thresh=0.4, det_size=(640, 640))
            except TypeError:
                # 古い insightface は prepare に det_thresh が無い
                self.app.prepare(ctx_id=-1, det_size=(640, 640))
            self.usable = True
        except Exception as exc:
            if log_cb:
                log_cb(f"顔認識モデルを読み込めませんでした（人物選択は使えません）: {exc}")

    def analyze(self, frame):
        """frame から [{bbox:(x,y,w,h), embedding, det_score}] を返す。"""
        if not self.usable:
            return []
        results = []
        for f in self.app.get(frame):
            x1, y1, x2, y2 = (int(v) for v in f.bbox)
            emb = getattr(f, "normed_embedding", None)
            if emb is None:
                emb = getattr(f, "embedding", None)
            if emb is None:
                continue
            results.append({
                "bbox": (x1, y1, x2 - x1, y2 - y1),
                "embedding": np.asarray(emb, dtype=np.float32),
                "det_score": float(getattr(f, "det_score", 1.0)),
            })
        return results


def _crop(frame, bbox, pad=0.2):
    h, w = frame.shape[:2]
    x, y, bw, bh = bbox
    px, py = int(bw * pad), int(bh * pad)
    x0, y0 = max(0, x - px), max(0, y - py)
    x1, y1 = min(w, x + bw + px), min(h, y + bh + py)
    if x1 <= x0 or y1 <= y0:
        return None
    return frame[y0:y1, x0:x1].copy()


def collect_faces(media_path, analyzer, sample_step=8, max_samples=600,
                  progress_cb=None, stop_event=None):
    """動画/画像から顔を収集して [{embedding, thumbnail(BGR), det_score}] を返す。"""
    if not analyzer.usable:
        return []

    media_type = get_media_type(media_path)
    records = []

    def handle(frame):
        for face in analyzer.analyze(frame):
            thumb = _crop(frame, face["bbox"])
            if thumb is None or thumb.size == 0:
                continue
            records.append({
                "embedding": face["embedding"],
                "thumbnail": thumb,
                "det_score": face["det_score"],
            })

    if media_type == "image":
        handle(read_image(media_path))
        if progress_cb:
            progress_cb(1, 1)
        return records

    cap = cv2.VideoCapture(media_path)
    cap.set(cv2.CAP_PROP_ORIENTATION_AUTO, 1)
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    step = max(1, sample_step)
    # サンプル数が多すぎる場合は間引く
    if total and total // step > max_samples:
        step = max(step, total // max_samples)

    idx = 0
    try:
        while True:
            if stop_event is not None and stop_event.is_set():
                break
            ok, frame = cap.read()
            if not ok:
                break
            if idx % step == 0:
                handle(frame)
                if progress_cb:
                    progress_cb(idx, total)
            idx += 1
    finally:
        cap.release()
    return records


def build_person_clusters(records, distance_threshold=0.7):
    """収集した顔レコードを人物ごとにまとめる。

    返り値: [{centroid, embeddings(正規化済みK×D), thumbnail(代表), count}] の
    リスト（多く写る人物順）。embeddings は書き出し時の最近傍マッチに使う。
    """
    if not records:
        return []
    labels = cluster_embeddings([r["embedding"] for r in records],
                                distance_threshold)
    groups = {}
    for label, rec in zip(labels, records):
        groups.setdefault(label, []).append(rec)

    clusters = []
    for label, recs in groups.items():
        embs = np.stack([r["embedding"] for r in recs]).astype(np.float32)
        normed = _normalize(embs)
        centroid = normed.mean(axis=0)
        # 代表サムネイルは検出スコアが最も高いもの
        best = max(recs, key=lambda r: r["det_score"])
        clusters.append({
            "centroid": centroid.astype(np.float32),
            "embeddings": normed,        # 正規化済みメンバー埋め込み（K×D）
            "thumbnail": best["thumbnail"],
            "count": len(recs),
        })
    clusters.sort(key=lambda c: c["count"], reverse=True)
    return clusters


def assign_to_clusters(embedding, cluster_embeddings_list):
    """顔の埋め込みを各クラスタのメンバー集合と比較し、最小コサイン距離の
    クラスタ (index, distance) を返す（k-NN/最近傍方式）。

    cluster_embeddings_list: 各要素が正規化済みの (K, D) 配列。
    重心一点ではなくメンバー全体との最小距離で判定するため、角度・表情の
    変化に強い。
    """
    e = np.asarray(embedding, dtype=np.float32)
    e = e / (np.linalg.norm(e) + 1e-8)
    best_idx, best_dist = -1, float("inf")
    for i, embs in enumerate(cluster_embeddings_list):
        if embs is None or len(embs) == 0:
            continue
        sims = np.asarray(embs, dtype=np.float32) @ e  # (K,)
        dist = 1.0 - float(sims.max())                 # 最も近いメンバーまでの距離
        if dist < best_dist:
            best_idx, best_dist = i, dist
    return best_idx, best_dist
