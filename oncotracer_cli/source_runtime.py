"""Keep Python children on the same OncoTracer source, wheel or release archive."""
from __future__ import annotations

from pathlib import Path
import sys


def package_bootstrap(source: Path | str) -> str:
    """Bind only our package, leaving stdlib and installed dependency order intact."""
    root = str(source)
    return (
        "def _oncotracer_bind_package():\n"
        "    import importlib.machinery, importlib.util, sys\n"
        f"    root = {root!r}\n"
        "    spec = importlib.machinery.PathFinder.find_spec('oncotracer_cli', [root])\n"
        "    if spec is None or spec.loader is None or spec.submodule_search_locations is None:\n"
        "        raise ImportError('Cannot load the selected OncoTracer package from ' + root)\n"
        # A user/--target wheel may be outside -I's dependency search path.
        # Append it without allowing sibling modules to outrank the stdlib.
        "    if root not in sys.path:\n"
        "        sys.path.append(root)\n"
        "    for name in tuple(sys.modules):\n"
        "        if name == 'oncotracer_cli' or name.startswith('oncotracer_cli.'):\n"
        "            del sys.modules[name]\n"
        "    package = importlib.util.module_from_spec(spec)\n"
        "    sys.modules['oncotracer_cli'] = package\n"
        "    spec.loader.exec_module(package)\n"
        "_oncotracer_bind_package()\n"
        "del _oncotracer_bind_package\n"
    )


def python_code(code: str, *, source: Path | None = None) -> list[str]:
    # Keep cwd (relative input paths), interpreter and dependency search order.
    # Pin the selected package without promoting an entire site-packages tree.
    root = Path(source).resolve() if source is not None else Path(__file__).resolve().parents[1]
    return [sys.executable, '-c', package_bootstrap(root) + code]


def python_module(module: str, *, source: Path | None = None) -> list[str]:
    return python_code(
        f'import runpy; runpy.run_module({module!r}, run_name="__main__", alter_sys=True)',
        source=source,
    )
