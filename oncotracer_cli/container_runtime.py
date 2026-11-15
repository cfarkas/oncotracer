"""User ownership and image references for the supported Docker backend."""
from __future__ import annotations
import os
from .runtime import OncoTracerError
BACKENDS = frozenset({"docker"})

def _require_backend(backend: str) -> None:
    if backend not in BACKENDS:
        raise OncoTracerError(f"Unsupported container backend: {backend}. Use Docker or native Conda tools.")

def user_arguments(backend: str) -> list[str]:
    _require_backend(backend)
    return ["--user", f"{os.getuid()}:{os.getgid()}"]

def image_reference(value, backend: str) -> str:
    from .cli import DEFAULT_IMAGE
    _require_backend(backend)
    return str(value or DEFAULT_IMAGE)
