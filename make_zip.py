import os
import zipfile


APP_NAME = "video_masker_app"
ZIP_NAME = APP_NAME + ".zip"

EXCLUDED_DIRS = {".venv", "__pycache__", ".git"}
EXCLUDED_SUFFIXES = {".pyc", ".pyo"}
EXCLUDED_FILES = {ZIP_NAME}
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


def should_include(path):
    parts = set(path.split(os.sep))
    if parts & EXCLUDED_DIRS:
        return False
    name = os.path.basename(path)
    if name in EXCLUDED_FILES:
        return False
    ext = os.path.splitext(name)[1].lower()
    if ext in EXCLUDED_SUFFIXES:
        return False
    if ext in EXCLUDED_MEDIA_SUFFIXES:
        return False
    return True


def main():
    with zipfile.ZipFile(ZIP_NAME, "w", zipfile.ZIP_DEFLATED) as archive:
        for root, dirs, files in os.walk("."):
            dirs[:] = [d for d in dirs if should_include(os.path.join(root, d))]
            for filename in files:
                path = os.path.join(root, filename)
                if not should_include(path):
                    continue
                archive.write(path, os.path.join(APP_NAME, path.lstrip("./")))
    print("created:", ZIP_NAME)


if __name__ == "__main__":
    main()
