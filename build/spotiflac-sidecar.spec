# PyInstaller spec for the Spotiflac sidecar.
#
# Produces a one-folder bundle (dist/spotiflac-sidecar/) that the Electron app
# ships under Contents/Resources/sidecar. spotdl pulls in a lot of optional and
# dynamically-imported modules, so we collect them wholesale rather than chase
# individual hidden imports.
#
# Build:  pyinstaller build/spotiflac-sidecar.spec --noconfirm

from PyInstaller.utils.hooks import collect_all, collect_submodules

datas, binaries, hiddenimports = [], [], []

for pkg in (
    "spotdl",
    "spotiflac",
    "server",
    "yt_dlp",
    "spotipy",
    "SpotipyFree",
    "ytmusicapi",
    "mutagen",
    "rich",
    "fastapi",
    "starlette",
    "uvicorn",
    "pydantic",
    "pydantic_core",
    "websockets",
    "rapidfuzz",
    "slugify",
    "pykakasi",
    "syncedlyrics",
    "soundcloud",
    "platformdirs",
    "datastar_py",
    "jinja2",
    "bs4",
    # Lossless backend (Tidal/Qobuz/Deezer). Safe to skip if not installed.
    "streamrip",
    "aiohttp",
    "aiofiles",
    "click",
    "tomlkit",
    "mutagen",
):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:
        # Optional dependency not installed in this environment.
        pass

# uvicorn loads its protocol/loop implementations lazily.
hiddenimports += collect_submodules("uvicorn")


block_cipher = None

a = Analysis(
    ["sidecar_entry.py"],
    pathex=[".."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "PyQt5", "PyQt6", "PySide6"],
    cipher=block_cipher,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="spotiflac-sidecar",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="spotiflac-sidecar",
)
