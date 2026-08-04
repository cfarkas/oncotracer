#!/usr/bin/env python3
"""Finalize audited doctor semantics after native classifier materialization."""
from pathlib import Path

path = Path('oncotracer_cli/cli.py')
text = path.read_text(encoding='utf-8')
old_names = 'for name in ("core", "qdnaseq", "ichorcna", "classifier", "gistic2"):'
new_names = 'for name in ("core", "qdnaseq", "ichorcna", "classifier", "gistic"):'
if old_names in text:
    text = text.replace(old_names, new_names, 1)
elif new_names not in text:
    raise SystemExit('ERROR: doctor prefix list was not recognized')

old = '''        checks["prefixes"] = prefixes
    elif backend == "docker":
'''
new = '''        checks["prefixes"] = prefixes
        success = all(bool(value.get("success")) for value in command_checks.values())
        if backend == "conda":
            required_prefixes = {"core", "qdnaseq", "ichorcna", "classifier", "gistic"}
            success = success and required_prefixes.issubset(prefixes) and all(
                bool(prefixes[name].get("exists")) for name in required_prefixes
            )
    elif backend == "docker":
'''
if old in text:
    text = text.replace(old, new, 1)
elif 'required_prefixes = {"core", "qdnaseq", "ichorcna", "classifier", "gistic"}' not in text:
    raise SystemExit('ERROR: doctor success insertion point was not recognized')

path.write_text(text, encoding='utf-8')
print('native_v2_doctor_finalized=true')
