import os
import tkinter as tk
from tkinter import messagebox, ttk

import cv2
from PIL import Image, ImageTk

from video_masker.media import read_image

_CORNER_HIT = 8   # px — 角判定の許容距離


def _frames_to_time(frame_idx, fps):
    total_sec = int(frame_idx / fps) if fps else 0
    m = total_sec // 60
    s = total_sec % 60
    return "{:d}:{:02d}".format(m, s)


def _bgr_to_hex(color_bgr):
    b, g, r = color_bgr
    return "#{:02X}{:02X}{:02X}".format(r, g, b)


class _ActionButton(tk.Label):
    """macOS でも背景色と文字色が消えないボタン風ラベル。"""

    def __init__(self, parent, text, command, bg, fg, active_bg, **kwargs):
        super().__init__(
            parent,
            text=text,
            bg=bg,
            fg=fg,
            cursor="hand2",
            padx=16,
            pady=8,
            **kwargs,
        )
        self._bg = bg
        self._active_bg = active_bg
        self._command = command
        self.bind("<Enter>", lambda _event: self.config(bg=self._active_bg))
        self.bind("<Leave>", lambda _event: self.config(bg=self._bg))
        self.bind("<ButtonRelease-1>", self._on_release)

    def _on_release(self, _event):
        self.config(bg=self._bg)
        self._command()


