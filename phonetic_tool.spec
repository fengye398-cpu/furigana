# -*- mode: python ; coding: utf-8 -*-

block_cipher = None

a = Analysis(
    ['test_ui_test2_copy_6.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('D:\\桌面\\JP_EN注音\\.venv\\lib\\site-packages\\pykakasi\\data', "pykakasi/data"),
        ('D:\\桌面\\JP_EN注音\\.venv\\lib\\site-packages\\tkinterdnd2\\tkdnd', "tkinterdnd2/tkdnd"),
        ('D:\\桌面\\JP_EN注音\\phonetic_accurate_db.sqlite', "."),
        ('D:\\桌面\\JP_EN注音\\app_icon.ico', "."),
        ('D:\\桌面\\JP_EN注音\\app_icon.png', "."),
        ('D:\\桌面\\JP_EN注音\\icon_manager.py', ".")
    ],
    hiddenimports=[
        'pysrt',
        'chardet',
        'icon_manager',
        'tkinterdnd2',
        'tkinterdnd2.TkinterDnD',
        'pykakasi',
        'sudachipy',
        'sudachidict_core',
        'pypinyin',
        'sqlite3',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'matplotlib',
        'numpy',
        'pandas',
        'scipy',
        'PIL.ImageQt',
        'PyQt5',
        'PyQt6',
        'PySide2',
        'PySide6',
        'IPython',
        'jupyter',
        'notebook',
        'pytest',
        'unittest',
        'doctest',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# 目录模式配置
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,  # 关键：使用目录模式
    name='日英中文注音工具',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='app_icon.ico'
)

# 收集所有文件到目录
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='日英中文注音工具'
)
