"""在本機開啟 Windows 原生的選擇檔案／資料夾對話框。

瀏覽器拿不到檔案的完整路徑（只能上傳複本），但服務本來就跑在使用者電腦上，
所以由服務端開對話框、回傳路徑；PDF 仍留在原位，不會被複製。
對話框放在獨立子程序執行，避免 Tk 與網頁服務的執行緒互相干擾。
"""
import os
import subprocess
import sys

_DIALOG_SCRIPT = r'''
import sys, tkinter as tk
from tkinter import filedialog
kind, initial = sys.argv[1], (sys.argv[2] if len(sys.argv) > 2 else "") or None
root = tk.Tk()
root.withdraw()
root.attributes("-topmost", True)  # 否則對話框常被瀏覽器蓋住
root.update()
if kind == "folder":
    result = filedialog.askdirectory(parent=root, title="選擇放 PDF 的資料夾", initialdir=initial, mustexist=True)
    paths = [result] if result else []
else:
    paths = list(filedialog.askopenfilenames(parent=root, title="選擇 PDF（可多選）", initialdir=initial,
                                             filetypes=[("PDF 檔案", "*.pdf"), ("所有檔案", "*.*")]))
root.destroy()
sys.stdout.write("\n".join(paths))
'''

TIMEOUT = 600  # 使用者遲遲不選時放棄，避免卡住請求


def pick(kind: str, initial_dir: str = "") -> list:
    """kind 為 "pdf"（可多選）或 "folder"；取消時回傳空清單。"""
    if initial_dir and not os.path.isdir(initial_dir):
        initial_dir = ""
    result = subprocess.run(
        [sys.executable, "-c", _DIALOG_SCRIPT, "folder" if kind == "folder" else "pdf", initial_dir],
        capture_output=True, text=True, encoding="utf-8", timeout=TIMEOUT,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip().splitlines()[-1] if result.stderr.strip() else "無法開啟對話框")
    # tkinter 回傳的是正斜線路徑，轉成 Windows 慣用的格式
    return [os.path.normpath(p) for p in result.stdout.splitlines() if p.strip()]
