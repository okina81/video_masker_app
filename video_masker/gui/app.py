import json
import os
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

try:
    import sv_ttk
except Exception:  # sv_ttk が無くても素のテーマで動く
    sv_ttk = None

from video_masker.gui.face_gallery import FaceGalleryDialog
from video_masker.gui.preview import FinishPreview
from video_masker.gui.roi_selector import RoiSelector
from video_masker.masking import MANUAL_COLORS, make_manual_masker
from video_masker.media import get_media_type
from video_masker.model import ModelPreparationError
from video_masker.processing import process_image, process_video
from video_masker.system import open_folder


# ── カラーパレット（ococブランドカラー準拠・一元管理） ─────
COLORS = {
    "bg":         "#0d1b3e",    # ネイビー（ウィンドウ背景）
    "surface":    "#162248",    # ミッドナイト（カード・パネル）
    "surface2":   "#0d1b3e",    # サイドバー・ナビ
    "accent":     "#00a0e9",    # スカイブルー（ボタン・強調）★ブランドカラー
    "accent_dim": "#00a0e920",  # アクセント薄め（※アルファ付き。tk色には渡さない）
    "text":       "#c9d8f0",    # アイスブルー（メインテキスト）
    "muted":      "#7eb8e8",    # ライトブルー（サブテキスト）
    "disabled":   "#4a6a8a",    # スレート（無効・ヒント）
    "border":     "#1e3266",    # ロイヤルブルー（枠線）
    "danger":     "#f28b82",    # コーラル（エラー・警告）
    "success":    "#81c995",    # グリーン（成功・完了）
}

# ── フォント（一元管理） ───────────────────────────────────
FONTS = {
    "title": ("Helvetica Neue", 15, "bold"),
    "body":  ("Helvetica Neue", 13),
    "label": ("Helvetica Neue", 11),
    "mono":  ("Menlo", 12),
}

SETTINGS_PATH = os.path.join(os.path.expanduser("~"), ".video_masker_settings.json")


def _bgr_to_hex(bgr):
    b, g, r = bgr
    return f"#{r:02x}{g:02x}{b:02x}"


# ── レイアウト補助 ─────────────────────────────────────────

class _Stack:
    """1カラムに縦積みするための grid ヘルパー（pack を使わない）。"""

    def __init__(self, frame):
        self.f = frame
        self.r = 0
        frame.columnconfigure(0, weight=1)

    def add(self, widget, pady=(0, 8), sticky="ew", padx=0):
        widget.grid(row=self.r, column=0, sticky=sticky, padx=padx, pady=pady)
        self.r += 1
        return widget


# ── カラースウォッチ（任意色の表示は Canvas が必要） ────────

class _Swatch(tk.Canvas):
    SIZE = 28

    def __init__(self, parent, color_name, color_bgr, var):
        super().__init__(parent, width=self.SIZE, height=self.SIZE,
                         highlightthickness=0, bg=COLORS["surface"], cursor="hand2")
        self.color_name = color_name
        self.hex_color = _bgr_to_hex(color_bgr)
        self.var = var
        self._draw()
        self.bind("<ButtonRelease-1>", lambda _e: self.var.set(self.color_name))
        var.trace_add("write", lambda *_: self._draw())

    def _draw(self):
        self.delete("all")
        s = self.SIZE
        selected = self.var.get() == self.color_name
        if selected:
            self.create_rectangle(1, 1, s - 1, s - 1,
                                  outline=COLORS["accent"], width=2)
        self.create_rectangle(5, 5, s - 5, s - 5, fill=self.hex_color,
                              outline=COLORS["border"])


# ── メインアプリ ──────────────────────────────────────────

