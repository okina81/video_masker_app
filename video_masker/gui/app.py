import json
import os
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from video_masker.gui.preview import FinishPreview
from video_masker.gui.roi_selector import RoiSelector
from video_masker.masking import MANUAL_COLORS, make_manual_masker
from video_masker.media import get_media_type
from video_masker.model import ModelPreparationError
from video_masker.processing import process_image, process_video
from video_masker.system import open_folder


# ── カラーパレット ─────────────────────────────────────────
BG          = "#EDF1F7"
SURFACE     = "#FFFFFF"
SURFACE_ALT = "#F7FAFC"
PRIMARY     = "#1A6B63"
PRIMARY_LT  = "#E6F4F2"
PRIMARY_DK  = "#0F4F48"
TEXT        = "#111827"
TEXT_MUTED  = "#6B7280"
BORDER      = "#DDE3EC"
SHADOW_C    = "#C9D2DC"
SUCCESS     = "#059669"
DANGER      = "#DC2626"

SETTINGS_PATH = os.path.join(os.path.expanduser("~"), ".video_masker_settings.json")


def _bgr_to_hex(bgr):
    b, g, r = bgr
    return f"#{r:02x}{g:02x}{b:02x}"


# ── カスタムウィジェット ───────────────────────────────────

class _Swatch(tk.Canvas):
    """クリック可能なカラースウォッチ"""
    SIZE = 26

    def __init__(self, parent, color_name, color_bgr, var, **kw):
        super().__init__(parent, width=self.SIZE, height=self.SIZE,
                         highlightthickness=0, cursor="hand2", **kw)
        self.color_name = color_name
        self.hex_color  = _bgr_to_hex(color_bgr)
        self.var        = var
        self._draw()
        self.bind("<ButtonRelease-1>", self._on_click)
        var.trace_add("write", lambda *_: self._draw())

    def _draw(self):
        self.delete("all")
        selected = self.var.get() == self.color_name
        if selected:
            self.create_rectangle(0, 0, self.SIZE, self.SIZE,
                                  fill=PRIMARY, outline="")
        self.create_rectangle(3, 3, self.SIZE - 3, self.SIZE - 3,
                              fill=self.hex_color,
                              outline=SURFACE if selected else BORDER,
                              width=2 if selected else 1)

    def _on_click(self, _event):
        self.var.set(self.color_name)


class _ToggleBtn(tk.Label):
    """セグメントコントロール風トグルボタン"""

    def __init__(self, parent, text, value, var, **kw):
        super().__init__(parent, text=text, cursor="hand2",
                         font=("Helvetica", 12), padx=14, pady=7,
                         relief="flat", **kw)
        self.value = value
        self.var   = var
        self._refresh()
        self.bind("<ButtonRelease-1>", self._on_click)
        var.trace_add("write", lambda *_: self._refresh())

    def _refresh(self):
        if self.var.get() == self.value:
            self.config(bg=PRIMARY, fg="white")
        else:
            self.config(bg=SURFACE_ALT, fg=TEXT_MUTED)

    def _on_click(self, _event):
        self.var.set(self.value)


class _Btn(tk.Label):
    """bg/fg が Mac でも確実に効くクロスプラットフォームボタン。

    tk.Button は macOS Aqua テーマで bg/fg を無視するため、
    tk.Label ベースで実装し hover・click をバインドで再現する。
    """

    def __init__(self, parent, text, command, font,
                 bg, fg, active_bg, padx=16, pady=10, **kw):
        super().__init__(parent, text=text, font=font,
                         bg=bg, fg=fg, padx=padx, pady=pady,
                         cursor="hand2", **kw)
        self._bg        = bg
        self._fg        = fg
        self._active_bg = active_bg
        self._command   = command
        self._enabled   = True

        self.bind("<Enter>",           lambda _: self._on_enter())
        self.bind("<Leave>",           lambda _: self._on_leave())
        self.bind("<ButtonRelease-1>", lambda _: self._on_release())

    def _on_enter(self):
        if self._enabled:
            tk.Label.config(self, bg=self._active_bg)

    def _on_leave(self):
        if self._enabled:
            tk.Label.config(self, bg=self._bg)

    def _on_release(self):
        if self._enabled:
            self._on_leave()
            self._command()

    def config(self, **kw):
        state = kw.pop("state", None)
        if state == "disabled":
            self._enabled = False
            tk.Label.config(self, fg="#AAAAAA", bg=self._bg)
        elif state == "normal":
            self._enabled = True
            tk.Label.config(self, fg=self._fg, bg=self._bg)

        if "command" in kw:
            self._command = kw.pop("command")
        if "activebackground" in kw:
            self._active_bg = kw.pop("activebackground")
        kw.pop("activeforeground", None)

        if "bg" in kw:
            self._bg = kw["bg"]
            if self._enabled:
                tk.Label.config(self, bg=self._bg)
            kw.pop("bg")
        if "fg" in kw:
            self._fg = kw["fg"]
            if self._enabled:
                tk.Label.config(self, fg=self._fg)
            kw.pop("fg")

        if kw:
            tk.Label.config(self, **kw)

    def configure(self, **kw):
        self.config(**kw)


