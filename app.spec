# -*- mode: python ; coding: utf-8 -*-
# PyInstaller recipe: turns main.py into dist/<APP_NAME>.app (macOS).
# Run through build_mac.sh, or directly:  pyinstaller --noconfirm app.spec
import sys

from PyInstaller.utils.hooks import collect_data_files

sys.path.insert(0, SPECPATH)  # SPECPATH = folder of this file (PyInstaller global)
from Authentication.config import APP_NAME, APP_VERSION, BUNDLE_ID  # single source of truth

# Files that are not Python code but must travel inside the app.
datas = [
    ("assets", "assets"),  # dashboard logo, served by Dash
    ("database/market_data.db", "database"),  # market data snapshot, opened read-only
]
# PyInstaller has no ready-made recipe for this library: copy its JavaScript files explicitly.
datas += collect_data_files("dash_mantine_components")

a = Analysis(
    ["main.py"],
    pathex=[SPECPATH],
    datas=datas,
    excludes=["tkinter"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,  # "onedir" layout: the recommended one inside a .app
    name=APP_NAME,
    console=False,  # windowed app: no Terminal window
    argv_emulation=False,
    target_arch=None,  # architecture of the Python in use (arm64 on Apple Silicon)
    codesign_identity=None,  # None = ad-hoc signature; "Developer ID Application: ..." to distribute
    entitlements_file=None,
)
coll = COLLECT(exe, a.binaries, a.datas, name=APP_NAME)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name=f"{APP_NAME}.app",
        icon=None,  # e.g. "assets/icon.icns"
        bundle_identifier=BUNDLE_ID,
        version=APP_VERSION,
        info_plist={
            "CFBundleDisplayName": APP_NAME,
            "CFBundleShortVersionString": APP_VERSION,
            "CFBundleVersion": APP_VERSION,
            "LSMinimumSystemVersion": "14.0",  # the PySide6 6.12 wheels target macOS 14+
            "NSHighResolutionCapable": True,
        },
    )
