"""Small native Windows actions. No user-supplied shell commands are accepted."""
import json
import os
from pathlib import Path
import subprocess


def select_path(kind="file", purpose="dataset"):
    if os.name != "nt":
        raise ValueError("Native selection is available in the Windows app. Enter a local path instead.")
    if kind not in ("file", "folder") or purpose not in ("dataset", "script", "export", "image", "import", "workspace"):
        raise ValueError("Unsupported path-selection request.")
    # The PowerShell text is constant; user-provided paths never become executable code.
    if kind == "folder":
        script = "Add-Type -AssemblyName System.Windows.Forms; $d=New-Object System.Windows.Forms.FolderBrowserDialog; $d.Description='Choose a folder for MultiGPU'; if($d.ShowDialog() -eq 'OK'){ConvertTo-Json -Compress $d.SelectedPath}"
    else:
        script = "Add-Type -AssemblyName System.Windows.Forms; $d=New-Object System.Windows.Forms.OpenFileDialog; $d.Title='Choose a file for MultiGPU'; if($d.ShowDialog() -eq 'OK'){ConvertTo-Json -Compress $d.FileName}"
    result = subprocess.run(["powershell.exe", "-NoProfile", "-STA", "-Command", script], capture_output=True, text=True, timeout=300,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if result.returncode:
        raise ValueError("Windows could not open the file selector. Enter the path directly.")
    return {"path": json.loads(result.stdout.strip()) if result.stdout.strip() else ""}


def open_export(path):
    from .artifacts import verify_export
    path = Path(path).resolve()
    verify_export(path)
    if os.name != "nt":
        raise ValueError("Open-folder is available in the Windows app.")
    os.startfile(str(path))
    return {"opened": True, "path": str(path)}
