# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files, collect_submodules, collect_dynamic_libs, collect_all

block_cipher = None

datas = [
    ('resources', 'resources'),
]
datas += collect_data_files('qt_material')
datas += collect_data_files('onnxruntime')

# Bundle pip so install_ai_packages() can run inside the frozen .exe.
# (AI tagging packages are NOT included in the build — they are downloaded
#  on first use into <data_dir>/ai_packages/ via AISetupDialog.)
_pip_d, _pip_b, _pip_h = collect_all('pip')
datas    += _pip_d
binaries = collect_dynamic_libs('onnxruntime') + _pip_b

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
    # AI tagging modules — imported via deferred/conditional imports that
    # PyInstaller's static analyser can miss.
    'app.ui.ai_tagger_dialog',
    'app.ui.ai_setup_dialog',
    'app.utils.ai_runtime',
]
hiddenimports += collect_submodules('sqlalchemy.dialects.sqlite')
hiddenimports += collect_submodules('onnxruntime')
hiddenimports += _pip_h

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
    # AI tagging packages are NOT bundled — they are downloaded on first use
    # by AISetupDialog into <data_dir>/ai_packages/.  Listing them here
    # prevents accidental bundling when building on a dev machine that already
    # has them installed.
    excludes=[
        'tkinter', 'matplotlib', 'scipy',
        'torch', 'torchvision', 'transformers', 'accelerate', 'qwen_vl_utils',
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
