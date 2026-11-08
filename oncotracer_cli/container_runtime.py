"""Small engine-specific differences for the shared Docker/Podman image."""
from __future__ import annotations

import os

from .runtime import OncoTracerError

BACKENDS = frozenset({"docker", "podman"})


def user_arguments(backend: str) -> list[str]:
    if backend not in BACKENDS:
        raise OncoTracerError(f"Unsupported container backend: {backend}")
    # Rootless Podman otherwise maps the requested container UID to a subordinate
    # host UID. keep-id preserves ownership and access to existing project files.
    arguments = ["--userns=keep-id"] if backend == "podman" and os.geteuid() != 0 else []
    arguments += ["--user", f"{os.getuid()}:{os.getgid()}"]
    if backend == "podman":
        arguments += ["--env", "ONCOTRACER_CONTAINER_RUNTIME=podman", "--env", "HOME=/tmp"]
    return arguments


def image_reference(value, backend: str) -> str:
    from .cli import DEFAULT_IMAGE
    image = str(value or DEFAULT_IMAGE)
    # Podman short-name resolution may prompt without a terminal. Qualify our
    # published default; explicit local tags and other registries stay verbatim.
    return "docker.io/" + image if backend == "podman" and image == DEFAULT_IMAGE else image
