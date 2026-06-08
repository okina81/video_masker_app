import os
import zipfile


APP_NAME = "video_masker_app"
ZIP_NAME = APP_NAME + ".zip"

# 配布zipから除外するディレクトリ
EXCLUDED_DIRS = {".venv", "__pycache__", ".git", ".claude"}
# 除外する拡張子（ビルド生成物・ログ）
EXCLUDED_SUFFIXES = {".pyc", ".pyo", ".log"}
# 除外する個別ファイル（開発専用・利用者には不要）
EXCLUDED_FILES = {
    ZIP_NAME,
    "make_zip.py",
    "CLAUDE.md",
    ".gitignore",
    "gitignore",
    "ui_snapshot.png",
    ".video_masker_settings.json",
}
# メディア（利用者の入出力ファイル）は基本的に同梱しない
EXCLUDED_MEDIA_SUFFIXES = {
    ".mp4",
    ".mov",
    ".m4v",
    ".avi",
    ".jpg",
    ".jpeg",
    ".png",
    ".bmp",
    ".webp",
    ".tif",
    ".tiff",
}
# メディア除外より優先して必ず同梱するアプリ同梱アセット
#   - IMG_4388.PNG: ココちゃんオーバーレイの既定画像
#   - .onnx 顔検出モデルは EXCLUDED_MEDIA に含まれないため自動で同梱される
ALWAYS_INCLUDE_FILES = {"IMG_4388.PNG"}


def should_include(path):
    parts = set(path.split(os.sep))
    if parts & EXCLUDED_DIRS:
        return False
    name = os.path.basename(path)
    # 必須アセットは他の除外ルールより優先して同梱
    if name in ALWAYS_INCLUDE_FILES:
        return True
    if name in EXCLUDED_FILES:
        return False
    ext = os.path.splitext(name)[1].lower()
    if ext in EXCLUDED_SUFFIXES:
        return False
    if ext in EXCLUDED_MEDIA_SUFFIXES:
        return False
    return True


def main():
    count = 0
    with zipfile.ZipFile(ZIP_NAME, "w", zipfile.ZIP_DEFLATED) as archive:
        for root, dirs, files in os.walk("."):
            dirs[:] = [d for d in dirs if should_include(os.path.join(root, d))]
            for filename in files:
                path = os.path.join(root, filename)
                if not should_include(path):
                    continue
                arcname = os.path.join(APP_NAME, os.path.relpath(path, "."))
                archive.write(path, arcname)
                count += 1
    print(f"created: {ZIP_NAME}  ({count} files)")


if __name__ == "__main__":
    main()
