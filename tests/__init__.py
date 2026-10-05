"""Resolve tests consistently without mutating managed Python environments."""

import os
import sys

# Test imports may use packages from an integrity-sealed managed environment.
# Preserve stale/missing caches both here and in Python subprocesses we launch.
sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
