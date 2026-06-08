import os
import subprocess
import sys


def open_folder(path):
    folder = os.path.dirname(os.path.abspath(path))
    if sys.platform == "darwin":
        subprocess.run(["open", folder])
    elif sys.platform.startswith("win"):
        os.startfile(folder)
    else:
        subprocess.run(["xdg-open", folder])

