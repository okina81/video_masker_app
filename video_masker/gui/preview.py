import os
import tkinter as tk
from tkinter import messagebox, ttk

import cv2
from PIL import Image, ImageTk

from video_masker.media import get_media_type, read_image
from video_masker.model import ModelPreparationError
from video_masker.processing import build_track_items, mask_frame


def _fmt_time(frame_idx, fps):
    sec = int(frame_idx / fps) if fps else 0
    return f"{sec // 60}:{sec % 60:02d}"


class FinishPreview(tk.Toplevel):
    def __init__(self, parent, input_path, media_type, params, all_paths=None,
                 manual_boxes_per_file=None):
        super().__init__(parent)
        self.title("仕上がりプレビュー")
        self.input_path = input_path
        self.media_type = media_type
        self.params     = params
        self.playing    = False
        self.cap        = None
        self.frame_idx  = 0
        self.total      = 1
        self.fps        = 30
        self.face_detector = None
        self._seeking   = False  # シーク中の多重呼び出し防止
        self._preview_error = False

        # 複数ファイル対応
        self._all_paths = all_paths if all_paths and len(all_paths) > 1 else None
        self._file_idx  = (all_paths.index(input_path)
                           if self._all_paths and input_path in all_paths else 0)
        self._manual_boxes_per_file = manual_boxes_per_file or {}

        if self._all_paths and input_path in self._manual_boxes_per_file:
            self.params["manual_boxes"] = self._manual_boxes_per_file[input_path]

        self.track_items = self.make_track_items()

        # ── ファイル切り替えバー（複数ファイル時のみ）
        if self._all_paths:
            nav = tk.Frame(self, bg="#F0F4F8", padx=10, pady=6)
            nav.pack(fill="x")
            tk.Button(nav, text="◀ 前", width=6,
                      command=self._prev_file).pack(side="left")
            self._file_label = tk.Label(nav, text="", font=("Helvetica", 11),
                                        bg="#F0F4F8")
            self._file_label.pack(side="left", padx=10, expand=True)
            tk.Button(nav, text="次 ▶", width=6,
                      command=self._next_file).pack(side="right")
            self._update_nav_label()

        self.canvas = tk.Canvas(self, bg="black", highlightthickness=0)
        self.canvas.pack(padx=10, pady=(10, 4))

        # ── シークバー行
        self._seek_row = tk.Frame(self)
        self.seek_var  = tk.IntVar(value=0)
        self._seek_scale = ttk.Scale(
            self._seek_row,
            from_=0, to=1,
            variable=self.seek_var,
            orient="horizontal",
            command=self._on_seek_move,
        )
        self._seek_scale.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self._time_label = tk.Label(self._seek_row, text="0:00 / 0:00", width=14,
                                    font=("Helvetica", 11))
        self._time_label.pack(side="left")

        # ── 再生コントロール行
        controls = tk.Frame(self)
        controls.pack(fill="x", padx=10, pady=(2, 10))
        self.play_btn    = tk.Button(controls, text="▶ 再生", width=8,
                                     command=self.toggle_play)
        self.restart_btn = tk.Button(controls, text="最初から", command=self.restart)
        self.status      = tk.Label(controls, text="", font=("Helvetica", 11))
        self.status.pack(side="left", padx=(0, 8))
        tk.Button(controls, text="閉じる", command=self.close).pack(side="right")

        if self.media_type == "video":
            self.play_btn.pack(side="left")
            self.restart_btn.pack(side="left", padx=6)
            self._seek_row.pack(fill="x", padx=10, pady=(0, 4))

        self.protocol("WM_DELETE_WINDOW", self.close)
        if self.media_type == "image":
            self.show_image_preview()
        else:
            self.open_video()
            self.show_next_frame()

    # ── ファイル切り替え ──────────────────────────────────────────────────

    def _update_nav_label(self):
        if not self._all_paths:
            return
        n    = len(self._all_paths)
        name = os.path.basename(self._all_paths[self._file_idx])
        self._file_label.config(text=f"{self._file_idx + 1} / {n}  {name}")

    def _switch_file(self, idx):
        self.playing = False
        self.play_btn.config(text="▶ 再生")
        if self.cap is not None:
            self.cap.release()
            self.cap = None
        self.face_detector = None

        path       = self._all_paths[idx]
        media_type = get_media_type(path) or "image"
        self.input_path = path
        self.media_type = media_type
        self.frame_idx  = 0
        self._file_idx  = idx
        self.params["manual_boxes"] = self._manual_boxes_per_file.get(path, [])
        self.track_items = self.make_track_items()
        self._update_nav_label()

        if media_type == "video":
            self.play_btn.pack(side="left")
            self.restart_btn.pack(side="left", padx=6)
            self._seek_row.pack(fill="x", padx=10, pady=(0, 4),
                                before=self._seek_row.master.winfo_children()[-1])
        else:
            self.play_btn.pack_forget()
            self.restart_btn.pack_forget()
            self._seek_row.pack_forget()

        if media_type == "image":
            self.show_image_preview()
        else:
            self.open_video()
            self.show_next_frame()

    def _prev_file(self):
        if self._all_paths:
            self._switch_file((self._file_idx - 1) % len(self._all_paths))

    def _next_file(self):
        if self._all_paths:
            self._switch_file((self._file_idx + 1) % len(self._all_paths))

    # ── コア ─────────────────────────────────────────────────────────────

    def make_track_items(self):
        if self.media_type != "video" or not self.params.get("track"):
            return []
        return build_track_items(self.params["manual_boxes"])

    def display_frame(self, frame):
        rgb    = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        image  = Image.fromarray(rgb)
        max_w  = self.winfo_screenwidth()  - 120
        max_h  = self.winfo_screenheight() - 260
        scale  = min(1.0, max_w / image.width, max_h / image.height)
        disp_w = max(1, int(image.width  * scale))
        disp_h = max(1, int(image.height * scale))
        image  = image.resize((disp_w, disp_h))
        self.tk_img = ImageTk.PhotoImage(image)
        self.canvas.configure(width=disp_w, height=disp_h)
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, anchor="nw", image=self.tk_img)

    def apply_preview_mask(self, frame):
        try:
            self.face_detector = mask_frame(
                frame,
                self.frame_idx,
                self.params["mode"],
                self.params["use_faces"],
                self.params["manual_boxes"],
                self.params["score"],
                manual_masker_fn=self.params.get("manual_masker_fn", lambda roi: roi * 0),
                track=self.params.get("track", False),
                track_items=self.track_items,
                face_detector=self.face_detector,
                face_margin=self.params.get("face_margin", 0.0),
                scrfd_detector=self.params.get("scrfd_detector"),
            )
            return True
        except ModelPreparationError as exc:
            self._show_preview_error(str(exc))
        except Exception as exc:
            self._show_preview_error(f"プレビューの作成に失敗しました: {exc}")
        return False

    def _show_preview_error(self, message):
        self.playing = False
        self._preview_error = True
        if hasattr(self, "play_btn"):
            self.play_btn.config(text="▶ 再生", state="disabled")
        if hasattr(self, "restart_btn"):
            self.restart_btn.config(state="disabled")
        self.status.config(text="プレビューを表示できませんでした")
        messagebox.showerror("プレビューエラー", message, parent=self)

    def _update_seek_ui(self):
        self.seek_var.set(self.frame_idx)
        cur  = _fmt_time(self.frame_idx, self.fps)
        tot  = _fmt_time(self.total,     self.fps)
        self._time_label.config(text=f"{cur} / {tot}")

    def show_image_preview(self):
        frame = read_image(self.input_path)
        if not self.apply_preview_mask(frame):
            return
        self.display_frame(frame)
        self.status.config(text="画像プレビュー")

    def open_video(self):
        self.cap = cv2.VideoCapture(self.input_path)
        self.cap.set(cv2.CAP_PROP_ORIENTATION_AUTO, 1)
        self.total     = int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        self.fps       = self.cap.get(cv2.CAP_PROP_FPS) or 30
        self.frame_idx = 0
        self._seek_scale.configure(to=max(1, self.total - 1))
        self._update_seek_ui()

    def show_next_frame(self):
        if self.cap is None:
            return
        ok, frame = self.cap.read()
        if not ok:
            self.playing = False
            self.play_btn.config(text="▶ 再生")
            self.status.config(text="最後まで再生しました")
            return

        if not self.apply_preview_mask(frame):
            return
        self.display_frame(frame)
        self.frame_idx += 1
        self._update_seek_ui()

        if self.playing:
            self.after(max(1, int(1000 / self.fps)), self.show_next_frame)

    def _on_seek_move(self, _=None):
        """シークバーを動かしたときに呼ばれる。"""
        if self._seeking:
            return
        self._seeking = True
        try:
            target = int(self.seek_var.get())
            was_playing = self.playing
            self.playing = False

            if self.cap is not None:
                self.cap.set(cv2.CAP_PROP_POS_FRAMES, target)
                ok, frame = self.cap.read()
                if ok:
                    # シーク後は追跡状態をリセット
                    self.face_detector = None
                    self.track_items   = self.make_track_items()
                    self.frame_idx     = target
                    if not self.apply_preview_mask(frame):
                        return
                    self.display_frame(frame)
                    self._update_seek_ui()

            if was_playing:
                self.playing = True
                self.play_btn.config(text="⏸ 一時停止")
                self.show_next_frame()
        finally:
            self._seeking = False

    def toggle_play(self):
        self.playing = not self.playing
        self.play_btn.config(text="⏸ 一時停止" if self.playing else "▶ 再生")
        if self.playing:
            self.show_next_frame()

    def restart(self):
        self.playing = False
        self.play_btn.config(text="▶ 再生")
        if self.cap is not None:
            self.cap.release()
        self.face_detector = None
        self.track_items   = self.make_track_items()
        self.open_video()
        self.show_next_frame()

    def close(self):
        self.playing = False
        if self.cap is not None:
            self.cap.release()
        self.destroy()
