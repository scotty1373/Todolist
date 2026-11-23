# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
import sys

project = Path(SPECPATH)
analysis = Analysis(
    [str(project / "desktop_main.py")],
    pathex=[str(project)],
    datas=[
        (str(project / "web" / "templates"), "web/templates"),
        (str(project / "web" / "static"), "web/static"),
    ],
)
# Qt's Windows ICU imports are provided by the OS. An unrelated ICU on the
# build machine's search path can export incompatible, versioned symbols.
if sys.platform == "win32":
    windows_icu = {"icu.dll", "icuuc.dll", "icuin.dll"}
    icu_sources = {
        Path(source).parent
        for name, source, _ in analysis.binaries
        if Path(name).name.lower() in windows_icu
    }
    analysis.binaries = [
        (name, source, kind)
        for name, source, kind in analysis.binaries
        if Path(name).name.lower() not in windows_icu
        and not (Path(name).name.lower().startswith("icudt") and Path(source).parent in icu_sources)
    ]

pyz = PYZ(analysis.pure)
exe = EXE(
    pyz,
    analysis.scripts,
    analysis.binaries,
    analysis.datas,
    [],
    name="TodoList",
    console=False,
    upx=False,
)
