# PyInstaller spec for Fing.exe: build with build.ps1 (or `pyinstaller packaging\fing.spec` from the project folder).
# A one-folder build (dist\Fing\Fing.exe plus _internal\), so it starts quickly and the CUDA DLLs load from disk.

import os

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules, copy_metadata
from PyInstaller.utils.win32.versioninfo import (FixedFileInfo, StringFileInfo, StringStruct, StringTable,
                                                 VarFileInfo, VarStruct, VSVersionInfo)

ROOT = os.path.dirname(SPECPATH)
VERSION = (1, 0, 0, 0)

datas = [
    (os.path.join(ROOT, "fing", "icon.ico"), "fing"),
    (os.path.join(ROOT, "fing", "web"), os.path.join("fing", "web")),
]
datas += collect_data_files("faster_whisper")  # the VAD model
datas += copy_metadata("keyring")  # keyring finds Credential Manager through its entry points

binaries = collect_dynamic_libs("ctranslate2")
binaries += collect_dynamic_libs("nvidia")  # cuBLAS/cuDNN, kept at nvidia\*\bin for stt.add_cuda_dll_dirs

hiddenimports = ["fing"] + collect_submodules("fing")  # imported lazily inside functions
hiddenimports += collect_submodules("winrt")
hiddenimports += collect_submodules("comtypes")
hiddenimports += collect_submodules("typesafe_sdk")
hiddenimports += ["pystray._win32", "keyring.backends.Windows"]

version = VSVersionInfo(
    ffi=FixedFileInfo(filevers=VERSION, prodvers=VERSION),
    kids=[
        StringFileInfo([StringTable("040904B0", [
            StringStruct("CompanyName", "Fing"),
            StringStruct("FileDescription", "Fing"),  # the name Task Manager shows
            StringStruct("FileVersion", ".".join(map(str, VERSION))),
            StringStruct("InternalName", "Fing"),
            StringStruct("OriginalFilename", "Fing.exe"),
            StringStruct("ProductName", "Fing"),
            StringStruct("ProductVersion", ".".join(map(str, VERSION))),
        ])]),
        VarFileInfo([VarStruct("Translation", [0x0409, 1200])]),
    ],
)

a = Analysis(
    [os.path.join(SPECPATH, "launcher.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["pytest", "jevharness"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Fing",
    icon=os.path.join(ROOT, "fing", "icon.ico"),
    version=version,
    console=False,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="Fing", upx=False)
