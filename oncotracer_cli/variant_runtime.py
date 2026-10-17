"""Select the authenticated managed Illumina caller environment consistently.

This is selection, not discovery: a managed installation never borrows common
caller tools from another Conda environment or the inherited PATH. ONT callers
retain their explicit environments because they have separate dependencies.
"""
from __future__ import annotations

import os
import sys
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from .runtime import OncoTracerError


_ACTIVE_BACKEND = ContextVar("oncotracer_variant_runtime_backend", default=None)


@contextmanager
def variant_runtime_backend(backend: str):
    """Keep a command-line backend override authoritative during nested parsing."""
    token = _ACTIVE_BACKEND.set(backend)
    try:
        yield
    finally:
        _ACTIVE_BACKEND.reset(token)


@dataclass(frozen=True)
class VariantRuntimeSelection:
    prefix: Path | None = None
    managed: bool = False
    diagnostics: tuple[str, ...] = ()


def select_variant_runtime(config: Mapping[str, object], *, mode: str,
                           backend: str | None = None,
                           install_config: Mapping[str, object] | None = None,
                           environment: Mapping[str, str] | None = None) -> VariantRuntimeSelection:
    """Authenticate managed selection without probing or executing caller tools."""
    env = os.environ if environment is None else environment
    requested = backend or _ACTIVE_BACKEND.get() or str(config.get("execution_backend") or "")
    if (mode != "illumina" or requested in {"docker", "singularity", "apptainer"}
            or config.get("_docker_skip_host_tools")
            or env.get("ONCOTRACER_CONTAINER_RUNTIME") == "docker"):
        return VariantRuntimeSelection()
    if install_config is None:
        from .cli import _load_install_config
        install_config = _load_install_config()
    if install_config.get("backend") not in {"conda", "poetry"}:
        return VariantRuntimeSelection()
    managed_backend = str(install_config["backend"])
    repair = (f"Run oncotracer install --{managed_backend} to install or repair the "
              "managed variant environment, then save and check these settings again.")
    if not install_config.get("variants_prefix"):
        raise OncoTracerError("The managed OncoTracer installation has no validated variants_prefix. " + repair)
    from .install_safety import managed_variants_prefix
    try:
        prefix = managed_variants_prefix(dict(install_config), semantic=False)
    except (OncoTracerError, OSError, ValueError) as error:
        raise OncoTracerError(f"The managed OncoTracer variant environment failed authentication: {error}. {repair}") from error
    prefix = Path(prefix).expanduser().resolve()
    saved = config.get("variant_tool_prefix")
    inherited = env.get("ONCOTRACER_VARIANTS_PREFIX")
    previous = saved or inherited
    diagnostics = ()
    if previous and Path(str(previous)).expanduser().resolve() != prefix:
        origin = "saved variant_tool_prefix" if saved else "inherited ONCOTRACER_VARIANTS_PREFIX"
        diagnostics = (f"Using the validated OncoTracer variant environment {prefix}; "
                       f"{origin} {previous} is superseded. Save settings to record the managed path.",)
    return VariantRuntimeSelection(prefix, True, diagnostics)


def apply_variant_runtime(config: Mapping[str, object], *, mode: str, emit: bool = False,
                          **selection_options) -> tuple[dict[str, object], VariantRuntimeSelection]:
    result = dict(config)
    selection = select_variant_runtime(result, mode=mode, **selection_options)
    if selection.managed:
        result["variant_tool_prefix"] = str(selection.prefix)
    if emit:
        for message in selection.diagnostics:
            print(message, file=sys.stderr)
    return result, selection


@contextmanager
def managed_variant_runtime(config: Mapping[str, object], *, mode: str,
                            semantic: bool = False, **selection_options):
    """Keep the selected independent caller installation authenticated in use."""
    enabled = str(config.get("run_variants", False)).strip().lower() in {"true", "1", "yes", "on"}
    if not enabled:
        yield
        return
    selection = select_variant_runtime(config, mode=mode, **selection_options)
    if not selection.managed:
        yield
        return
    from .install_safety import managed_variants_runtime_lock
    with managed_variants_runtime_lock(selection.prefix, semantic=semantic):
        yield
