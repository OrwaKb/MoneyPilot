# MoneyPilot one-time setup: venv + deps + Desktop shortcut.
# Run from the project root:  powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

if (-not (Test-Path "$root\.venv")) {
    py -3.11 -m venv "$root\.venv"
}
& "$root\.venv\Scripts\python.exe" -m pip install -r "$root\requirements.txt"

$desktop = [Environment]::GetFolderPath("Desktop")
$ws = New-Object -ComObject WScript.Shell
$lnk = $ws.CreateShortcut("$desktop\MoneyPilot.lnk")
$lnk.TargetPath = "$root\.venv\Scripts\pythonw.exe"
$lnk.Arguments = "-m app"
$lnk.WorkingDirectory = $root
$icon = "$root\app\ui\assets\icon.ico"
if (Test-Path $icon) { $lnk.IconLocation = $icon }
$lnk.Save()

Write-Host "Done. Desktop shortcut 'MoneyPilot' created." -ForegroundColor Green
