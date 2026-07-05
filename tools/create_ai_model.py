#!/usr/bin/env python3
"""
One-time model downloader for stlLibrary's AI Tagger.

Downloads the pre-exported ONNX version of Qwen2-VL-2B-Instruct from
onnx-community/Qwen2-VL-2B-Instruct on HuggingFace.  No PyTorch required.

Requirements (install in any Python environment):
    pip install huggingface-hub

Usage:
    python tools/create_ai_model.py --output <data_dir>/ai_cache

  The data_dir is shown in stlLibrary → Settings → Data Directory.
  The model is written to <output>/qwen2vl_onnx/ and is picked up
  automatically by the AI Tagger on the next launch.

Optional flags:
    --variant   ONNX precision variant to download.
                  fp16     — float16, best for GPU  (default)
                  q4f16    — INT4 weights + fp16 compute, smaller GPU option
                  int8     — INT8, good CPU/GPU balance
                  q4       — INT4, smallest files, CPU-optimised
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

HF_REPO    = "onnx-community/Qwen2-VL-2B-Instruct"
OUT_SUBDIR = "qwen2vl_onnx"

# Files that are always needed (config, tokenizer, etc.)
_BASE_PATTERNS = [
    "*.json",
    "*.txt",
    "*.tiktoken",
    "vocab.json",
    "merges.txt",
]

# Quantisation variants → patterns for the three required ONNX components
_VARIANT_PATTERNS: dict[str, list[str]] = {
    "fp16":   [
        "onnx/vision_encoder_fp16.onnx",
        "onnx/vision_encoder_fp16.onnx_data",
        "onnx/embed_tokens_fp16.onnx",
        "onnx/decoder_model_merged_fp16.onnx",
        "onnx/decoder_model_merged_fp16.onnx_data",
    ],
    "q4f16":  [
        "onnx/vision_encoder_q4f16.onnx",
        "onnx/vision_encoder_q4f16.onnx_data",
        "onnx/embed_tokens_q4f16.onnx",
        "onnx/decoder_model_merged_q4f16.onnx",
    ],
    "int8":   [
        "onnx/vision_encoder_int8.onnx",
        "onnx/embed_tokens_int8.onnx",
        "onnx/decoder_model_merged_int8.onnx",
    ],
    "q4":     [
        "onnx/vision_encoder_q4.onnx",
        "onnx/vision_encoder_q4.onnx_data",
        "onnx/embed_tokens_q4.onnx",
        "onnx/decoder_model_merged_q4.onnx",
    ],
}


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Download Qwen2-VL-2B ONNX model for stlLibrary AI Tagger."
    )
    ap.add_argument(
        "--output", required=True, type=Path,
        help="Parent directory (e.g. ~/.local/share/stlLibrary/ai_cache). "
             "The model is written to <output>/qwen2vl_onnx/.",
    )
    ap.add_argument(
        "--variant", default="fp16",
        choices=list(_VARIANT_PATTERNS),
        help="ONNX precision variant (default: fp16 — best for GPU).",
    )
    args = ap.parse_args()

    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        print("ERROR: huggingface-hub not found.")
        print("Install with:  pip install huggingface-hub")
        sys.exit(1)

    output = args.output / OUT_SUBDIR
    output.mkdir(parents=True, exist_ok=True)

    patterns = _BASE_PATTERNS + _VARIANT_PATTERNS[args.variant]

    size_hint = {
        "fp16": "~3.5 GB",
        "q4f16": "~1.5 GB",
        "int8": "~2.2 GB",
        "q4": "~1.2 GB",
    }[args.variant]

    print(f"Downloading {HF_REPO} [{args.variant}] ({size_hint})…")
    print(f"Output: {output}")
    print()

    snapshot_download(
        HF_REPO,
        local_dir=str(output),
        allow_patterns=patterns,
    )

    print()
    print("=" * 60)
    print("Done!  stlLibrary will detect the model automatically.")
    print(f"Variant : {args.variant}")
    print(f"Location: {output}")
    print("=" * 60)


if __name__ == "__main__":
    main()