class MaskApp:
    def __init__(self, root):
        self.root = root
        self.input_path   = None
        self._input_paths: list = []
        self.media_type   = None
        self.manual_boxes = []
        self._manual_boxes_per_file: dict = {}
        self._person_clusters = None  # 人物選択の結果 [{centroid, enabled}]
        self._stop_event  = threading.Event()
        self._proc_start  = 0.0

        root.title("画像・動画かくしツール")
        root.geometry("1280x820")
        root.minsize(1080, 700)

        if sv_ttk is not None:
            sv_ttk.set_theme("dark")
        self._init_styles()
        root.configure(bg=COLORS["bg"])

        try:
            root.tk.call("package", "require", "tkdnd")
            root.drop_target_register("DND_Files")
            root.dnd_bind("<<Drop>>", self._on_drop)
        except Exception:
            pass

        self._build_ui()

    # ── スタイル設定 ──────────────────────────────────────

    def _init_styles(self):
        s = ttk.Style()
        # ベース
        s.configure(".", background=COLORS["bg"], foreground=COLORS["text"],
                    font=FONTS["body"], borderwidth=0)
        s.configure("TButton", font=FONTS["body"])
        s.configure("Switch.TCheckbutton", font=FONTS["body"])
        s.configure("Toolbutton", font=FONTS["label"])

        # フレーム
        s.configure("Bg.TFrame", background=COLORS["bg"])
        s.configure("Card.TFrame", background=COLORS["surface"])
        s.configure("Sub.TFrame", background=COLORS["surface"])
        s.configure("Header.TFrame", background=COLORS["accent"])
        s.configure("Status.TFrame", background=COLORS["surface"])

        # ラベル（背景は親に合わせて指定）
        s.configure("Header.TLabel", background=COLORS["accent"],
                    foreground="#ffffff", font=FONTS["title"])
        s.configure("HeaderSub.TLabel", background=COLORS["accent"],
                    foreground="#ece9ff", font=FONTS["label"])
        s.configure("Badge.TLabel", background=COLORS["accent"],
                    foreground="#ffffff", font=FONTS["label"])
        s.configure("CardTitle.TLabel", background=COLORS["surface"],
                    foreground=COLORS["text"], font=FONTS["title"])
        s.configure("Section.TLabel", background=COLORS["surface"],
                    foreground=COLORS["text"], font=("Helvetica Neue", 13, "bold"))
        s.configure("Body.TLabel", background=COLORS["surface"],
                    foreground=COLORS["text"], font=FONTS["body"])
        s.configure("Muted.TLabel", background=COLORS["surface"],
                    foreground=COLORS["muted"], font=FONTS["label"])
        s.configure("Hint.TLabel", background=COLORS["surface"],
                    foreground=COLORS["disabled"], font=FONTS["label"])
        s.configure("Field.TLabel", background=COLORS["surface"],
                    foreground=COLORS["muted"], font=FONTS["label"])
        s.configure("Status.TLabel", background=COLORS["surface"],
                    foreground=COLORS["text"], font=FONTS["body"])
        s.configure("Accent.TLabel", background=COLORS["surface"],
                    foreground=COLORS["accent"], font=FONTS["label"])
        s.configure("Drop.TLabel", background=COLORS["surface"],
                    foreground=COLORS["accent"], font=FONTS["title"])

    # ── UI構築 ────────────────────────────────────────────

    def _build_ui(self):
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=1)

        # ── ヘッダー（全幅）
        hdr = ttk.Frame(self.root, style="Header.TFrame", padding=(28, 20))
        hdr.grid(row=0, column=0, sticky="ew")
        ttk.Label(hdr, text="🔒  画像・動画かくしツール",
                  style="Header.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(hdr, text="顔・手動範囲・文字をまとめてかくす",
                  style="HeaderSub.TLabel").grid(row=1, column=0, sticky="w", pady=(4, 0))

        # ── 本体（横長3カラム）
        body = ttk.Frame(self.root, style="Bg.TFrame", padding=(18, 16))
        body.grid(row=1, column=0, sticky="nsew")
        for i in range(3):
            body.columnconfigure(i, weight=1, uniform="col")
        body.rowconfigure(0, weight=1)
        col_left  = ttk.Frame(body, style="Bg.TFrame")
        col_mid   = ttk.Frame(body, style="Bg.TFrame")
        col_right = ttk.Frame(body, style="Bg.TFrame")
        col_left.grid(row=0, column=0, sticky="nsew", padx=(0, 9))
        col_mid.grid(row=0, column=1, sticky="nsew", padx=9)
        col_right.grid(row=0, column=2, sticky="nsew", padx=(9, 0))
        for c in (col_left, col_mid, col_right):
            c.columnconfigure(0, weight=1)

        self._build_file_card(col_left, row=0)
        self._build_face_card(col_mid)
        self._build_manual_text_card(col_right)
        self._build_output_card(col_left, row=1)

        # ── ステータスバー（最下部に常設）
        self._build_statusbar()

        # 設定の自動保存
        for var in (self.faces_var, self.score_var, self.margin_var,
                    self.mode_var, self.manual_color_var, self.manual_design_var,
                    self.track_var, self._overlay_path_var, self.text_var):
            var.trace_add("write", lambda *_: self._save_settings())

        self._load_settings()

    # ── 各カード ──────────────────────────────────────────

    def _build_file_card(self, parent, row):
        body = self._card(parent, "1", "ファイルをえらぶ", row=row)
        st = _Stack(body)

        drop = ttk.Frame(body, style="Card.TFrame", padding=(16, 22))
        drop.columnconfigure(0, weight=1)
        dlbl = ttk.Label(drop, text="📂  クリックしてファイルをえらぶ",
                         style="Drop.TLabel", anchor="center")
        dlbl.grid(row=0, column=0)
        for w in (drop, dlbl):
            w.bind("<ButtonRelease-1>", lambda _e: self.pick_inputs())
            w.configure(cursor="hand2")
        st.add(drop)

        self.file_label = ttk.Label(body, text="まだ選んでいません", style="Muted.TLabel")
        st.add(self.file_label, pady=(12, 0))

    def _build_face_card(self, parent):
        body = self._card(parent, "2", "顔をかくす")
        st = _Stack(body)

        self.faces_var = tk.BooleanVar(value=True)
        st.add(ttk.Checkbutton(body, text="顔を自動でかくす",
                               style="Switch.TCheckbutton", variable=self.faces_var,
                               command=self.toggle_face_settings))

        # 折りたたみ対象
        self.face_settings_frame = ttk.Frame(body, style="Sub.TFrame")
        st.add(self.face_settings_frame)
        fst = _Stack(self.face_settings_frame)

        self._slider_row(fst, "顔認識のレベル",
                         var_attr="score_var", label_attr="score_label",
                         from_=0.2, to=0.9, default=0.5, cb=self.on_score_change,
                         hint="低いほど検出されやすく、高いほど確実な顔だけを検出します。")
        self._slider_row(fst, "顔の隠す範囲",
                         var_attr="margin_var", label_attr="margin_label",
                         from_=0.0, to=0.5, default=0.0, cb=self.on_margin_change,
                         hint="広くすると顔の周囲をより大きく隠します。ずれが気になるときに調整します。")

        self.scrfd_var = tk.BooleanVar(value=False)
        fst.add(ttk.Checkbutton(
            self.face_settings_frame,
            text="検出漏れ時に SCRFD で補う（初回のみ約16MB）",
            style="Switch.TCheckbutton", variable=self.scrfd_var))

        # 人物ごとのマスク選択
        prow = ttk.Frame(self.face_settings_frame, style="Sub.TFrame")
        prow.columnconfigure(1, weight=1)
        ttk.Button(prow, text="👥  顔を一覧で確認する",
                   command=self.open_face_gallery).grid(row=0, column=0, sticky="w")
        self._person_label = ttk.Label(prow, text="全員をかくします", style="Muted.TLabel")
        self._person_label.grid(row=0, column=1, sticky="w", padx=(10, 0))
        fst.add(prow)

        # かくし方
        fst.add(ttk.Label(self.face_settings_frame, text="かくし方", style="Field.TLabel"),
                pady=(4, 2))
        self.mode_var = tk.StringVar(value="mosaic")
        seg = self._segment(self.face_settings_frame, self.mode_var,
                            [("モザイク", "mosaic"), ("ぼかし", "blur"),
                             ("黒塗り", "fill"), ("キャラ", "overlay")])
        fst.add(seg, sticky="w")

        # キャラ画像オーバーレイ選択（mode=overlay のときだけ表示）
        default_overlay = os.path.join(os.getcwd(), "IMG_4388.PNG")
        self._overlay_path_var = tk.StringVar(
            value=default_overlay if os.path.exists(default_overlay) else "")
        self._overlay_row = ttk.Frame(self.face_settings_frame, style="Sub.TFrame")
        self._overlay_row.columnconfigure(1, weight=1)
        ttk.Button(self._overlay_row, text="🖼  キャラ画像をえらぶ",
                   command=self._pick_overlay_image).grid(row=0, column=0, sticky="w")
        self._overlay_label = ttk.Label(self._overlay_row, text="", style="Muted.TLabel")
        self._overlay_label.grid(row=0, column=1, sticky="w", padx=(10, 0))
        fst.add(self._overlay_row, pady=(6, 0))
        self._update_overlay_label()
        self.mode_var.trace_add("write", lambda *_: self._toggle_overlay_row())
        self._toggle_overlay_row()

    def _build_manual_text_card(self, parent):
        body = self._card(parent, "3", "手動範囲・文字をかくす")
        st = _Stack(body)

        st.add(ttk.Label(body, text="手動で範囲を指定する", style="Section.TLabel"))

        # 色
        crow = ttk.Frame(body, style="Sub.TFrame")
        ttk.Label(crow, text="色", style="Field.TLabel").grid(row=0, column=0, padx=(0, 8))
        self.manual_color_var = tk.StringVar(value="黒")
        for i, (name, bgr) in enumerate(MANUAL_COLORS.items()):
            _Swatch(crow, name, bgr, self.manual_color_var).grid(
                row=0, column=i + 1, padx=3)
        st.add(crow, sticky="w", pady=(10, 4))

        # デザイン
        drow = ttk.Frame(body, style="Sub.TFrame")
        ttk.Label(drow, text="デザイン", style="Field.TLabel").grid(
            row=0, column=0, padx=(0, 8))
        self.manual_design_var = tk.StringVar(value="ベタ塗り")
        seg2 = self._segment(drow, self.manual_design_var,
                             [(n, n) for n in ("ベタ塗り", "斜線", "チェック")])
        seg2.grid(row=0, column=1, sticky="w")
        st.add(drow, sticky="w", pady=(0, 10))

        # 範囲選択
        srow = ttk.Frame(body, style="Sub.TFrame")
        srow.columnconfigure(1, weight=1)
        ttk.Button(srow, text="✏️  塗る範囲をえらぶ",
                   command=self.open_selector).grid(row=0, column=0, sticky="w")
        self.manual_label = ttk.Label(srow, text="範囲: なし", style="Muted.TLabel")
        self.manual_label.grid(row=0, column=1, sticky="w", padx=(12, 0))
        st.add(srow, pady=(0, 8))

        self.track_var = tk.BooleanVar(value=False)
        self._track_cb = ttk.Checkbutton(
            body, text="塗った範囲を動くものに追従させる",
            style="Switch.TCheckbutton", variable=self.track_var)
        self._track_frame = self._track_cb  # 互換参照
        st.add(self._track_cb, sticky="w")

        st.add(ttk.Separator(body), pady=16)

        st.add(ttk.Label(body, text="文字（個人情報）を自動でかくす", style="Section.TLabel"))
        self.text_var = tk.BooleanVar(value=False)
        st.add(ttk.Checkbutton(body, text="画面の文字を検出してかくす",
                               style="Switch.TCheckbutton", variable=self.text_var),
               sticky="w", pady=(6, 2))
        st.add(ttk.Label(body,
                         text="※ Tesseract（文字認識）が必要です。未導入のときは無視されます。",
                         style="Hint.TLabel", wraplength=360), sticky="w")

    def _build_output_card(self, parent, row):
        body = self._card(parent, "4", "確認して書き出す", row=row)
        st = _Stack(body)

        # 保存先フォルダ
        frow = ttk.Frame(body, style="Sub.TFrame")
        frow.columnconfigure(1, weight=1)
        ttk.Label(frow, text="保存先", style="Field.TLabel").grid(row=0, column=0, padx=(0, 8))
        self._out_folder_var = tk.StringVar(value="")
        ttk.Entry(frow, textvariable=self._out_folder_var).grid(
            row=0, column=1, sticky="ew", padx=(0, 8))
        ttk.Button(frow, text="変更", command=self._pick_out_folder).grid(row=0, column=2)
        st.add(frow)
        st.add(ttk.Label(body, text="空欄のときは入力ファイルと同じフォルダに保存します",
                         style="Hint.TLabel"), pady=(0, 8))

        # ファイル名
        nrow = ttk.Frame(body, style="Sub.TFrame")
        nrow.columnconfigure(1, weight=1)
        ttk.Label(nrow, text="ファイル名", style="Field.TLabel").grid(
            row=0, column=0, padx=(0, 8))
        self._out_name_var = tk.StringVar(value="")
        self._out_name_entry = ttk.Entry(nrow, textvariable=self._out_name_var)
        self._out_name_entry.grid(row=0, column=1, sticky="ew")
        st.add(nrow)
        self._out_name_hint = ttk.Label(
            body, text="空欄のときは「元ファイル名_かくし済み」になります（拡張子は自動）",
            style="Hint.TLabel", wraplength=360)
        st.add(self._out_name_hint, pady=(0, 12))

        # ボタン
        brow = ttk.Frame(body, style="Sub.TFrame")
        brow.columnconfigure(0, weight=1)
        brow.columnconfigure(1, weight=1)
        self.preview_btn = ttk.Button(brow, text="👀  仕上がりを確認する",
                                      command=self.open_finish_preview)
        self.preview_btn.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self.start_btn = ttk.Button(brow, text="🚀  書き出す",
                                    style="Accent.TButton", command=self.on_run)
        self.start_btn.grid(row=0, column=1, sticky="ew", padx=(6, 0))
        st.add(brow)

    def _build_statusbar(self):
        bar = ttk.Frame(self.root, style="Status.TFrame", padding=(16, 8))
        bar.grid(row=2, column=0, sticky="ew")
        bar.columnconfigure(0, weight=1)
        self.status = ttk.Label(bar, text="準備できています", style="Status.TLabel")
        self.status.grid(row=0, column=0, sticky="w")
        self.progress = ttk.Progressbar(bar, maximum=100, length=240)
        self.progress.grid(row=0, column=1, sticky="e", padx=(12, 0))

    # ── ウィジェットヘルパー ──────────────────────────────

    def _card(self, parent, number, title, row=0):
        """番号バッジ付きカード。中身を積む body フレームを返す。"""
        card = ttk.Frame(parent, style="Card.TFrame", padding=(18, 16))
        card.grid(row=row, column=0, sticky="new", pady=(0, 14))
        card.columnconfigure(0, weight=1)

        head = ttk.Frame(card, style="Card.TFrame")
        head.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        ttk.Label(head, text=f"  {number}  ", style="Badge.TLabel").grid(row=0, column=0)
        ttk.Label(head, text=title, style="CardTitle.TLabel").grid(
            row=0, column=1, sticky="w", padx=(10, 0))

        body = ttk.Frame(card, style="Card.TFrame")
        body.grid(row=1, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)
        return body

    def _segment(self, parent, var, options):
        """セグメントコントロール（ttk.Radiobutton の Toolbutton）。"""
        seg = ttk.Frame(parent, style="Sub.TFrame")
        for i, (lbl, val) in enumerate(options):
            ttk.Radiobutton(seg, text=lbl, value=val, variable=var,
                            style="Toolbutton").grid(
                row=0, column=i, padx=(0 if i == 0 else 4, 0))
        return seg

    def _slider_row(self, stack, label, var_attr, label_attr,
                    from_, to, default, cb, hint):
        panel = ttk.Frame(stack.f, style="Card.TFrame", padding=(12, 10))
        panel.columnconfigure(0, weight=1)

        ttk.Label(panel, text=label, style="Body.TLabel").grid(
            row=0, column=0, sticky="w")
        badge = ttk.Label(panel, text="標準" if default == 0.0 else "ふつう",
                          style="Accent.TLabel")
        badge.grid(row=0, column=1, sticky="e")
        setattr(self, label_attr, badge)

        var = tk.DoubleVar(value=default)
        setattr(self, var_attr, var)
        ttk.Scale(panel, from_=from_, to=to, variable=var, orient="horizontal",
                  command=cb).grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 4))

        ttk.Label(panel, text=hint, style="Hint.TLabel",
                  wraplength=360, justify="left").grid(
            row=2, column=0, columnspan=2, sticky="w")
        stack.add(panel)

    # ── ロジック ─────────────────────────────────────────

    def _on_drop(self, event):
        path = event.data.strip("{}")
        if path and os.path.isfile(path):
            self._load_files([path])

    def toggle_face_settings(self):
        if self.faces_var.get():
            self.face_settings_frame.grid()
        else:
            self.face_settings_frame.grid_remove()

    def _load_files(self, paths):
        """paths: list[str] — 1本以上のファイルパス"""
        valid = [p for p in paths if get_media_type(p) is not None]
        if not valid:
            messagebox.showinfo("おしらせ", "対応している画像または動画を選んでください。")
            return

        self._input_paths = valid
        first = valid[0]
        self.input_path = first
        self.media_type = get_media_type(first)
        self.manual_boxes = []
        self._manual_boxes_per_file = {}
        self.manual_label.configure(text="範囲: なし", foreground=COLORS["muted"])
        self._person_clusters = None
        self._update_person_label()

        if len(valid) == 1:
            icon = "🖼" if self.media_type == "image" else "🎬"
            self.file_label.configure(
                text=f"✓  {icon} {os.path.basename(first)}", foreground=COLORS["success"])
        else:
            self.file_label.configure(
                text=f"✓  {len(valid)}ファイル選択中", foreground=COLORS["success"])

        self.set_status("設定を確認して、必要ならプレビューしてください。")

        if len(valid) > 1:
            self._out_name_entry.configure(state="disabled")
            self._out_name_hint.configure(
                text="複数ファイル処理時は各ファイル名が「元ファイル名_かくし済み」になります")
        else:
            self._out_name_entry.configure(state="normal")
            self._out_name_hint.configure(
                text="空欄のときは「元ファイル名_かくし済み」になります（拡張子は自動）")

        if self.media_type == "image":
            self.track_var.set(False)
            self._track_cb.configure(state="disabled")
        else:
            self._track_cb.configure(state="normal")

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

    def pick_input(self):
        self.pick_inputs()

    def _pick_out_folder(self):
        folder = filedialog.askdirectory(title="保存先フォルダをえらんでください")
        if folder:
            self._out_folder_var.set(folder)

    def _pick_overlay_image(self):
        path = filedialog.askopenfilename(
            title="キャラ画像（透過PNG推奨）をえらんでください",
            filetypes=[("画像ファイル", "*.png *.webp *.jpg *.jpeg *.bmp"),
                       ("すべてのファイル", "*.*")],
        )
        if path:
            self._overlay_path_var.set(path)
            self._update_overlay_label()

    def _update_overlay_label(self):
        path = self._overlay_path_var.get()
        if path and os.path.exists(path):
            self._overlay_label.configure(text=os.path.basename(path),
                                          foreground=COLORS["accent"])
        else:
            self._overlay_label.configure(text="画像が未選択です",
                                          foreground=COLORS["muted"])

    def _toggle_overlay_row(self):
        if self.mode_var.get() == "overlay":
            self._overlay_row.grid()
        else:
            self._overlay_row.grid_remove()

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
                self._manual_boxes_per_file = selector.result
                files_with_boxes = sum(
                    1 for boxes in self._manual_boxes_per_file.values() if boxes)
                if files_with_boxes:
                    self.manual_label.configure(
                        text=f"範囲: {files_with_boxes}ファイルで設定済み",
                        foreground=COLORS["accent"])
                else:
                    self.manual_label.configure(text="範囲: なし",
                                                foreground=COLORS["muted"])
            else:
                self.manual_boxes = selector.result
                count = len(self.manual_boxes)
                self.manual_label.configure(
                    text=f"範囲: {count}か所" if count else "範囲: なし",
                    foreground=COLORS["accent"] if count else COLORS["muted"])

    def open_face_gallery(self):
        if not self.input_path or not os.path.exists(self.input_path):
            messagebox.showinfo("おしらせ",
                                "さきに『ファイルをえらぶ』からファイルを選んでください。")
            return
        dialog = FaceGalleryDialog(self.root, self.input_path,
                                   score=float(self.score_var.get()))
        self.root.wait_window(dialog)
        if dialog.result is not None:
            self._person_clusters = dialog.result
            self._update_person_label()

    def _update_person_label(self):
        clusters = self._person_clusters
        if not clusters:
            self._person_label.configure(text="全員をかくします",
                                         foreground=COLORS["muted"])
            return
        off = sum(1 for c in clusters if not c["enabled"])
        if off:
            self._person_label.configure(
                text=f"{len(clusters)}人中 {off}人はかくしません",
                foreground=COLORS["accent"])
        else:
            self._person_label.configure(text=f"{len(clusters)}人全員をかくします",
                                         foreground=COLORS["muted"])

    def set_status(self, message):
        self.status.configure(text=message)

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
                self.set_status(f"かくしています…  {pct}%  (あと約 {eta})")
            else:
                self.set_status(f"かくしています…  {pct}%")
        else:
            self.set_status(f"かくしています…  {pct}%")
        self.root.update_idletasks()

    def on_score_change(self, _=None):
        s = self.score_var.get()
        self.score_label.configure(
            text="高感度" if s < 0.4 else "ふつう" if s < 0.7 else "厳しめ")

    def on_margin_change(self, _=None):
        m = self.margin_var.get()
        self.margin_label.configure(
            text="標準" if m < 0.15 else "やや広め" if m < 0.30 else "広め")

    def build_run_params(self, input_path=None):
        manual_masker_fn = make_manual_masker(
            self.manual_color_var.get(), self.manual_design_var.get())
        resolved_path = input_path or self.input_path
        if input_path and input_path in self._manual_boxes_per_file:
            boxes = list(self._manual_boxes_per_file[input_path])
        else:
            boxes = list(self.manual_boxes)
        person_centroids = person_enabled = None
        if self._person_clusters:
            person_centroids = [c["centroid"] for c in self._person_clusters]
            person_enabled = [bool(c["enabled"]) for c in self._person_clusters]
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
            overlay_path=self._overlay_path_var.get() or None,
            mask_text=self.text_var.get(),
            person_centroids=person_centroids,
            person_enabled=person_enabled,
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
        output_ext = ".mp4" if media_type == "video" else os.path.splitext(input_path)[1]
        folder = self._out_folder_var.get().strip() or os.path.dirname(input_path)
        name = self._out_name_var.get().strip() if use_custom_name else ""
        if not name:
            base = os.path.splitext(os.path.basename(input_path))[0]
            name = base + "_かくし済み"
        return os.path.join(folder, name + output_ext)

    def on_run(self):
        if not self._input_paths and not self.input_path:
            messagebox.showinfo("おしらせ", "さきにファイルを選んでください。")
            return

        paths = self._input_paths if self._input_paths else [self.input_path]
        paths = [p for p in paths if os.path.exists(p)]
        if not paths:
            messagebox.showinfo("おしらせ", "選択したファイルが見つかりません。")
            return

        is_batch = len(paths) > 1
        use_custom_name = not is_batch

        self._stop_event.clear()
        self._proc_start = time.time()

        self.start_btn.configure(text="⏹  キャンセル", style="TButton",
                                 command=self._cancel)
        self.preview_btn.configure(state="disabled")
        self.progress["value"] = 0
        self.set_status("準備しています…")

        stop_event = self._stop_event

        def _restore_start_btn():
            self.start_btn.configure(text="🚀  書き出す", style="Accent.TButton",
                                     command=self.on_run)
            self.preview_btn.configure(state="normal")

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
                        f"{completed} / {total_files} ファイルを処理しました。\n\n以下のファイルでエラーが発生しました:\n{err_msg}")
                elif is_batch:
                    self.set_status(f"✅  {total_files}ファイル処理しました")
                    if last_output_path and messagebox.askyesno(
                        "完成", f"{total_files}ファイル処理しました！\n\n保存先のフォルダを開きますか？"):
                        open_folder(last_output_path)
                else:
                    self.set_status("✅  できました！")
                    if last_output_path and messagebox.askyesno(
                        "完成",
                        f"できました！\n\n{os.path.basename(last_output_path)}"
                        "\n\n保存先のフォルダを開きますか？"):
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
                "overlay_path":  self._overlay_path_var.get(),
                "mask_text":     self.text_var.get(),
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
            if data.get("overlay_path"):
                self._overlay_path_var.set(data["overlay_path"])
                self._update_overlay_label()
            if "mask_text" in data:
                self.text_var.set(data["mask_text"])
            self._toggle_overlay_row()
        except Exception:
            pass
