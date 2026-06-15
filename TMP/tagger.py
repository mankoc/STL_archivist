#!/usr/bin/env python3
"""GUI image tagger — Qwen2-VL for tag generation and character identification.
   Supports multiple images of the same model to improve accuracy.
"""

import threading
import warnings
import tkinter as tk
from tkinter import filedialog
from pathlib import Path

warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=UserWarning)

import torch
from PIL import Image, ImageTk
from huggingface_hub import hf_hub_download  # noqa: F401 (kept for potential future use)

QWEN_MODEL = "Qwen/Qwen2-VL-2B-Instruct"
THUMB_SIZE = 80
MAX_PREVIEW = 520

# Qwen2-VL encodes images as (H/28)*(W/28) tokens.
# Keep total visual tokens under this limit to avoid OOM on 8 GB VRAM.
MAX_VISUAL_TOKENS = 600
PATCH = 28


def image_size_for(n_images: int) -> int:
    """Return the per-image thumbnail size that keeps total tokens under budget."""
    tokens_each = MAX_VISUAL_TOKENS // max(n_images, 1)
    side = int((tokens_each ** 0.5) * PATCH)
    # Snap to a multiple of PATCH, clamp between 224 and 512
    side = max(224, min(512, (side // PATCH) * PATCH))
    return side


def build_qwen_prompt(stems: list[str]) -> str:
    filenames_str = ", ".join(f'"{s}"' for s in stems)
    intro = (
        f"These {len(stems)} images all show the same 3D model from different angles. "
        if len(stems) > 1 else
        "This image shows a 3D model. "
    )
    return (
        intro
        + f"The file(s) are named: {filenames_str}. "
        "Use the filenames as hints alongside the images to identify the character or subject depicted. "
        "Output ONLY a comma-separated list of tags. "
        "Include: character name (if recognizable), franchise or source material, character type, "
        "genre, visual style, and any other relevant descriptors. "
        "Example: Batman, DC Comics, superhero, dark, cape, fictional character\n"
        "If no specific character can be identified, output descriptive tags (style, genre, subject type). "
        "Output ONLY the comma-separated tags, no other text."
    )


BG      = "#1e1e2e"
PANEL   = "#2a2a3e"
PANEL2  = "#252538"
ACCENT  = "#7c6af7"
TEXT    = "#e0e0f0"
SUBTEXT = "#888aaa"
TAG_FG  = "#c9c7ff"
BTN_BG  = "#7c6af7"
BTN_FG  = "#ffffff"
BTN_HOV = "#9b8dff"
GREEN   = "#50fa7b"
ORANGE  = "#ffb86c"
RED     = "#ff5555"
GRAY    = "#888aaa"


class TaggerApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Image Tagger")
        self.root.configure(bg=BG)
        self.root.geometry("960x620")
        self.root.minsize(720, 480)

        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.qwen_model = None
        self.qwen_proc  = None

        self._main_photo: ImageTk.PhotoImage | None = None
        self._thumb_photos: list[ImageTk.PhotoImage] = []

        self._build_ui()
        threading.Thread(target=self._load_qwen, daemon=True).start()

    # ------------------------------------------------------------------ UI

    def _build_ui(self) -> None:
        # ── top bar ──────────────────────────────────────────────────────
        bar = tk.Frame(self.root, bg=PANEL, pady=10, padx=14)
        bar.pack(fill=tk.X)

        self.btn = tk.Button(
            bar, text="Pick images", command=self._pick,
            bg=BTN_BG, fg=BTN_FG, relief=tk.FLAT,
            font=("Segoe UI", 10, "bold"), padx=14, pady=6,
            activebackground=BTN_HOV, activeforeground=BTN_FG,
            cursor="hand2", state=tk.DISABLED,
        )
        self.btn.pack(side=tk.LEFT)
        self.btn.bind("<Enter>", lambda _: self.btn.config(bg=BTN_HOV))
        self.btn.bind("<Leave>", lambda _: self.btn.config(bg=BTN_BG))

        self.img_count_var = tk.StringVar()
        tk.Label(bar, textvariable=self.img_count_var,
                 bg=PANEL, fg=SUBTEXT, font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=(8, 0))

        self.status_var = tk.StringVar(value="Loading Qwen…")
        self.status_lbl = tk.Label(bar, textvariable=self.status_var,
                                   bg=PANEL, fg=GRAY, font=("Segoe UI", 9))
        self.status_lbl.pack(side=tk.LEFT, padx=(16, 4))

        # ── body ─────────────────────────────────────────────────────────
        body = tk.Frame(self.root, bg=BG)
        body.pack(fill=tk.BOTH, expand=True, padx=14, pady=12)

        # left: main preview + thumbnail strip
        left = tk.Frame(body, bg=PANEL)
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.img_label = tk.Label(left, text="No images selected",
                                  bg=PANEL, fg=SUBTEXT, font=("Segoe UI", 11))
        self.img_label.pack(fill=tk.BOTH, expand=True, padx=2, pady=2)

        strip_outer = tk.Frame(left, bg=PANEL2, height=THUMB_SIZE + 16)
        strip_outer.pack(fill=tk.X, side=tk.BOTTOM)
        strip_outer.pack_propagate(False)

        self.thumb_canvas = tk.Canvas(strip_outer, bg=PANEL2,
                                      highlightthickness=0, height=THUMB_SIZE + 12)
        thumb_hsb = tk.Scrollbar(strip_outer, orient=tk.HORIZONTAL,
                                 command=self.thumb_canvas.xview)
        self.thumb_canvas.configure(xscrollcommand=thumb_hsb.set)
        thumb_hsb.pack(side=tk.BOTTOM, fill=tk.X)
        self.thumb_canvas.pack(fill=tk.BOTH, expand=True)

        self.thumb_inner = tk.Frame(self.thumb_canvas, bg=PANEL2)
        self.thumb_canvas.create_window((0, 0), window=self.thumb_inner, anchor="nw")
        self.thumb_inner.bind(
            "<Configure>",
            lambda _: self.thumb_canvas.configure(
                scrollregion=self.thumb_canvas.bbox("all")
            ),
        )

        # right: tags panel
        right = tk.Frame(body, bg=PANEL, width=230)
        right.pack(side=tk.RIGHT, fill=tk.Y, padx=(12, 0))
        right.pack_propagate(False)

        tk.Label(right, text="Tags", bg=PANEL, fg=TEXT,
                 font=("Segoe UI", 10, "bold"), anchor="w",
                 padx=10, pady=8).pack(fill=tk.X)
        tk.Frame(right, bg=ACCENT, height=1).pack(fill=tk.X, padx=10)

        tags_frame = tk.Frame(right, bg=PANEL)
        tags_frame.pack(fill=tk.BOTH, expand=True, padx=6, pady=4)
        sb = tk.Scrollbar(tags_frame, orient=tk.VERTICAL)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.tag_list = tk.Listbox(
            tags_frame, yscrollcommand=sb.set,
            bg=PANEL, fg=TAG_FG, selectbackground=PANEL2,
            activestyle="none", relief=tk.FLAT,
            font=("Segoe UI", 9), bd=0,
        )
        self.tag_list.pack(fill=tk.BOTH, expand=True)
        sb.config(command=self.tag_list.yview)

        self.tag_count_var = tk.StringVar()
        tk.Label(right, textvariable=self.tag_count_var, bg=PANEL,
                 fg=SUBTEXT, font=("Segoe UI", 8), pady=4).pack()

    # ------------------------------------------------------------------ model loading

    def _load_qwen(self) -> None:
        try:
            from transformers import Qwen2VLForConditionalGeneration, AutoProcessor
            self._set_status("Qwen: downloading… (first run)", ORANGE)
            proc = AutoProcessor.from_pretrained(QWEN_MODEL)
            model = Qwen2VLForConditionalGeneration.from_pretrained(
                QWEN_MODEL, torch_dtype=torch.float16, device_map="cuda",
            )
            model.eval()
            self.qwen_proc  = proc
            self.qwen_model = model
            self.root.after(0, lambda: self._set_status("Ready", GREEN))
            self.root.after(0, lambda: self.btn.config(state=tk.NORMAL))
        except Exception as e:
            self.root.after(0, lambda e=e: self._set_status(f"Error: {e}", RED))

    # ------------------------------------------------------------------ image flow

    def _pick(self) -> None:
        paths = filedialog.askopenfilenames(
            title="Select images (same model, multiple angles)",
            filetypes=[("Images", "*.jpg *.jpeg *.png *.webp *.bmp *.tiff"), ("All", "*.*")],
        )
        if paths:
            self._process(list(paths))

    def _process(self, paths: list[str]) -> None:
        self.btn.config(state=tk.DISABLED)
        self.tag_list.delete(0, tk.END)
        self.tag_count_var.set("")
        self.img_count_var.set(f"{len(paths)} image{'s' if len(paths) != 1 else ''}")
        self._show_preview(paths[0])
        self._build_thumbs(paths)
        threading.Thread(target=self._run_pipeline, args=(paths,), daemon=True).start()

    def _show_preview(self, path: str) -> None:
        img = Image.open(path).convert("RGB")
        img.thumbnail((MAX_PREVIEW, MAX_PREVIEW), Image.LANCZOS)
        self._main_photo = ImageTk.PhotoImage(img)
        self.img_label.config(image=self._main_photo, text="")

    def _build_thumbs(self, paths: list[str]) -> None:
        for widget in self.thumb_inner.winfo_children():
            widget.destroy()
        self._thumb_photos = []

        for path in paths:
            img = Image.open(path).convert("RGB")
            img.thumbnail((THUMB_SIZE, THUMB_SIZE), Image.LANCZOS)
            photo = ImageTk.PhotoImage(img)
            self._thumb_photos.append(photo)

            lbl = tk.Label(self.thumb_inner, image=photo,
                           bg=PANEL2, cursor="hand2", bd=2, relief=tk.FLAT)
            lbl.pack(side=tk.LEFT, padx=4, pady=6)
            lbl.bind("<Button-1>", lambda e, p=path: self._show_preview(p))
            lbl.bind("<Enter>",    lambda e, w=lbl: w.config(bg=ACCENT))
            lbl.bind("<Leave>",    lambda e, w=lbl: w.config(bg=PANEL2))

    # ------------------------------------------------------------------ pipeline

    def _run_pipeline(self, paths: list[str]) -> None:
        self.root.after(0, lambda: self._set_status("Tagging…", ORANGE))
        try:
            from qwen_vl_utils import process_vision_info

            size = image_size_for(len(paths))
            resized = []
            for path in paths:
                img = Image.open(path).convert("RGB")
                img.thumbnail((size, size), Image.LANCZOS)
                resized.append(img)

            stems = [Path(p).stem for p in paths]
            content = [{"type": "image", "image": img} for img in resized]
            content.append({"type": "text", "text": build_qwen_prompt(stems)})

            messages = [{"role": "user", "content": content}]
            text = self.qwen_proc.apply_chat_template(
                messages, tokenize=False, add_generation_prompt=True
            )
            image_inputs, video_inputs = process_vision_info(messages)
            inputs = self.qwen_proc(
                text=[text], images=image_inputs, videos=video_inputs,
                padding=True, return_tensors="pt",
            ).to("cuda")

            with torch.no_grad():
                ids = self.qwen_model.generate(**inputs, max_new_tokens=120)
            trimmed = ids[0][inputs.input_ids.shape[1]:]
            answer = self.qwen_proc.decode(trimmed, skip_special_tokens=True).strip()
            seen: set[str] = set()
            tags = [
                t for raw in answer.split(",")
                if (t := raw.strip()) and t.lower() not in seen and not seen.add(t.lower())
            ]

            self.root.after(0, lambda t=tags: self._show_tags(t))
            self.root.after(0, lambda: self._set_status("Ready", GREEN))
        except Exception as e:
            self.root.after(0, lambda e=e: self._set_status(f"Error: {e}", RED))

        self.root.after(0, lambda: self.btn.config(state=tk.NORMAL))

    # ------------------------------------------------------------------ helpers

    def _show_tags(self, tags: list[str]) -> None:
        self.tag_list.delete(0, tk.END)
        for tag in tags:
            self.tag_list.insert(tk.END, f"  {tag}")
        self.tag_count_var.set(f"{len(tags)} tags")

    def _set_status(self, text: str, color: str = GRAY) -> None:
        self.status_var.set(text)
        self.status_lbl.config(fg=color)


def main() -> None:
    root = tk.Tk()
    TaggerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