# ── メインアプリ ──────────────────────────────────────────

class MaskApp:
    def __init__(self, root):
        self.root = root
        self.input_path   = None
        self._input_paths: list = []
        self.media_type   = None
        self.manual_boxes = []
        self._manual_boxes_per_file: dict = {}
        self._stop_event  = threading.Event()
        self._proc_start  = 0.0

        root.title("画像・動画かくしツール")
        root.geometry("700x920")
        root.configure(bg=BG)
        root.resizable(True, True)

        try:
            root.tk.call("package", "require", "tkdnd")
            root.drop_target_register("DND_Files")
            root.dnd_bind("<<Drop>>", self._on_drop)
        except Exception:
            pass

        self._configure_styles()
        self._build_ui()

    # ── スタイル設定 ──────────────────────────────────────

    def _configure_styles(self):
        s = ttk.Style()
        s.configure("Mask.Horizontal.TProgressbar",
                    troughcolor=BORDER, background=PRIMARY, thickness=8)

    # ── UI構築 ────────────────────────────────────────────

    def _build_ui(self):
        F_BIG   = ("Helvetica", 15, "bold")
        F_MID   = ("Helvetica", 13)
        F_SMALL = ("Helvetica", 11)

        shell = self._scroll_shell(self.root)

        # ヘッダー
        hdr = tk.Frame(shell, bg=PRIMARY, padx=28, pady=24)
        hdr.pack(fill="x")
        tk.Label(hdr, text="🔒  画像・動画かくしツール",
                 font=("Helvetica", 22, "bold"),
                 bg=PRIMARY, fg="white").pack(anchor="w")
        tk.Label(hdr,
                 text="顔検出・手動範囲・追従・プレビューをひとつの画面で",
                 font=("Helvetica", 12),
                 bg=PRIMARY, fg="#B2F5EE").pack(anchor="w", pady=(6, 0))

        content = tk.Frame(shell, bg=BG, padx=24, pady=20)
        content.pack(fill="both", expand=True)

        # ── STEP 1: ファイルをえらぶ ──
        s1 = self._card(content, "1", "ファイルをえらぶ")

        dz = tk.Frame(s1, bg=PRIMARY_LT,
                      highlightbackground=PRIMARY, highlightthickness=2,
                      cursor="hand2")
        dz.pack(fill="x", pady=(4, 0))
        dz_lbl = tk.Label(dz, text="📂  クリックしてファイルをえらぶ",
                          font=F_BIG, bg=PRIMARY_LT, fg=PRIMARY,
                          pady=20, cursor="hand2")
        dz_lbl.pack()

        def _dz_enter(_): dz.config(bg="#D3EEEB"); dz_lbl.config(bg="#D3EEEB")
        def _dz_leave(_): dz.config(bg=PRIMARY_LT); dz_lbl.config(bg=PRIMARY_LT)
        def _dz_click(_): self.pick_inputs()
        for w in (dz, dz_lbl):
            w.bind("<Enter>", _dz_enter)
            w.bind("<Leave>", _dz_leave)
            w.bind("<ButtonRelease-1>", _dz_click)

        self.file_label = tk.Label(s1, text="まだ選んでいません",
                                   font=F_MID, bg=SURFACE, fg=TEXT_MUTED)
        self.file_label.pack(anchor="w", pady=(12, 0))

        # ── STEP 2: かくすものを決める ──
        s2 = self._card(content, "2", "かくすものを決める")

        # 顔チェック
        self.faces_var = tk.BooleanVar(value=True)
        face_row = tk.Frame(s2, bg=SURFACE)
        face_row.pack(fill="x", pady=(0, 8))
        tk.Checkbutton(face_row, text="顔を自動でかくす",
                       font=F_MID, variable=self.faces_var,
                       bg=SURFACE, fg=TEXT,
                       activebackground=SURFACE, selectcolor=SURFACE,
                       cursor="hand2",
                       command=self.toggle_face_settings).pack(side="left")

        # 顔設定まとめフレーム（折りたたみ対象）
        self.face_settings_frame = tk.Frame(s2, bg=SURFACE)
        self.face_settings_frame.pack(fill="x")

        # 顔認識レベル
        self._slider_row(self.face_settings_frame,
                         "顔認識のレベル",
                         var_attr="score_var", label_attr="score_label",
                         from_=0.2, to=0.9, default=0.5,
                         cb=self.on_score_change,
                         hint="低いほど検出されやすく、高いほど確実な顔だけを検出します。")

        # 顔の隠す範囲
        self._slider_row(self.face_settings_frame,
                         "顔の隠す範囲",
                         var_attr="margin_var", label_attr="margin_label",
                         from_=0.0, to=0.5, default=0.0,
                         cb=self.on_margin_change,
                         hint="広くすると顔の周囲をより大きく隠します。ずれが気になる場合に調整してください。")

        # SCRFD フォールバック
        scrfd_row = tk.Frame(self.face_settings_frame, bg=SURFACE)
        scrfd_row.pack(fill="x", pady=(0, 6))
        self.scrfd_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            scrfd_row,
            text="検出漏れ時に SCRFD で補完する",
            font=("Helvetica", 13),
            variable=self.scrfd_var,
            bg=SURFACE, fg=TEXT,
            activebackground=SURFACE, selectcolor=SURFACE,
            cursor="hand2",
        ).pack(side="left")
        tk.Label(
            scrfd_row,
            text="（初回のみ約16MBをダウンロード）",
            font=("Helvetica", 10),
            bg=SURFACE, fg=TEXT_MUTED,
        ).pack(side="left", padx=(4, 0))

        # かくし方トグル
        mode_sec = tk.Frame(self.face_settings_frame, bg=SURFACE)
        mode_sec.pack(fill="x", pady=(0, 10))
        tk.Label(mode_sec, text="かくし方", font=("Helvetica", 11),
                 bg=SURFACE, fg=TEXT_MUTED).pack(anchor="w")
        self.mode_var = tk.StringVar(value="mosaic")
        seg = tk.Frame(mode_sec, bg=BORDER, padx=1, pady=1)
        seg.pack(anchor="w", pady=(6, 0))
        for lbl, val in [("モザイク", "mosaic"), ("ぼかし", "blur"), ("黒塗り", "fill")]:
            _ToggleBtn(seg, lbl, val, self.mode_var).pack(side="left", padx=1, pady=0)

        ttk.Separator(s2).pack(fill="x", pady=14)

        # 手動範囲セクション
        tk.Label(s2, text="手動で範囲を指定する",
                 font=("Helvetica", 13, "bold"), bg=SURFACE, fg=TEXT).pack(anchor="w")

        # 色スウォッチ
        crow = tk.Frame(s2, bg=SURFACE)
        crow.pack(anchor="w", pady=(10, 4))
        tk.Label(crow, text="色", font=F_SMALL, bg=SURFACE,
                 fg=TEXT_MUTED, width=5, anchor="w").pack(side="left")
        self.manual_color_var = tk.StringVar(value="黒")
        for name, bgr in MANUAL_COLORS.items():
            _Swatch(crow, name, bgr, self.manual_color_var,
                    bg=SURFACE).pack(side="left", padx=3)

        # デザイントグル
        drow = tk.Frame(s2, bg=SURFACE)
        drow.pack(anchor="w", pady=(0, 10))
        tk.Label(drow, text="デザイン", font=F_SMALL, bg=SURFACE,
                 fg=TEXT_MUTED, width=7, anchor="w").pack(side="left")
        self.manual_design_var = tk.StringVar(value="ベタ塗り")
        seg2 = tk.Frame(drow, bg=BORDER, padx=1, pady=1)
        seg2.pack(side="left")
        for name in ["ベタ塗り", "斜線", "チェック"]:
            _ToggleBtn(seg2, name, name, self.manual_design_var).pack(side="left", padx=1)

        # 範囲選択ボタン
        sel_row = tk.Frame(s2, bg=SURFACE)
        sel_row.pack(fill="x", pady=(4, 6))
        self._btn(sel_row, "✏️  塗る範囲をえらぶ", self.open_selector,
                  F_MID, "secondary").pack(side="left")
        self.manual_label = tk.Label(sel_row, text="範囲: なし",
                                     font=F_MID, bg=SURFACE, fg=TEXT_MUTED)
        self.manual_label.pack(side="left", padx=14)

        # 追従チェック
        self.track_var = tk.BooleanVar(value=False)
        self._track_frame = tk.Frame(s2, bg=SURFACE)
        self._track_frame.pack(anchor="w", pady=(2, 0))
        self._track_cb = tk.Checkbutton(
            self._track_frame,
            text="塗った範囲を動くものに追従させる",
            font=F_MID, variable=self.track_var,
            bg=SURFACE, fg=TEXT,
            activebackground=SURFACE, selectcolor=SURFACE,
            cursor="hand2",
        )
        self._track_cb.pack(side="left")

        # ── STEP 3: 確認して書き出す ──
        s3 = self._card(content, "3", "確認して書き出す")

        # 保存先フォルダ行
        out_folder_row = tk.Frame(s3, bg=SURFACE)
        out_folder_row.pack(fill="x", pady=(0, 4))
        tk.Label(out_folder_row, text="保存先フォルダ", font=F_SMALL,
                 bg=SURFACE, fg=TEXT_MUTED, width=12, anchor="w").pack(side="left")
        self._out_folder_var = tk.StringVar(value="")
        out_folder_entry = tk.Entry(out_folder_row, textvariable=self._out_folder_var,
                                    font=F_SMALL, fg=TEXT, bg=SURFACE_ALT,
                                    relief="flat", bd=1,
                                    highlightbackground=BORDER, highlightthickness=1)
        out_folder_entry.pack(side="left", fill="x", expand=True, padx=(6, 6))
        self._btn(out_folder_row, "変更", self._pick_out_folder,
                  ("Helvetica", 11), "secondary").pack(side="left")
        tk.Label(s3, text="空欄のときは入力ファイルと同じフォルダに保存します",
                 font=("Helvetica", 10), bg=SURFACE, fg=TEXT_MUTED).pack(anchor="w", pady=(0, 6))

        # ファイル名行
        out_name_row = tk.Frame(s3, bg=SURFACE)
        out_name_row.pack(fill="x", pady=(0, 4))
        tk.Label(out_name_row, text="ファイル名", font=F_SMALL,
                 bg=SURFACE, fg=TEXT_MUTED, width=12, anchor="w").pack(side="left")
        self._out_name_var = tk.StringVar(value="")
        self._out_name_entry = tk.Entry(out_name_row, textvariable=self._out_name_var,
                                        font=F_SMALL, fg=TEXT, bg=SURFACE_ALT,
                                        relief="flat", bd=1,
                                        highlightbackground=BORDER, highlightthickness=1)
        self._out_name_entry.pack(side="left", fill="x", expand=True, padx=(6, 0))
        self._out_name_hint = tk.Label(s3, text="空欄のときは「元ファイル名_かくし済み」になります（拡張子は自動）",
                                       font=("Helvetica", 10), bg=SURFACE, fg=TEXT_MUTED)
        self._out_name_hint.pack(anchor="w", pady=(0, 10))

        btn_row = tk.Frame(s3, bg=SURFACE)
        btn_row.pack(fill="x")
        self.preview_btn = self._btn(btn_row, "👁  仕上がりプレビュー",
                                     self.open_finish_preview, F_BIG, "secondary")
        self.preview_btn.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self.start_btn = self._btn(btn_row, "🚀  書き出す",
                                   self.on_run, F_BIG, "primary")
        self.start_btn.pack(side="left", fill="x", expand=True, padx=(6, 0))

        stat = tk.Frame(s3, bg=SURFACE_ALT,
                        highlightbackground=BORDER, highlightthickness=1,
                        padx=14, pady=12)
        stat.pack(fill="x", pady=(14, 0))
        self.status = tk.Label(stat, text="準備できています",
                               font=F_MID, bg=SURFACE_ALT, fg=TEXT)
        self.status.pack(anchor="w")
        self.progress = ttk.Progressbar(stat, maximum=100,
                                        style="Mask.Horizontal.TProgressbar")
        self.progress.pack(fill="x", pady=(8, 0))

        # 設定の trace を登録して自動保存
        for var in (self.faces_var, self.score_var, self.margin_var,
                    self.mode_var, self.manual_color_var, self.manual_design_var,
                    self.track_var):
            var.trace_add("write", lambda *_: self._save_settings())

        self._load_settings()

    # ── ウィジェットヘルパー ──────────────────────────────

    def _card(self, parent, number, title):
        """左アクセントバー＋ドロップシャドウ風カード"""
        shadow = tk.Frame(parent, bg=SHADOW_C)
        shadow.pack(fill="x", pady=(0, 16))
        inner = tk.Frame(shadow, bg=SURFACE)
        inner.pack(fill="x", padx=(0, 2), pady=(0, 2))
        accent = tk.Frame(inner, bg=PRIMARY, width=5)
        accent.pack(side="left", fill="y")
        body = tk.Frame(inner, bg=SURFACE, padx=20, pady=18)
        body.pack(side="left", fill="both", expand=True)

        hdr = tk.Frame(body, bg=SURFACE)
        hdr.pack(fill="x", pady=(0, 14))
        tk.Label(hdr, text=f" {number} ",
                 font=("Helvetica", 11, "bold"),
                 bg=PRIMARY, fg="white", padx=4, pady=3).pack(side="left")
        tk.Label(hdr, text=title,
                 font=("Helvetica", 15, "bold"),
                 bg=SURFACE, fg=TEXT).pack(side="left", padx=10)
        return body

    def _btn(self, parent, text, command, font, style="primary"):
        if style == "primary":
            return _Btn(parent, text=text, command=command, font=font,
                        bg=PRIMARY, fg="white", active_bg=PRIMARY_DK,
                        padx=16, pady=12)
        return _Btn(parent, text=text, command=command, font=font,
                    bg=PRIMARY_LT, fg=PRIMARY, active_bg="#CCE9E6",
                    padx=16, pady=12)

    def _slider_row(self, parent, label,
                    var_attr, label_attr,
                    from_, to, default, cb, hint):
        panel = tk.Frame(parent, bg=SURFACE_ALT,
                         highlightbackground=BORDER, highlightthickness=1,
                         padx=14, pady=10)
        panel.pack(fill="x", pady=(0, 8))

        top = tk.Frame(panel, bg=SURFACE_ALT)
        top.pack(fill="x")
        tk.Label(top, text=label, font=("Helvetica", 12, "bold"),
                 bg=SURFACE_ALT, fg=TEXT).pack(side="left")
        badge = tk.Label(top, text="標準" if default == 0.0 else "ふつう",
                         font=("Helvetica", 10),
                         bg=PRIMARY_LT, fg=PRIMARY, padx=8, pady=2)
        badge.pack(side="right")
        setattr(self, label_attr, badge)

        var = tk.DoubleVar(value=default)
        setattr(self, var_attr, var)

        ttk.Scale(panel, from_=from_, to=to,
                  variable=var, orient="horizontal",
                  command=cb).pack(fill="x", pady=(8, 4))

        tk.Label(panel, text=hint, font=("Helvetica", 10),
                 bg=SURFACE_ALT, fg=TEXT_MUTED,
                 wraplength=580, justify="left").pack(anchor="w")

    # ── スクロールシェル ──────────────────────────────────

    def _scroll_shell(self, root):
        vp = tk.Frame(root, bg=BG)
        vp.pack(fill="both", expand=True)
        canvas = tk.Canvas(vp, bg=BG, highlightthickness=0)
        sb = ttk.Scrollbar(vp, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        shell = tk.Frame(canvas, bg=BG)
        wid = canvas.create_window((0, 0), window=shell, anchor="nw")
        shell.bind("<Configure>",
                   lambda _: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>",
                    lambda e: canvas.itemconfigure(wid, width=e.width))

        def _wheel(e):
            d = getattr(e, "delta", 0)
            canvas.yview_scroll(-3 if (e.num == 4 or d > 0) else 3, "units")
            return "break"
        canvas.bind_all("<MouseWheel>", _wheel)
        canvas.bind_all("<Button-4>", _wheel)
        canvas.bind_all("<Button-5>", _wheel)
        self.scroll_canvas = canvas
        return shell

    # ── ロジック ─────────────────────────────────────────

    def _on_drop(self, event):
        path = event.data.strip("{}")
        if path and os.path.isfile(path):
            self._load_files([path])

    def toggle_face_settings(self):
        if self.faces_var.get():
            self.face_settings_frame.pack(fill="x")
        else:
            self.face_settings_frame.pack_forget()

    def _load_files(self, paths):
        """paths: list[str] — 1本以上のファイルパス"""
        valid = []
        for p in paths:
            if get_media_type(p) is not None:
                valid.append(p)
        if not valid:
            messagebox.showinfo("おしらせ", "対応している画像または動画を選んでください。")
            return

        self._input_paths = valid
        # 先頭ファイルを代表として input_path / media_type に設定
        first = valid[0]
        self.input_path = first
        self.media_type = get_media_type(first)
        self.manual_boxes = []
        self._manual_boxes_per_file = {}
        self.manual_label.config(text="範囲: なし", fg=TEXT_MUTED)

        if len(valid) == 1:
            icon = "🖼" if self.media_type == "image" else "🎬"
            self.file_label.config(
                text=f"✓  {icon} {os.path.basename(first)}", fg=SUCCESS)
        else:
            self.file_label.config(
                text=f"✓  {len(valid)}ファイル選択中", fg=SUCCESS)

        self.status.config(text="設定を確認して、必要ならプレビューしてください。")

        # ファイル名入力欄: 複数ファイル時は無効化
        if len(valid) > 1:
            self._out_name_entry.config(state="disabled", bg="#EAECEF")
            self._out_name_hint.config(
                text="複数ファイル処理時は各ファイル名が「元ファイル名_かくし済み」になります")
        else:
            self._out_name_entry.config(state="normal", bg=SURFACE_ALT)
            self._out_name_hint.config(
                text="空欄のときは「元ファイル名_かくし済み」になります（拡張子は自動）")

        # 追従チェックは代表ファイル（先頭）に合わせて制御
        if self.media_type == "image":
            self.track_var.set(False)
            self._track_cb.config(state="disabled")
        else:
            self._track_cb.config(state="normal")

    # 後方互換ラッパー（内部利用）
    def _load_file(self, path):
        self._load_files([path])

    def pick_inputs(self):
        paths = filedialog.askopenfilenames(
            title="画像・動画をえらんでください（複数選択可）",
            filetypes=[
                ("画像・動画ファイル",
                 "*.jpg *.jpeg *.png *.bmp *.webp *.tif *.tiff "
                 "*.mp4 *.mov *.m4v *.avi"),
                ("画像ファイル",
                 "*.jpg *.jpeg *.png *.bmp *.webp *.tif *.tiff"),
                ("動画ファイル", "*.mp4 *.mov *.m4v *.avi"),
                ("すべてのファイル", "*.*"),
            ],
        )
        if paths:
            self._load_files(list(paths))

    # 後方互換エイリアス
    def pick_input(self):
        self.pick_inputs()

    def _pick_out_folder(self):
        folder = filedialog.askdirectory(title="保存先フォルダをえらんでください")
        if folder:
            self._out_folder_var.set(folder)

    def open_selector(self):
        if not self.input_path or not os.path.exists(self.input_path):
            messagebox.showinfo("おしらせ",
                                "さきに『ファイルをえらぶ』からファイルを選んでください。")
            return
        color_bgr = MANUAL_COLORS.get(self.manual_color_var.get(), (0, 0, 0))
        is_multi = len(self._input_paths) > 1
        selector = RoiSelector(
            self.root, self.input_path, self.manual_boxes, self.media_type,
            color_bgr=color_bgr, design=self.manual_design_var.get(),
            all_paths=self._input_paths if is_multi else None,
            all_boxes=self._manual_boxes_per_file if is_multi else None,
        )
        self.root.wait_window(selector)
        if selector.result is not None:
            if isinstance(selector.result, dict):
                # 複数ファイル: ファイルごとの boxes を保存
                self._manual_boxes_per_file = selector.result
                files_with_boxes = sum(
                    1 for boxes in self._manual_boxes_per_file.values() if boxes
                )
                if files_with_boxes:
                    self.manual_label.config(
                        text=f"範囲: {files_with_boxes}ファイルで設定済み",
                        fg=PRIMARY,
                    )
                else:
                    self.manual_label.config(text="範囲: なし", fg=TEXT_MUTED)
            else:
                # 単一ファイル: 従来通り
                self.manual_boxes = selector.result
                count = len(self.manual_boxes)
                self.manual_label.config(
                    text=f"範囲: {count}か所" if count else "範囲: なし",
                    fg=PRIMARY if count else TEXT_MUTED,
                )

    def set_status(self, message):
        self.status.config(text=message)

    @staticmethod
    def _fmt_time(sec):
        sec = max(0, int(sec))
        return f"{sec // 60}:{sec % 60:02d}"

    def set_progress(self, cur, total):
        pct = int(cur / total * 100) if total else 0
        self.progress["value"] = pct
        if cur > 0:
            elapsed = time.time() - self._proc_start
            if elapsed > 0:
                rate = cur / elapsed
                remaining = (total - cur) / rate if rate > 0 else 0
                eta = self._fmt_time(remaining)
                self.status.config(text=f"かくしています…  {pct}%  (あと約 {eta})")
            else:
                self.status.config(text=f"かくしています…  {pct}%")
        else:
            self.status.config(text=f"かくしています…  {pct}%")
        self.root.update_idletasks()

    def on_score_change(self, _=None):
        s = self.score_var.get()
        text = "高感度" if s < 0.4 else "ふつう" if s < 0.7 else "厳しめ"
        self.score_label.config(text=text)

    def on_margin_change(self, _=None):
        m = self.margin_var.get()
        text = "標準" if m < 0.15 else "やや広め" if m < 0.30 else "広め"
        self.margin_label.config(text=text)

    def build_run_params(self, input_path=None):
        manual_masker_fn = make_manual_masker(
            self.manual_color_var.get(),
            self.manual_design_var.get(),
        )
        resolved_path = input_path or self.input_path
        # ファイルごとの boxes があればそれを使い、なければ共通の manual_boxes を使う
        if input_path and input_path in self._manual_boxes_per_file:
            boxes = list(self._manual_boxes_per_file[input_path])
        else:
            boxes = list(self.manual_boxes)
        return dict(
            input_path=resolved_path,
            mode=self.mode_var.get(),
            use_faces=self.faces_var.get(),
            manual_boxes=boxes,
            manual_masker_fn=manual_masker_fn,
            track=self.track_var.get(),
            score=float(self.score_var.get()),
            face_margin=float(self.margin_var.get()),
            use_scrfd=self.scrfd_var.get(),
        )

    def open_finish_preview(self):
        if not self.input_path or not os.path.exists(self.input_path):
            messagebox.showinfo("おしらせ", "さきにファイルを選んでください。")
            return
        params = self.build_run_params()
        if self.media_type == "image":
            params.pop("track")
        is_multi = len(self._input_paths) > 1
        try:
            FinishPreview(self.root, self.input_path, self.media_type, params,
                          all_paths=self._input_paths if is_multi else None,
                          manual_boxes_per_file=self._manual_boxes_per_file if is_multi else None)
        except ModelPreparationError as exc:
            messagebox.showerror("プレビューエラー", str(exc))
        except Exception as exc:
            messagebox.showerror("プレビューエラー", f"プレビューを開けませんでした: {exc}")

    def _cancel(self):
        self._stop_event.set()
        self.set_status("キャンセルしています…")

    def _make_output_path(self, input_path, media_type, use_custom_name):
        """1ファイル分の出力パスを返す。
        use_custom_name=True のとき _out_name_var を使う（1ファイル処理時のみ）。"""
        output_ext = ".mp4" if media_type == "video" else os.path.splitext(input_path)[1]
        folder = self._out_folder_var.get().strip() or os.path.dirname(input_path)
        if use_custom_name:
            name = self._out_name_var.get().strip()
        else:
            name = ""
        if not name:
            base = os.path.splitext(os.path.basename(input_path))[0]
            name = base + "_かくし済み"
        return os.path.join(folder, name + output_ext)

    def on_run(self):
        if not self._input_paths and not self.input_path:
            messagebox.showinfo("おしらせ", "さきにファイルを選んでください。")
            return

        # _input_paths が空の場合（後方互換）は input_path を使う
        paths = self._input_paths if self._input_paths else [self.input_path]
        # 存在確認
        paths = [p for p in paths if os.path.exists(p)]
        if not paths:
            messagebox.showinfo("おしらせ", "選択したファイルが見つかりません。")
            return

        is_batch = len(paths) > 1
        # カスタム名は1ファイル処理時のみ使用
        use_custom_name = not is_batch

        self._stop_event.clear()
        self._proc_start = time.time()

        self.start_btn.config(
            state="normal", text="⏹  キャンセル",
            bg=DANGER, fg="white",
            activebackground="#B91C1C",
            command=self._cancel,
        )
        self.preview_btn.config(state="disabled")
        self.progress["value"] = 0
        self.set_status("準備しています…")

        stop_event = self._stop_event

        def _restore_start_btn():
            self.start_btn.config(
                state="normal", text="🚀  書き出す",
                bg=PRIMARY, fg="white",
                activebackground=PRIMARY_DK,
                command=self.on_run,
            )
            self.preview_btn.config(state="normal")

        def task():
            errors = []
            last_output_path = None
            total_files = len(paths)

            for file_idx, input_path in enumerate(paths):
                if stop_event.is_set():
                    break

                file_name = os.path.basename(input_path)
                media_type = get_media_type(input_path)
                if media_type is None:
                    errors.append(f"{file_name}: 対応していないファイル形式です")
                    continue

                if is_batch:
                    status_prefix = f"{file_idx + 1} / {total_files}: {file_name} 処理中…"
                    self.root.after(0, self.set_status, status_prefix)

                output_path = self._make_output_path(input_path, media_type, use_custom_name)
                last_output_path = output_path

                params = self.build_run_params(input_path=input_path)
                params.update(output_path=output_path)

                try:
                    processor = process_video if media_type == "video" else process_image
                    if media_type == "image":
                        params.pop("track", None)
                        processor(
                            progress_cb=lambda cur, tot:
                                self.root.after(0, self.set_progress, cur, tot),
                            log_cb=lambda msg:
                                self.root.after(0, self.set_status, msg),
                            **params,
                        )
                    else:
                        processor(
                            progress_cb=lambda cur, tot:
                                self.root.after(0, self.set_progress, cur, tot),
                            log_cb=lambda msg:
                                self.root.after(0, self.set_status, msg),
                            stop_event=stop_event,
                            **params,
                        )
                except Exception as exc:
                    errors.append(f"{file_name}: {exc}")

            if stop_event.is_set():
                self.root.after(0, self.set_status, "キャンセルしました")
                self.root.after(0, _restore_start_btn)
                return

            def done():
                _restore_start_btn()
                if errors:
                    completed = total_files - len(errors)
                    err_msg = "\n".join(errors)
                    self.set_status(f"⚠️  {completed} / {total_files} ファイル処理しました（{len(errors)}件エラー）")
                    messagebox.showerror(
                        "エラー",
                        f"{completed} / {total_files} ファイルを処理しました。\n\n以下のファイルでエラーが発生しました:\n{err_msg}"
                    )
                elif is_batch:
                    self.set_status(f"✅  {total_files}ファイル処理しました")
                    if last_output_path and messagebox.askyesno(
                        "完成",
                        f"{total_files}ファイル処理しました！\n\n保存先のフォルダを開きますか？",
                    ):
                        open_folder(last_output_path)
                else:
                    self.set_status("✅  できました！")
                    if last_output_path and messagebox.askyesno(
                        "完成",
                        f"できました！\n\n{os.path.basename(last_output_path)}"
                        "\n\n保存先のフォルダを開きますか？",
                    ):
                        open_folder(last_output_path)

            self.root.after(0, done)

        threading.Thread(target=task, daemon=True).start()

    # ── 設定の保存・読み込み ──────────────────────────────

    def _save_settings(self):
        try:
            data = {
                "use_faces":     self.faces_var.get(),
                "use_scrfd":     self.scrfd_var.get(),
                "score":         self.score_var.get(),
                "face_margin":   self.margin_var.get(),
                "mode":          self.mode_var.get(),
                "manual_color":  self.manual_color_var.get(),
                "manual_design": self.manual_design_var.get(),
                "track":         self.track_var.get(),
            }
            with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _load_settings(self):
        try:
            if not os.path.exists(SETTINGS_PATH):
                return
            with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if "use_faces" in data:
                self.faces_var.set(data["use_faces"])
                self.toggle_face_settings()
            if "score" in data:
                self.score_var.set(data["score"])
                self.on_score_change()
            if "face_margin" in data:
                self.margin_var.set(data["face_margin"])
                self.on_margin_change()
            if "mode" in data:
                self.mode_var.set(data["mode"])
            if "manual_color" in data:
                self.manual_color_var.set(data["manual_color"])
            if "manual_design" in data:
                self.manual_design_var.set(data["manual_design"])
            if "track" in data:
                self.track_var.set(data["track"])
            if "use_scrfd" in data:
                self.scrfd_var.set(data["use_scrfd"])
        except Exception:
            pass
