# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

datas = [
    ('resources', 'resources'),
]
datas += collect_data_files('qt_material')

hiddenimports = [
    'sqlalchemy.dialects.sqlite',
    'sqlalchemy.dialects.sqlite.pysqlite',
    'PIL._imaging',
    'PIL.Image',
    'PIL.ImageFile',
    'PIL.JpegImagePlugin',
    'PIL.PngImagePlugin',
    'PIL.BmpImagePlugin',
    'PIL.WebPImagePlugin',
    'PIL.GifImagePlugin',
    'PIL.TiffImagePlugin',
    # App modules
    'app.ui.ai_tagger_dialog',
    'app.ui.ai_setup_dialog',
    'app.utils.ai_runtime',
]
hiddenimports += collect_submodules('sqlalchemy.dialects.sqlite')

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={
        'PyQt6': {
            'plugins': ['platforms', 'imageformats', 'styles'],
        },
    },
    runtime_hooks=[],
    excludes=[
        'tkinter', 'matplotlib', 'scipy',
        # AI tagging runs via the external llama.cpp llama-server binary
        # (downloaded on demand) — no Python AI packages are bundled. The CLIP
        # image-embedding feature uses onnxruntime/numpy, also downloaded.
        'torch', 'torchvision', 'torchaudio', 'accelerate',
        'transformers', 'tokenizers', 'qwen_vl_utils', 'optimum',
        'onnxruntime', 'numpy',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='stlLibrary',
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
    icon='resources/icon.ico',
)
