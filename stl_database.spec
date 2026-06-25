# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

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

# Bundle pip (and certifi for TLS) so the frozen exe can install the optional
# AI packages at runtime with its own interpreter — no host Python required.
# See main.py `--run-pip` entry point and ai_runtime._run_pip.
binaries = []
for _pkg in ('pip', 'certifi'):
    _d, _b, _h = collect_all(_pkg)
    datas += _d
    binaries += _b
    hiddenimports += _h

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=binaries,
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
        # AI packages are downloaded on demand into ai_packages/ — not bundled
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
