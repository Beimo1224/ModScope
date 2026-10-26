"""Native folder selection for the local web app.

The browser's directory picker works everywhere.  This helper adds the
convenience of scanning a path without copying potentially hundreds of MB of
JARs through an upload.  It is optional and fails gracefully.
"""

from __future__ import annotations

import sys
from pathlib import Path


def choose_folder() -> str:
    if sys.platform == "win32":
        return _choose_windows()
    return _choose_tk()


def _choose_windows() -> str:
    import subprocess

    script = r"""
Add-Type -AssemblyName System.Windows.Forms
[System.Windows.Forms.Application]::EnableVisualStyles()
$dialog = New-Object System.Windows.Forms.FolderBrowserDialog
$dialog.Description = '选择 Minecraft 的 mods 文件夹'
$dialog.ShowNewFolderButton = $false
$result = $dialog.ShowDialog()
if ($result -eq [System.Windows.Forms.DialogResult]::OK) {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    Write-Output $dialog.SelectedPath
}
"""
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-STA", "-Command", script],
        capture_output=True,
        timeout=300,
    )
    if completed.returncode != 0:
        raise RuntimeError("系统文件夹选择器未能打开")
    for encoding in ("utf-8-sig", "utf-8", "gbk"):
        try:
            text = completed.stdout.decode(encoding).strip()
            break
        except UnicodeDecodeError:
            text = ""
    if not text:
        return ""
    return str(Path(text))


def _choose_tk() -> str:
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    try:
        root.attributes("-topmost", True)
        root.update()
        selected = filedialog.askdirectory(title="选择 Minecraft 的 mods 文件夹", mustexist=True)
    finally:
        root.destroy()
    return selected or ""


__all__ = ["choose_folder"]
