import tkinter as tk

from video_masker.gui.app import MaskApp


def main():
    root = tk.Tk()
    MaskApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
