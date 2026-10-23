"""Small native Windows actions. No user-supplied shell commands are accepted."""
import json
import os
from pathlib import Path
import subprocess
import sys


def provider_signin(provider_id):
    if os.name != "nt":
        raise ValueError("Native sign-in is available in the Windows app. Use the displayed command instead.")
    if provider_id not in ("kaggle", "modal", "lightning"):
        raise ValueError("Choose an automatic provider with a supported native sign-in.")
    if sys.prefix == sys.base_prefix:
        raise ValueError("Open the app from its managed environment before signing in.")
    python = Path(sys.executable)
    if provider_id == "modal":
        command = [str(python), "-m", "modal", "setup"]
    else:
        client = python.parent / ("kaggle.exe" if provider_id == "kaggle" else "lightning.exe")
        if not client.is_file():
            raise ValueError("Prepare the provider tools in the app before signing in.")
        command = [str(client), *(["auth", "login"] if provider_id == "kaggle" else ["login"])]
    # Provider-owned interactive authentication stays in its own console/browser.
    process = subprocess.Popen(command, creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0), close_fds=True)
    return {"started": True, "provider_id": provider_id, "pid": process.pid,
            "message": "Finish sign-in in the provider window, then return to Save & check."}


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