class RoiSelector(tk.Toplevel):
    def __init__(self, parent, media_path, boxes=None, media_type="video",
                 color_bgr=(0, 0, 0), design="ベタ塗り", all_paths=None,
                 all_boxes=None):
        super().__init__(parent)
        self.title("塗る範囲をえらぶ")
        self.color_bgr = color_bgr
        self.design    = design
        self.result    = None
        self.playing   = False

        # 複数ファイル対応
        self._all_paths   = all_paths if all_paths and len(all_paths) > 1 else None
        self._file_idx    = (all_paths.index(media_path)
                             if self._all_paths and media_path in all_paths else 0)

        # ファイルごとの boxes を管理する辞書
        if self._all_paths:
            self._file_boxes = {}
            for p in self._all_paths:
                self._file_boxes[p] = list((all_boxes or {}).get(p, []))

        self.cap          = None
        self.image_frame  = None
        frame = self._open_file(media_path, media_type)
        if frame is None:
            return

        max_w = self.winfo_screenwidth() - 80
        # ナビバー・ヒント2行・ズーム行・シーク行・ボタン行・タイトルバー分を確保
        max_h = self.winfo_screenheight() - 340
        self.base_scale = min(1.0, max_w / self.orig_w, max_h / self.orig_h)
        self.zoom = 1.0
        self.scale = self.base_scale
        self.view_w = int(self.orig_w * self.base_scale)
        self.view_h = int(self.orig_h * self.base_scale)
        self.update_display_size()

        # 複数ファイル時は _file_boxes から現在ファイルの boxes をロード
        if self._all_paths:
            self.boxes = list(self._file_boxes.get(media_path, []))
        else:
            self.boxes = list(boxes) if boxes else []
        self.cur_frame  = 0
        self.start_xy   = None
        self.temp_rect  = None

        # 移動・リサイズ用の状態
        self._edit_mode     = "draw"
        self._edit_idx      = -1
        self._resize_corner = None
        self._move_offset   = (0, 0)

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
            self._update_file_label()

        hint_line1 = ("ドラッグ: 範囲を追加　右クリック: 範囲を削除　"
                      "ひとつ戻す / すべて消す で取り消し　"
                      "ボックスをドラッグ: 移動　角をドラッグ: リサイズ")
        tk.Label(self, text=hint_line1, font=("Helvetica", 11)).pack(pady=(10, 0), padx=10)
        if self.media_type == "video":
            hint_line2 = "シークバーで目的のフレームに移動してから描くと、そのフレーム以降だけ適用されます"
            tk.Label(self, text=hint_line2, font=("Helvetica", 11), fg="#555555").pack(pady=(2, 4), padx=10)
        else:
            tk.Label(self, text="").pack(pady=(2, 4))

        preview = tk.Frame(self)
        preview.pack(padx=10)
        self.canvas = tk.Canvas(
            preview,
            width=self.view_w,
            height=self.view_h,
            bg="black",
            cursor="cross",
            highlightthickness=0,
        )
        self.h_scroll = ttk.Scrollbar(preview, orient="horizontal", command=self.canvas.xview)
        self.v_scroll = ttk.Scrollbar(preview, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(xscrollcommand=self.h_scroll.set, yscrollcommand=self.v_scroll.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.v_scroll.grid(row=0, column=1, sticky="ns")
        self.h_scroll.grid(row=1, column=0, sticky="ew")
        self.canvas.bind("<ButtonPress-1>", self.on_press)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
        self.canvas.bind("<Button-2>", self.on_right_click)
        self.canvas.bind("<Button-3>", self.on_right_click)
        self.canvas.bind("<MouseWheel>", self.on_mouse_wheel)
        self.canvas.bind("<Button-4>", self.on_mouse_wheel)
        self.canvas.bind("<Button-5>", self.on_mouse_wheel)
        self.canvas.bind("<Motion>", self.on_motion)

        zoom_row = tk.Frame(self)
        zoom_row.pack(fill="x", padx=10, pady=(8, 0))
        tk.Button(zoom_row, text="-", width=4, command=lambda: self.change_zoom(0.8)).pack(side="left")
        self.zoom_label = tk.Label(zoom_row, text="100%", width=8)
        self.zoom_label.pack(side="left", padx=4)
        tk.Button(zoom_row, text="+", width=4, command=lambda: self.change_zoom(1.25)).pack(side="left")
        tk.Button(zoom_row, text="全体表示", command=self.reset_zoom).pack(side="left", padx=8)

        # シークバー行（フレームごと管理して hide/show をシンプルに）
        self._seek_row = tk.Frame(self)
        self.play_btn = tk.Button(self._seek_row, text="▶ 再生", width=8, command=self.toggle_play)
        self.play_btn.pack(side="left")
        self.seek_var = tk.IntVar(value=0)
        self.seek = ttk.Scale(
            self._seek_row,
            from_=0,
            to=max(0, self.total - 1),
            variable=self.seek_var,
            orient="horizontal",
            command=self.on_seek,
        )
        self.seek.pack(side="left", fill="x", expand=True, padx=8)
        total_time = _frames_to_time(self.total, self.fps)
        self.time_label = tk.Label(self._seek_row, text="0:00 / " + total_time, width=14)
        self.time_label.pack(side="left")
        if self.media_type == "video":
            self._seek_row.pack(fill="x", padx=10, pady=8)

        self._btns_row = tk.Frame(self)
        btns = self._btns_row
        btns.pack(fill="x", padx=10, pady=(0, 12))
        tk.Button(btns, text="ひとつ戻す", command=self.undo).pack(side="left")
        tk.Button(btns, text="すべて消す", command=self.clear).pack(side="left", padx=6)
        _ActionButton(
            btns,
            text="決定 ✓",
            font=("Helvetica", 13, "bold"),
            bg="#009E73",
            fg="white",
            active_bg="#007F5F",
            command=self.confirm,
        ).pack(side="right")
        tk.Button(btns, text="キャンセル", command=self.cancel).pack(side="right", padx=6)

        self.protocol("WM_DELETE_WINDOW", self.cancel)
        self.show_frame(0)

    # ── ファイル操作 ──────────────────────────────────────────────────────

    def _open_file(self, media_path, media_type):
        """ファイルを開き、先頭フレームを返す。失敗時は None を返しウィンドウを閉じる。"""
        from video_masker.media import get_media_type as _gmt
        self.media_path = media_path
        self.media_type = media_type or _gmt(media_path) or "image"

        if self.cap is not None:
            self.cap.release()
            self.cap = None
        self.image_frame = None

        if self.media_type == "image":
            try:
                frame = read_image(media_path)
            except RuntimeError as exc:
                messagebox.showerror("エラー", str(exc))
                self.destroy()
                return None
            self.image_frame = frame
            self.total = 1
            self.fps   = 30
        else:
            self.cap   = cv2.VideoCapture(media_path)
            self.total = int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            self.fps   = self.cap.get(cv2.CAP_PROP_FPS) or 30
            ok, frame  = self.cap.read()
            if not ok:
                messagebox.showerror("エラー", "動画を読み込めませんでした。")
                self.destroy()
                return None

        self.orig_h, self.orig_w = frame.shape[:2]
        return frame

    def _update_file_label(self):
        if not self._all_paths:
            return
        n    = len(self._all_paths)
        name = os.path.basename(self._all_paths[self._file_idx])
        self._file_label.config(text=f"{self._file_idx + 1} / {n}  {name}")

    def _switch_file(self, idx):
        self.playing = False
        # 現在ファイルの boxes を保存
        cur_path = self._all_paths[self._file_idx]
        self._file_boxes[cur_path] = list(self.boxes)

        path        = self._all_paths[idx]
        from video_masker.media import get_media_type as _gmt
        mtype       = _gmt(path) or "image"
        frame       = self._open_file(path, mtype)
        if frame is None:
            return
        self._file_idx = idx
        self._update_file_label()

        # 解像度が変わる可能性があるため scale を再計算
        max_w = self.winfo_screenwidth() - 80
        max_h = self.winfo_screenheight() - 340
        self.base_scale = min(1.0, max_w / self.orig_w, max_h / self.orig_h)
        self.zoom = 1.0
        self.view_w = int(self.orig_w * self.base_scale)
        self.view_h = int(self.orig_h * self.base_scale)
        self.update_display_size()
        self.canvas.configure(width=self.view_w, height=self.view_h)
        self.canvas.xview_moveto(0)
        self.canvas.yview_moveto(0)
        self.zoom_label.config(text="100%")

        # 新しいファイルの boxes をロード
        self.boxes = list(self._file_boxes.get(path, []))

        # シークバー行の表示を更新
        if self.media_type == "video":
            self.seek.config(to=max(0, self.total - 1))
            self.seek_var.set(0)
            total_time = _frames_to_time(self.total, self.fps)
            self.time_label.config(text="0:00 / " + total_time)
            self._seek_row.pack(fill="x", padx=10, pady=8,
                                before=self._btns_row)
        else:
            self._seek_row.pack_forget()

        self.cur_frame = 0
        self.show_frame(0)

    def _prev_file(self):
        if self._all_paths:
            self._switch_file((self._file_idx - 1) % len(self._all_paths))

    def _next_file(self):
        if self._all_paths:
            self._switch_file((self._file_idx + 1) % len(self._all_paths))

    # ── ヘルパー: ボックス座標をスケール後の canvas 座標に変換 ──────────

    def _box_canvas_coords(self, box):
        """box (x, y, w, h, ...) → (x0, y0, x1, y1) in canvas coords"""
        x, y, w, h = box[:4]
        return (x * self.scale, y * self.scale,
                (x + w) * self.scale, (y + h) * self.scale)

    def _corners(self, box):
        """box の 4 角を {"tl", "tr", "bl", "br"} → (cx, cy) dict で返す"""
        x0, y0, x1, y1 = self._box_canvas_coords(box)
        return {
            "tl": (x0, y0),
            "tr": (x1, y0),
            "bl": (x0, y1),
            "br": (x1, y1),
        }

    def _hit_corner(self, cx, cy, box):
        """(cx, cy) がどの角に近いか返す。どれにも近くなければ None。"""
        for name, (px, py) in self._corners(box).items():
            if abs(cx - px) <= _CORNER_HIT and abs(cy - py) <= _CORNER_HIT:
                return name
        return None

    def _inside_box(self, cx, cy, box):
        """(cx, cy) が box の内側にあれば True"""
        x0, y0, x1, y1 = self._box_canvas_coords(box)
        return x0 <= cx <= x1 and y0 <= cy <= y1

    # ─────────────────────────────────────────────────────────────────────

    def update_display_size(self):
        self.scale = self.base_scale * self.zoom
        self.disp_w = max(1, int(self.orig_w * self.scale))
        self.disp_h = max(1, int(self.orig_h * self.scale))

    def update_scrollbars(self):
        self.canvas.configure(scrollregion=(0, 0, self.disp_w, self.disp_h))
        if self.disp_w <= self.view_w:
            self.h_scroll.grid_remove()
        else:
            self.h_scroll.grid()
        if self.disp_h <= self.view_h:
            self.v_scroll.grid_remove()
        else:
            self.v_scroll.grid()

    def canvas_point(self, event):
        return self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)

    def change_zoom(self, factor):
        old_zoom = self.zoom
        self.zoom = max(1.0, min(6.0, self.zoom * factor))
        if self.zoom == old_zoom:
            return
        self.update_display_size()
        self.zoom_label.config(text=str(int(self.zoom * 100)) + "%")
        self.show_frame(self.cur_frame)

    def reset_zoom(self):
        self.zoom = 1.0
        self.update_display_size()
        self.zoom_label.config(text="100%")
        self.canvas.xview_moveto(0)
        self.canvas.yview_moveto(0)
        self.show_frame(self.cur_frame)

    def on_mouse_wheel(self, event):
        delta = getattr(event, "delta", 0)
        if event.num == 5 or delta < 0:
            self.change_zoom(0.8)
        else:
            self.change_zoom(1.25)
        return "break"

    def read_frame(self, idx):
        if self.media_type == "image":
            return self.image_frame.copy()
        self.cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ok, frame = self.cap.read()
        return frame if ok else None

    def show_frame(self, idx):
        idx = max(0, min(idx, max(0, self.total - 1)))
        self.cur_frame = idx
        frame = self.read_frame(idx)
        if frame is None:
            return
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        img = Image.fromarray(rgb).resize((self.disp_w, self.disp_h))
        self.tk_img = ImageTk.PhotoImage(img)
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, anchor="nw", image=self.tk_img)
        self.update_scrollbars()
        self.draw_boxes()
        if self.media_type == "video":
            cur_time = _frames_to_time(idx, self.fps)
            total_time = _frames_to_time(self.total, self.fps)
            self.time_label.config(text=cur_time + " / " + total_time)

    def draw_boxes(self):
        fill_color = _bgr_to_hex(self.color_bgr)
        for i, box in enumerate(self.boxes, start=1):
            x, y, w, h = box[:4]
            x0, y0 = x * self.scale, y * self.scale
            x1, y1 = (x + w) * self.scale, (y + h) * self.scale
            self.canvas.create_rectangle(x0, y0, x1, y1, fill=fill_color, stipple="gray50", outline="")
            self.canvas.create_rectangle(x0, y0, x1, y1, outline="#FFFFFF", width=4)
            self.canvas.create_rectangle(x0, y0, x1, y1, outline="#D55E00", width=2)
            self.canvas.create_rectangle(x0, y0, x0 + 22, y0 + 18, fill="#D55E00", outline="#FFFFFF")
            self.canvas.create_text(x0 + 11, y0 + 9, text=str(i), fill="#FFFFFF", font=("Helvetica", 11, "bold"))

    # ── マウスイベント ────────────────────────────────────────────────────

    def on_motion(self, event):
        """ホバー時にカーソルを変更する"""
        cx, cy = self.canvas_point(event)
        # 後ろのボックスが上に重なる場合を考慮して逆順チェック
        for box in reversed(self.boxes):
            corner = self._hit_corner(cx, cy, box)
            if corner:
                self.canvas.config(cursor="sizing")
                return
            if self._inside_box(cx, cy, box):
                self.canvas.config(cursor="fleur")
                return
        self.canvas.config(cursor="cross")

    def on_press(self, event):
        cx, cy = self.canvas_point(event)

        # 逆順でボックスを走査（最前面のボックス優先）
        for i in range(len(self.boxes) - 1, -1, -1):
            box = self.boxes[i]
            corner = self._hit_corner(cx, cy, box)
            if corner:
                self._edit_mode    = "resize"
                self._edit_idx     = i
                self._resize_corner = corner
                return
            if self._inside_box(cx, cy, box):
                x, y = box[:2]
                self._edit_mode   = "move"
                self._edit_idx    = i
                self._move_offset = (cx - x * self.scale, cy - y * self.scale)
                return

        # ボックス外 → 新規描画
        self._edit_mode = "draw"
        self.start_xy = (cx, cy)
        self.temp_rect = self.canvas.create_rectangle(
            cx, cy, cx, cy,
            outline="#D55E00",
            width=2,
            dash=(5, 3),
        )

    def on_drag(self, event):
        cx, cy = self.canvas_point(event)

        if self._edit_mode == "draw":
            if self.temp_rect is not None:
                x0, y0 = self.start_xy
                self.canvas.coords(self.temp_rect, x0, y0, cx, cy)

        elif self._edit_mode == "move":
            idx = self._edit_idx
            if 0 <= idx < len(self.boxes):
                box = self.boxes[idx]
                _, _, w, h = box[:2] + box[2:4]
                # canvas 座標からオリジナル座標に変換
                new_x = int((cx - self._move_offset[0]) / self.scale)
                new_y = int((cy - self._move_offset[1]) / self.scale)
                # 画像境界でクランプ
                new_x = max(0, min(new_x, self.orig_w - w))
                new_y = max(0, min(new_y, self.orig_h - h))
                start_frame = box[4] if len(box) > 4 else 0
                self.boxes[idx] = (new_x, new_y, w, h, start_frame)
                self.show_frame(self.cur_frame)

        elif self._edit_mode == "resize":
            idx = self._edit_idx
            if 0 <= idx < len(self.boxes):
                box = self.boxes[idx]
                ox, oy, ow, oh = box[:4]
                start_frame = box[4] if len(box) > 4 else 0
                # 現在の角の対角座標
                x0_orig = ox * self.scale
                y0_orig = oy * self.scale
                x1_orig = (ox + ow) * self.scale
                y1_orig = (oy + oh) * self.scale

                corner = self._resize_corner
                if corner == "tl":
                    x0_new, y0_new, x1_new, y1_new = cx, cy, x1_orig, y1_orig
                elif corner == "tr":
                    x0_new, y0_new, x1_new, y1_new = x0_orig, cy, cx, y1_orig
                elif corner == "bl":
                    x0_new, y0_new, x1_new, y1_new = cx, y0_orig, x1_orig, cy
                else:  # br
                    x0_new, y0_new, x1_new, y1_new = x0_orig, y0_orig, cx, cy

                # 最小サイズ制約 (5px in original coords)
                min_canvas = 5 * self.scale
                if abs(x1_new - x0_new) < min_canvas:
                    if corner in ("tl", "bl"):
                        x0_new = x1_new - min_canvas
                    else:
                        x1_new = x0_new + min_canvas
                if abs(y1_new - y0_new) < min_canvas:
                    if corner in ("tl", "tr"):
                        y0_new = y1_new - min_canvas
                    else:
                        y1_new = y0_new + min_canvas

                nx  = int(min(x0_new, x1_new) / self.scale)
                ny  = int(min(y0_new, y1_new) / self.scale)
                nw  = max(5, int(abs(x1_new - x0_new) / self.scale))
                nh  = max(5, int(abs(y1_new - y0_new) / self.scale))
                # 画像境界でクランプ
                nx = max(0, min(nx, self.orig_w - nw))
                ny = max(0, min(ny, self.orig_h - nh))
                self.boxes[idx] = (nx, ny, nw, nh, start_frame)
                self.show_frame(self.cur_frame)

    def on_release(self, event):
        if self._edit_mode == "move" or self._edit_mode == "resize":
            # 座標は on_drag で書き戻し済み
            self._edit_mode    = "draw"
            self._edit_idx     = -1
            self._resize_corner = None
            return

        # draw モード
        if self.start_xy is None:
            return
        x0, y0 = self.start_xy
        x1, y1 = self.canvas_point(event)
        self.start_xy = None
        self.temp_rect = None
        x, y = min(x0, x1), min(y0, y1)
        w, h = abs(x1 - x0), abs(y1 - y0)
        if w < 5 or h < 5:
            self.show_frame(self.cur_frame)
            return
        start_frame = self.cur_frame if self.media_type == "video" else 0
        self.boxes.append((int(x / self.scale), int(y / self.scale),
                           int(w / self.scale), int(h / self.scale), start_frame))
        self.show_frame(self.cur_frame)

    def on_seek(self, _=None):
        if not self.playing:
            self.show_frame(int(float(self.seek_var.get())))

    def toggle_play(self):
        self.playing = not self.playing
        self.play_btn.config(text="⏸ 一時停止" if self.playing else "▶ 再生")
        if self.playing:
            self.play_loop()

    def play_loop(self):
        if not self.playing:
            return
        nxt = self.cur_frame + 1
        if nxt >= self.total:
            self.playing = False
            self.play_btn.config(text="▶ 再生")
            return
        self.seek_var.set(nxt)
        self.show_frame(nxt)
        self.after(int(1000 / self.fps), self.play_loop)

    def undo(self):
        if self.boxes:
            self.boxes.pop()
            self.show_frame(self.cur_frame)

    def clear(self):
        self.boxes = []
        self.show_frame(self.cur_frame)

    def on_right_click(self, event):
        click_x, click_y = self.canvas_point(event)
        for i in range(len(self.boxes) - 1, -1, -1):
            x, y, w, h = self.boxes[i][:4]
            if x * self.scale <= click_x <= (x + w) * self.scale and y * self.scale <= click_y <= (y + h) * self.scale:
                self.boxes.pop(i)
                self.show_frame(self.cur_frame)
                return

    def confirm(self):
        self.playing = False
        if self._all_paths:
            # 現在ファイルの boxes を保存してから dict を返す
            cur_path = self._all_paths[self._file_idx]
            self._file_boxes[cur_path] = list(self.boxes)
            self.result = dict(self._file_boxes)
        else:
            self.result = list(self.boxes)
        if self.cap is not None:
            self.cap.release()
        self.destroy()

    def cancel(self):
        self.playing = False
        self.result = None
        if self.cap is not None:
            self.cap.release()
        self.destroy()
