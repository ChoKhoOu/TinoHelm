# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules

hiddenimports = ['tinohelm', 'tinohelm.cli', 'tinohelm.cli.main', 'tinohelm.cli.backtest', 'tinohelm.cli.strategy', 'tinohelm.cli.data', 'tinohelm.cli.node', 'tinohelm.cli._http', 'tinohelm.cli._style']
hiddenimports += collect_submodules('tinohelm.cli')


a = Analysis(
    ['/Users/ouzhuohao/TinoHelm/src/tinohelm/cli/main.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['nautilus_trader', 'fastapi', 'uvicorn', 'sqlalchemy', 'asyncpg', 'alembic', 'redis', 'pydantic_settings', 'plotly', 'optuna', 'numpy', 'pandas', 'scipy', 'matplotlib', 'PIL', 'cv2', 'torch', 'tensorflow', 'tkinter', 'test', 'unittest'],
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
    name='tino',
    debug=False,
    bootloader_ignore_signals=False,
    strip=True,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
