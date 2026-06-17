# PyInstaller spec — one-folder MoneyPilot.exe bundling the app, the UI assets,
# and the Claude Agent SDK's runtime (so AI can run once a friend signs in).
# Build with:  scripts\build_exe.ps1   (or: pyinstaller MoneyPilot.spec)
import os
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

# --- read-only resources: ship app/ui/** preserving the tree so resource_path()
#     finds them at _MEIPASS/app/ui/... exactly as it does from source. -----------
datas = []
for root, _dirs, files in os.walk(os.path.join("app", "ui")):
    for f in files:
        datas.append((os.path.join(root, f), root))

# --- Claude runtime: the SDK's _bundled/claude.exe (~233MB) + any package data.
datas += collect_data_files("claude_agent_sdk", include_py_files=False)

hiddenimports = (
    collect_submodules("webview")          # pywebview platform backends
    + collect_submodules("claude_agent_sdk")
    + ["anyio", "clr_loader", "pythonnet"]  # winforms/WebView2 bridge deps
)

a = Analysis(
    ["run_app.py"],
    pathex=["."],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["pytest", "_pytest", "tests", "web", "uvicorn", "fastapi",
              "starlette", "matplotlib", "PIL"],
    cipher=block_cipher,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="MoneyPilot",
    console=False,                      # windowed GUI app (pythonw-style)
    icon=os.path.join("app", "ui", "assets", "icon.ico"),
)
coll = COLLECT(
    exe, a.binaries, a.datas,
    strip=False, upx=False,
    name="MoneyPilot",                  # -> dist/MoneyPilot/MoneyPilot.exe
)
