"""Keep Python children on the same OncoTracer source, wheel or release archive."""
from __future__ import annotations

from pathlib import Path
import sys


def python_code(code: str, *, source: Path | None = None) -> list[str]:
    # PYTHONPATH alone loses to the working directory for both -c and -m.
    # Keep cwd (relative input paths) and the selected interpreter/dependencies.
    root = Path(source).resolve() if source is not None else Path(__file__).resolve().parents[1]
    bootstrap = f'import sys; sys.path.insert(0, {str(root)!r});\n'
    return [sys.executable, '-c', bootstrap + code]


def python_module(module: str, *, source: Path | None = None) -> list[str]:
    return python_code(
        f'import runpy; runpy.run_module({module!r}, run_name="__main__", alter_sys=True)',
        source=source,
    )
