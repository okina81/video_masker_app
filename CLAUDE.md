# CLAUDE.md

画像・動画かくしツール（顔・個人情報マスキング用デスクトップGUIアプリ）の開発ガイド。

## 概要

Python + tkinter 製のデスクトップGUIアプリ。広報・SNS向けに、画像/動画の顔や
個人情報（手動範囲・文字）を隠す。Webサーバーではなく、ポートも持たない。
エントリポイントは `mask_video_gui.py`（→ `video_masker.gui.main.main`）。

UIテキスト・コメントは日本語。非エンジニアの利用者を想定し、やさしい言葉を使う。

## 実行・開発環境

- 配布起動: `start_windows.bat` / `start_mac.command`（`.venv` 作成 + 依存導入 + 起動）
- 手動起動（venv作成済み）: `.venv\Scripts\pythonw.exe mask_video_gui.py`（Windows）
  - `pythonw` はコンソールを出さずGUIだけ起動する。デバッグ出力を見たいときは `python`。
- 依存導入: `.venv\Scripts\python.exe -m pip install -r requirements.txt`
- システムの素のPythonには一部依存しか入っていない。**必ず `.venv` を使う**。

## 検証方法（このリポジトリでの慣習）

GUIは自動操作しづらいので、変更後は次の順で確認する:

1. `python -m py_compile <変更ファイル>` で構文チェック。
2. コアロジックは小さなスクリプトで実データ/合成データを通して確認
   （例: `process_image` を temp 画像に対して実行し出力を検証）。
3. GUIの構築は「生成→描画→破棄」のスモークテストで実行時エラーを拾う:
   ```
   import tkinter as tk
   from video_masker.gui.app import MaskApp
   r = tk.Tk(); MaskApp(r); r.update_idletasks(); r.update(); r.destroy()
   ```
4. 実機GUIは `pythonw.exe mask_video_gui.py` を起動して確認。

## アーキテクチャ

- `video_masker/processing.py` — 中核オーケストレーション。`process_video` /
  `process_image` / `mask_frame`（プレビュー用）。顔・手動・文字マスクをまとめる。
- `video_masker/masking.py` — マスク描画関数（`apply_mosaic/blur/fill/overlay` ほか）と
  `MASKERS` 辞書、手動色 `MANUAL_COLORS`、`mask_region`。
- `video_masker/model.py` — YuNet モデルのDL/準備、SCRFD 検出器の生成。
- `video_masker/tracking.py` — `TrackedItem`（CSRT + Kalman）。手動範囲の追従。
- `video_masker/motion.py` — `estimate_camera_motion`（疎オプティカルフロー）、
  `SceneChangeDetector`（HSVヒストグラム相関）。
- `video_masker/face_clustering.py` — insightface埋め込み + scipy階層クラスタリングで
  同一人物をまとめ、人物ごとのマスク選択を可能にする。
- `video_masker/media.py` — 形式判定・画像IO（日本語パス対応の imdecode/imencode）・
  オーバーレイ画像読み込み。
- `video_masker/gui/` — `app.py`（メイン画面）、`preview.py`（仕上がりプレビュー）、
  `roi_selector.py`（範囲選択キャンバス）、`face_gallery.py`（顔一覧ダイアログ）。

### 重要なデータ形式

- 手動範囲 box タプル: `(x, y, w, h, start_frame, end_frame)`。
  - 第5要素 `start_frame` 省略時は 0、第6要素 `end_frame` 省略時/None は「最後まで」。
  - 編集系（roi_selector）でジオメトリを書き換えるときは `start`/`end` を保持すること。
- 顔マスクの masker は `build_face_masker(mode, overlay_path)` で1回だけ生成して使い回す
  （overlay は画像読み込みが重いため毎フレーム生成しない）。

### 任意依存（未導入でも他機能は動く＝graceful degradation）

- 人物選択: `insightface`。`face_clustering.embeddings_available()` で判定。
  未導入なら通常の全顔マスク（YuNet）にフォールバック。

## GUIスタイル規約（app.py）

UIを編集するときは既存の設計に合わせる。

- **カラーパレット定数**（app.py 冒頭）: `BG`, `SURFACE`, `SURFACE_ALT`, `PRIMARY`,
  `PRIMARY_LT`, `PRIMARY_DK`, `TEXT`, `TEXT_MUTED`, `BORDER`, `SHADOW_C`,
  `SUCCESS`, `DANGER`。色は直書きせずこれらを使う。
- **カスタムウィジェット**:
  - `_Btn` / `self._btn(parent, text, cmd, font, style)` — `tk.Button` は macOS Aqua で
    bg/fg が効かないため `tk.Label` ベースで自作。`style` は `"primary"` / `"secondary"`。
  - `_ToggleBtn` — セグメントコントロール風トグル（`StringVar` と値で連動）。
  - `_Swatch` — カラースウォッチ。
  - `self._card(parent, number, title)` — 左アクセントバー + 影付きカード。本文Frameを返す。
  - `self._slider_row(...)` — ラベル + バッジ + Scale + ヒントのスライダー行。
