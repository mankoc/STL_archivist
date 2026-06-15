# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files, collect_submodules, collect_dynamic_libs, collect_all

block_cipher = None

datas = [
    ('resources', 'resources'),
]
datas += collect_data_files('qt_material')
datas += collect_data_files('onnxruntime')
datas += collect_data_files('transformers')
datas += collect_data_files('tokenizers')

binaries = collect_dynamic_libs('onnxruntime')

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
    'onnxruntime',
    'onnxruntime.capi._pybind_state',
    'onnxruntime.capi.onnxruntime_pybind11_state',
    'numpy',
    'numpy.core._multiarray_umath',
    # AI tagger runtime (raw onnxruntime sessions, no PyTorch or optimum needed)
    # Only AutoTokenizer is used — do NOT import image_processing_qwen2_vl (imports torch)
    'transformers',
    'transformers.models.qwen2_vl',
    'tokenizers',
    # App modules
    'app.ui.ai_tagger_dialog',
    'app.ui.ai_setup_dialog',
    'app.utils.ai_runtime',
]
hiddenimports += collect_submodules('sqlalchemy.dialects.sqlite')
hiddenimports += collect_submodules('onnxruntime')

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
        'torch', 'torchvision', 'accelerate', 'qwen_vl_utils', 'optimum',
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
