"""
Manage the llama-server subprocess used for AI tagging.

``llama-server`` (from the prebuilt llama.cpp binaries downloaded by
:mod:`app.utils.ai_runtime`) hosts the Qwen2.5-VL GGUF model and exposes an
OpenAI-compatible HTTP API.  We launch it once on demand, keep it alive for the
duration of a tagging session, and shut it down when the dialog closes (or at
process exit).  All communication uses the standard library — no third-party
dependencies.
"""
from __future__ import annotations

import atexit
import base64
import json
import logging
import socket
import subprocess
import sys
import time
import urllib.request
from typing import Callable, Optional

from app.utils import ai_runtime

log = logging.getLogger(__name__)

_HOST = "127.0.0.1"

# Context window: enough for several downscaled images plus the prompt/response.
_N_CTX = 8192

# How long to wait for the server to load the model and answer /health.
_STARTUP_TIMEOUT = 240
# How long a single completion request may take.
_REQUEST_TIMEOUT = 300


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((_HOST, 0))
        return s.getsockname()[1]


def _parse_chat_response(raw: str) -> str:
    """Extract the assistant message text from a chat-completions JSON body.

    Validates the structure of an OpenAI-compatible response so that a malformed
    or error payload raises a clear :class:`RuntimeError` instead of a bare
    ``KeyError``/``IndexError``/``JSONDecodeError`` deep in the caller.
    """
    try:
        resp = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"AI server returned malformed JSON: {exc}") from exc
    choices = resp.get("choices") if isinstance(resp, dict) else None
    if not choices:
        raise RuntimeError(f"AI server returned no choices: {resp!r}")
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str):
        raise RuntimeError("AI server response missing message content.")
    return content.strip()


class _Server:
    """Lifecycle wrapper around a single ``llama-server`` subprocess.

    Lazily launched via :meth:`ensure_running`, kept alive across requests, and
    shut down by :meth:`stop` (also registered with :mod:`atexit`). A module-level
    singleton ``_server`` is exposed through the convenience wrappers below.
    """

    def __init__(self) -> None:
        self._proc: Optional[subprocess.Popen] = None
        self._port: Optional[int] = None

    @property
    def base_url(self) -> Optional[str]:
        return f"http://{_HOST}:{self._port}" if self._port else None

    def is_running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    # ------------------------------------------------------------------
    def ensure_running(self, status_cb: Callable[[str, str], None] | None = None) -> None:
        """Start llama-server if it is not already running, and wait until ready."""
        if self.is_running():
            return

        exe = ai_runtime.llama_server_exe()
        model, mmproj = ai_runtime.model_paths()
        if not (exe and model and model.exists() and mmproj and mmproj.exists()):
            raise RuntimeError(
                "AI components are not installed. Run the AI setup first."
            )

        port = _free_port()
        args = [
            str(exe),
            "-m", str(model),
            "--mmproj", str(mmproj),
            "--host", _HOST,
            "--port", str(port),
            "-c", str(_N_CTX),
        ]
        if ai_runtime.installed_variant() == "cuda":
            args += ["-ngl", "99"]   # offload all layers to the GPU

        creationflags = 0
        if sys.platform == "win32":
            # Don't pop up a console window for the child process.
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        if status_cb:
            status_cb("Starting AI server (loading model)…", "#f59e0b")
        log.info("Launching llama-server on port %d", port)

        self._proc = subprocess.Popen(
            args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )
        self._port = port
        try:
            self._wait_ready()
        except Exception:
            self.stop()
            raise

    def _wait_ready(self) -> None:
        deadline = time.time() + _STARTUP_TIMEOUT
        url = f"{self.base_url}/health"
        last_error: str | None = None
        while time.time() < deadline:
            if not self.is_running():
                raise RuntimeError("AI server process exited during startup.")
            try:
                with urllib.request.urlopen(url, timeout=3) as r:
                    if r.status == 200:
                        log.info("AI server ready on %s", self.base_url)
                        return
                    last_error = f"HTTP {r.status}"
            except Exception as exc:
                # The server is still loading the model; keep polling but remember
                # the most recent failure so a timeout reports something useful.
                last_error = f"{type(exc).__name__}: {exc}"
            time.sleep(1.0)
        raise RuntimeError(
            f"AI server did not become ready within {_STARTUP_TIMEOUT}s. "
            f"Last error: {last_error or 'no response'}"
        )

    # ------------------------------------------------------------------
    def chat(self, image_bytes_list: list[bytes], prompt: str,
             max_tokens: int = 200, temperature: float = 0.2) -> str:
        """Send images + a text prompt to the model and return its reply text."""
        if not self.is_running():
            raise RuntimeError("AI server is not running.")

        from app.utils.thumbnailer import is_animated_thumbnail, make_thumbnail

        content: list[dict] = []
        for b in image_bytes_list:
            if is_animated_thumbnail(b):
                # Animated-GIF thumbnails would be sent mislabelled as JPEG below,
                # and the model only ever looks at one frame — flatten to a still.
                b = make_thumbnail(b, animate=False) or b
            b64 = base64.b64encode(b).decode("ascii")
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
            })
        content.append({"type": "text", "text": prompt})

        payload = {
            "messages": [{"role": "user", "content": content}],
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": False,
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/v1/chat/completions",
            data=data,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT) as r:
            if r.status != 200:
                raise RuntimeError(f"AI server returned HTTP {r.status}.")
            raw = r.read().decode("utf-8")
        return _parse_chat_response(raw)

    # ------------------------------------------------------------------
    def stop(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            try:
                self._proc.terminate()
                self._proc.wait(10)
            except Exception:
                # Graceful shutdown failed or timed out — force-kill and confirm
                # the process actually died so we don't leak a zombie.
                try:
                    self._proc.kill()
                    self._proc.wait(5)
                except Exception as exc:
                    log.error("Failed to kill AI server process: %s", exc)
        self._proc = None
        self._port = None


_server = _Server()
atexit.register(_server.stop)


# Module-level convenience wrappers ----------------------------------------

def ensure_running(status_cb: Callable[[str, str], None] | None = None) -> None:
    _server.ensure_running(status_cb)


def chat(image_bytes_list: list[bytes], prompt: str, max_tokens: int = 200,
         temperature: float = 0.2) -> str:
    return _server.chat(image_bytes_list, prompt, max_tokens, temperature)


def stop() -> None:
    _server.stop()


def is_running() -> bool:
    return _server.is_running()
