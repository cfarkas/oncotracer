#!/usr/bin/env python3
"""Build entry point for the versioned GPU caller resource repairs."""
from pathlib import Path
import runpy
runpy.run_path(str(Path(__file__).resolve().parents[1]/'oncotracer_cli/gpu_callers.py'),run_name='__main__')
