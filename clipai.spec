# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller one-file build definition for Pedantic.

Built with ``python -m PyInstaller clipai.spec --noconfirm --clean``.

Most of this file is hidden imports. PyInstaller finds imports by static
analysis, and the libraries here all resolve their real implementation at
runtime: pystray and pynput pick a backend by platform, keyring discovers its
backends through entry points, and the Anthropic SDK imports its transport
lazily. None of those are visible to the analyzer, so they are listed
explicitly -- and a missing one produces an executable that starts and then
fails at the first hotkey rather than failing at build time.
"""

import re
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

APP_NAME = "Pedantic"
PUBLISHER = "Pedantic"

# Read the version out of the package instead of repeating it here, so the
# file metadata below cannot drift from clipai.__version__.
_init_source = Path(SPECPATH, "clipai", "__init__.py").read_text(encoding="utf-8")
_version_match = re.search(r'^__version__ = "([^"]+)"', _init_source, re.MULTILINE)
if _version_match is None:
    raise SystemExit("clipai/__init__.py does not define __version__")
VERSION = _version_match.group(1)

# Windows version resources are always four numbers.
_parts = [int(part) for part in VERSION.split(".")]
VERSION_TUPLE = tuple(_parts + [0] * (4 - len(_parts)))

# Embedding a version resource gives the executable a publisher, product name
# and version in its PE header. Without one it is an anonymous unsigned blob,
# which is itself a signal to antivirus heuristics -- and those heuristics are
# already unkind to PyInstaller one-file builds. Generated into build/ because
# it is derived from the version above, not something to edit by hand.
VERSION_RESOURCE = f"""VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={VERSION_TUPLE},
    prodvers={VERSION_TUPLE},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0),
  ),
  kids=[
    StringFileInfo([
      StringTable(
        "040904B0",
        [
          StringStruct("CompanyName", "{PUBLISHER}"),
          StringStruct("FileDescription", "{APP_NAME} text transformation utility"),
          StringStruct("FileVersion", "{VERSION}"),
          StringStruct("InternalName", "{APP_NAME}"),
          StringStruct("LegalCopyright", "Copyright (C) {PUBLISHER}"),
          StringStruct("OriginalFilename", "{APP_NAME}.exe"),
          StringStruct("ProductName", "{APP_NAME}"),
          StringStruct("ProductVersion", "{VERSION}"),
        ],
      )
    ]),
    VarFileInfo([VarStruct("Translation", [0x0409, 1200])]),
  ],
)
"""

version_resource_path = Path(SPECPATH, "build", "version_info.txt")
version_resource_path.parent.mkdir(parents=True, exist_ok=True)
version_resource_path.write_text(VERSION_RESOURCE, encoding="utf-8")

hidden_imports = [
    # Tray icon and image generation.
    "pystray",
    "pystray._win32",
    "PIL",
    "PIL.Image",
    "PIL.ImageDraw",
    "PIL.ImageFont",
    # Global hotkeys and synthetic input.
    "pynput",
    "pynput.keyboard",
    "pynput.keyboard._win32",
    "pynput.mouse",
    "pynput.mouse._win32",
    # Credential storage. keyring resolves backends through entry points,
    # which do not survive freezing.
    "keyring",
    "keyring.backends",
    "keyring.backends.Windows",
    "keyring.backends.fail",
    "keyring.backends.null",
    "win32ctypes.core",
    "win32ctypes.core.cffi",
    "win32ctypes.core.ctypes",
    # Anthropic SDK and its HTTP stack.
    "anthropic",
    "anthropic._client",
    "httpx2",
    "httpcore2",
    "h11",
    "anyio",
    "sniffio",
    "certifi",
    "truststore",
    "jiter",
    "pydantic",
    # Configuration.
    "tomllib",
    "tomli_w",
    "watchdog",
    "watchdog.observers",
    "watchdog.observers.winapi",
    # Win32 clipboard and window helpers.
    "win32clipboard",
    "win32api",
    "win32con",
    "win32gui",
    "win32process",
    "pywintypes",
    # Dialogs and the history window.
    "tkinter",
    "tkinter.messagebox",
    "tkinter.scrolledtext",
    # Application modules reached only through lazy imports.
    "clipai.runtime_frozen",
    "clipai.backends.anthropic_api",
]
hidden_imports += collect_submodules("clipai")

# The CA bundle must be inside the executable; the frozen runtime hook exports
# its unpacked location through SSL_CERT_FILE.
datas = collect_data_files("certifi")

excludes = [
    # Large scientific and GUI stacks that some dependency graphs pull in but
    # that Pedantic never uses.
    "numpy",
    "pandas",
    "matplotlib",
    "scipy",
    "PyQt5",
    "PyQt6",
    "PySide2",
    "PySide6",
    "IPython",
    "pytest",
    "setuptools",
    "pip",
]

a = Analysis(
    ["clipai/__main__.py"],
    pathex=["."],
    binaries=[],
    datas=datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=["clipai/pyi_rth_frozen.py"],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # UPX stays off. A compressed executable trips considerably more antivirus
    # heuristics than an uncompressed one, and the ~10 MB saved is not worth
    # users being told the download is malware. It also caused loader failures
    # with the pywin32 DLLs, which previously needed individual exclusions.
    upx=False,
    runtime_tmpdir=None,
    version=str(version_resource_path),
    # A tray application must not open a console window on launch.
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
