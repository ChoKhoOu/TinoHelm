# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules

hiddenimports = ['tinohelm', 'tinohelm.cli', 'tinohelm.cli.main', 'tinohelm.cli.backtest', 'tinohelm.cli.strategy', 'tinohelm.cli.data', 'tinohelm.cli.node', 'tinohelm.cli._http', 'tinohelm.cli._style']
hiddenimports += collect_submodules('tinohelm.cli')


a = Analysis(
    ['src/tinohelm/cli/main.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['nautilus_trader', 'fastapi', 'uvicorn', 'sqlalchemy', 'asyncpg', 'alembic', 'redis', 'pydantic_settings', 'plotly', 'optuna', 'numpy', 'pandas', 'scipy', 'matplotlib', 'PIL', 'torch', 'tkinter'],
    noarchive=False,
    optimize=2,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='tino',
    debug=False,
    bootloader_ignore_signals=False,
    strip=True,
    upx=True,
    upx_exclude=[],
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=True,
    upx=True,
    upx_exclude=[],
    name='tino',
)