- **レイアウト**: 横長3カラム（`_build_ui` 内）。
  - `body` を grid 3列（`uniform="col"`, 各 `weight=1`）。`col_left` / `col_mid` /
    `col_right` の各Frameにカードを `_card` で積む。
  - 左 = ファイル選択(1) + 書き出し(4)、中央 = 顔をかくす(2)、右 = 手動範囲・文字(3)。
  - 全体は `_scroll_shell`（縦スクロール対応）に載る。
- **設定の永続化**: `~/.video_masker_settings.json`。新しい設定項目を足すときは
  `_save_settings` / `_load_settings` の両方と、自動保存 trace 登録リストに追加する。
- フォントは `("Helvetica", size, [style])` を使用。

## UI設計ガイドライン（tkinter）

### テーマ

このアプリは素の `tk.Tk()` + カラー定数による自前スタイリングを採用している。
`sv_ttk` / `ttkthemes` は **使用しない**（カスタムウィジェット `_Btn` など、
bg/fg を直接指定する自作ウィジェットと競合するため）。

テーマ相当の役割はすべて下記カラーパレットと `_Btn` / `_card` 等のヘルパーが担う。

### カラーパレット（app.py 冒頭の定数）

ococペリブランドカラーを基調としたライトテーマ。

```python
# ── coccopelli Light Theme ────────────────────────────────
BG          = "#f0f7fd"   # スカイミスト     ウィンドウ背景
SURFACE     = "#ffffff"   # ホワイト         カード・パネル背景
SURFACE_ALT = "#e8f4fc"   # アイスブルー     サイドバー・交互行
PRIMARY     = "#00a0e9"   # スカイブルー ★   ボタン・強調（ブランドカラー）
PRIMARY_LT  = "#e0f3fd"   # フロスト         ホバー・選択背景
PRIMARY_DK  = "#0082c4"   # ディープスカイ   押下・アクティブ状態
TEXT        = "#1a3a5c"   # ディープネイビー メインテキスト
TEXT_MUTED  = "#5a87aa"   # スチールブルー   サブテキスト・ヒント
BORDER      = "#d0e4f5"   # ペールブルー     枠線・区切り線
SHADOW_C    = "#b8d4ec"   # ソフトシャドウ   カード影色（`_card` 左バー）
SUCCESS     = "#27ae60"   # グリーン         完了・成功
DANGER      = "#e74c3c"   # レッド           エラー・警告
```

**ルール:**
- 色は必ずこれらの定数を使う。HEXを直書きしない。
- `PRIMARY` はボタン・左アクセントバー・プログレスバーなど強調要素に統一して使う。
- `PRIMARY_LT` はホバー背景・選択行・バッジ背景など「薄いアクセント」に使う。
- `PRIMARY_DK` はボタン押下時の `activebackground` に使う。
- テキストは原則 `TEXT`。補足・プレースホルダーは `TEXT_MUTED`。

### フォント

```python
# フォント定数（app.py 冒頭）
FONT_TITLE  = ("Helvetica", 15, "bold")   # セクションタイトル
FONT_BODY   = ("Helvetica", 13)           # 本文・ラベル
FONT_LABEL  = ("Helvetica", 11)           # 小ラベル・ヒント
FONT_MONO   = ("Menlo", 12)               # パス・数値表示
```

- フォントサイズ 9 以下は使用禁止。
- 各ウィジェットでフォントをバラバラに指定しない。定数を参照する。

### ウィジェット・レイアウト

- `tk.Button` は macOS で bg/fg が効かない。ボタンは必ず `_Btn`（`tk.Label` ベース）を使う。
- レイアウトは `grid()` を基本とし、`pack()` との混在は避ける。
- `place(x=, y=)` 絶対座標は禁止（リサイズ崩れの原因）。
- ウィンドウ内側余白: `padx=16, pady=12`。ウィジェット間: `padx=8, pady=6`。
- リサイズ対応のため `columnconfigure(n, weight=1)` を必ず設定する。

### その他

- ボタンラベルは動詞で始める（「保存」「実行」「キャンセル」）。
- エラー・警告は `tk.messagebox` またはウィンドウ最下部のステータスバーで表示する。
- ステータスバーをウィンドウ最下部に常設し、処理状態を表示する。

## Git

- リモート: https://github.com/okina81/video_masker_app （branch=master）。
- コミット/プッシュはユーザーが明示したときのみ。フェーズ単位で意味のある粒度にする。
- `IMG_4388.PNG`（キャラ画像「ococちゃん」）はユーザー希望で追跡しない。
  `face_detection_yunet_2023mar.onnx` は `.gitignore` の例外指定でコミット対象。