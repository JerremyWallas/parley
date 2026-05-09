# PyInstaller spec file for Parley Desktop Client
# Build with: cd desktop && pyinstaller build.spec

# Collect soundfile's bundled libsndfile DLL/SO — PyInstaller's default
# heuristics don't always pick it up, and without it the Opus encoder
# silently falls back to WAV at runtime.
from PyInstaller.utils.hooks import collect_dynamic_libs, collect_data_files

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=collect_dynamic_libs('soundfile'),
    datas=collect_data_files('soundfile'),
    hiddenimports=[
        'pynput.keyboard._win32',
        'pynput.mouse._win32',
        'settings_ui',
        'overlay',
        'tray_window',
        'icon',
        'urllib3',
        'websocket',
        'soundfile',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='Parley',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    icon='parley.ico',
)
