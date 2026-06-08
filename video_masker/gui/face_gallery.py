"""検出した顔を人物ごとに一覧表示し、個別にマスク ON/OFF を選ぶダイアログ。"""

import threading
import tkinter as tk
from tkinter import messagebox

import cv2
from PIL import Image, ImageTk

from video_masker.face_clustering import (
    FaceAnalyzer,
    build_person_clusters,
    collect_faces,
    embeddings_available,
)

_THUMB = 96
_MASK_ON = "#1A6B63"
_MASK_OFF = "#9CA3AF"


class FaceGalleryDialog(tk.Toplevel):
    """顔一覧ダイアログ。result は [{centroid, enabled}] か None（キャンセル）。"""

    def __init__(self, parent, media_path, score=0.5):
        super().__init__(parent)
        self.title("人物ごとにかくす／かくさないを選ぶ")
        self.configure(bg="#EDF1F7")
        self.media_path = media_path
        self.score = score
        self.result = None
        self._clusters = []
        self._enabled_vars = []
        self._thumb_imgs = []  # ImageTk 参照保持
        self._stop = threading.Event()

        self.status = tk.Label(self, text="顔をさがしています…",
                               font=("Helvetica", 13), bg="#EDF1F7")
        self.status.pack(padx=20, pady=20)

        self.body = tk.Frame(self, bg="#EDF1F7")
        self.body.pack(fill="both", expand=True, padx=16, pady=(0, 8))

        self.btn_row = tk.Frame(self, bg="#EDF1F7")
        self.btn_row.pack(fill="x", padx=16, pady=(0, 14))

        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.after(50, self._start_collection)

    # ── 収集（バックグラウンド） ──────────────────────────────────────────

    def _start_collection(self):
        if not embeddings_available():
            self.status.config(
                text="顔認識ライブラリ(insightface)が見つかりません。\n"
                     "この機能を使うには insightface のインストールが必要です。")
            tk.Button(self.btn_row, text="閉じる", command=self._cancel).pack(side="right")
            return
        threading.Thread(target=self._collect_worker, daemon=True).start()

    def _collect_worker(self):
        try:
            analyzer = FaceAnalyzer(log_cb=lambda m: self.after(
                0, self.status.config, {"text": m}))
            if not analyzer.usable:
                self.after(0, self._on_failed,
                           "顔認識モデルを準備できませんでした。")
                return
            records = collect_faces(
                self.media_path, analyzer,
                progress_cb=lambda cur, tot: self.after(
                    0, self._on_progress, cur, tot),
                stop_event=self._stop,
            )
            clusters = build_person_clusters(records)
            if not self._stop.is_set():
                self.after(0, self._on_collected, clusters)
        except Exception as exc:
            self.after(0, self._on_failed, f"顔の収集に失敗しました: {exc}")

    def _on_progress(self, cur, total):
        if total:
            self.status.config(text=f"顔をさがしています…  {int(cur / total * 100)}%")

    def _on_failed(self, message):
        self.status.config(text=message)
        for w in self.btn_row.winfo_children():
            w.destroy()
        tk.Button(self.btn_row, text="閉じる", command=self._cancel).pack(side="right")

    # ── 一覧表示 ──────────────────────────────────────────────────────────

    def _on_collected(self, clusters):
        self._clusters = clusters
        if not clusters:
            self._on_failed("顔が見つかりませんでした。")
            return

        self.status.config(
            text=f"{len(clusters)}人を検出しました。隠したくない人を「かくさない」にしてください。")

        grid = tk.Frame(self.body, bg="#EDF1F7")
        grid.pack()
        cols = min(5, len(clusters))
        for i, cluster in enumerate(clusters):
            cell = tk.Frame(grid, bg="#FFFFFF", highlightbackground="#DDE3EC",
                            highlightthickness=1, padx=8, pady=8)
            cell.grid(row=i // cols, column=i % cols, padx=6, pady=6)

            img = self._make_thumb(cluster["thumbnail"])
            self._thumb_imgs.append(img)
            tk.Label(cell, image=img, bg="#FFFFFF").pack()
            tk.Label(cell, text=f"{cluster['count']}回うつっています",
                     font=("Helvetica", 9), bg="#FFFFFF", fg="#6B7280").pack()

            var = tk.BooleanVar(value=True)  # 既定: 隠す
            self._enabled_vars.append(var)
            btn = tk.Label(cell, font=("Helvetica", 11, "bold"),
                           padx=10, pady=4, cursor="hand2")
            btn.pack(pady=(6, 0))
            self._bind_toggle(btn, var)
            self._refresh_toggle(btn, var)

        for w in self.btn_row.winfo_children():
            w.destroy()
        tk.Button(self.btn_row, text="この設定で決定", command=self._confirm,
                  font=("Helvetica", 12, "bold")).pack(side="right")
        tk.Button(self.btn_row, text="キャンセル",
                  command=self._cancel).pack(side="right", padx=6)

    def _make_thumb(self, bgr):
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        img = Image.fromarray(rgb)
        scale = _THUMB / max(img.width, img.height)
        img = img.resize((max(1, int(img.width * scale)),
                          max(1, int(img.height * scale))))
        return ImageTk.PhotoImage(img)

    def _bind_toggle(self, btn, var):
        def toggle(_event=None):
            var.set(not var.get())
            self._refresh_toggle(btn, var)
        btn.bind("<ButtonRelease-1>", toggle)

    def _refresh_toggle(self, btn, var):
        if var.get():
            btn.config(text="かくす 👀", bg=_MASK_ON, fg="white")
        else:
            btn.config(text="かくさない 🙈", bg=_MASK_OFF, fg="white")

    # ── 結果 ──────────────────────────────────────────────────────────────

    def _confirm(self):
        self.result = [
            {"centroid": cluster["centroid"], "enabled": var.get()}
            for cluster, var in zip(self._clusters, self._enabled_vars)
        ]
        self._stop.set()
        self.destroy()

    def _cancel(self):
        self.result = None
        self._stop.set()
        self.destroy()
