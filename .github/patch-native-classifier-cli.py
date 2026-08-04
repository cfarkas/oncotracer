#!/usr/bin/env python3
"""Apply or verify the audited portable-tool probe repair in the v2 CLI."""
from pathlib import Path
import re

path = Path('oncotracer_cli/cli.py')
text = path.read_text(encoding='utf-8')
changed = False

old_check = '''def _check_process(command: Sequence[str], *, env: dict[str, str] | None = None) -> dict[str, object]:
    completed = subprocess.run(command, text=True, capture_output=True, env=env, check=False)
    text = (completed.stdout + completed.stderr).strip().splitlines()
    return {
        "command": shlex.join(command),
        "returncode": completed.returncode,
        "first_line": text[0] if text else "",
    }
'''
new_check = '''def _check_process(
    command: Sequence[str],
    *,
    env: dict[str, str] | None = None,
    accepted_returncodes: set[int] | frozenset[int] = frozenset({0}),
) -> dict[str, object]:
    completed = subprocess.run(command, text=True, capture_output=True, env=env, check=False)
    lines = (completed.stdout + completed.stderr).strip().splitlines()
    first_line = lines[0] if lines else ""
    return {
        "command": shlex.join(command),
        "returncode": completed.returncode,
        "first_line": first_line,
        "success": completed.returncode in accepted_returncodes and bool(first_line),
    }
'''
if old_check in text:
    text = text.replace(old_check, new_check, 1)
    changed = True
elif 'accepted_returncodes' not in text or '"success"' not in text:
    raise SystemExit('ERROR: unrecognized _check_process implementation')

doctor_marker = 'def command_doctor(args: argparse.Namespace) -> int:\n'
if doctor_marker not in text:
    raise SystemExit('ERROR: command_doctor was not found')
head, doctor = text.split(doctor_marker, 1)

if 'probes: dict[str, tuple[list[str], frozenset[int]]]' not in doctor:
    pattern = re.compile(
        r'    if backend in \{"host", "poetry", "conda"\}:\n.*?\n    elif backend == "docker":',
        re.DOTALL,
    )
    replacement = '''    if backend in {"host", "poetry", "conda"}:
        install = _load_install_config()
        environment = _native_environment(install)
        probes: dict[str, tuple[list[str], frozenset[int]]] = {
            "samtools": (["samtools", "--version"], frozenset({0})),
            # BWA prints a valid version/usage banner and exits 1 without a subcommand.
            "bwa": (["bwa"], frozenset({0, 1})),
            # Some minimap2 builds print a valid version while returning 1.
            "minimap2": (["minimap2", "--version"], frozenset({0, 1})),
            "pigz": (["pigz", "--version"], frozenset({0})),
        }
        command_checks: dict[str, dict[str, object]] = {}
        for command, (probe, accepted_returncodes) in probes.items():
            executable = shutil.which(command, path=environment.get("PATH"))
            if not executable:
                command_checks[command] = {
                    "command": shlex.join(probe),
                    "returncode": None,
                    "first_line": "",
                    "present": False,
                    "success": False,
                }
                continue
            result = _check_process(
                probe,
                env=environment,
                accepted_returncodes=accepted_returncodes,
            )
            result["present"] = True
            command_checks[command] = result
        checks["commands"] = command_checks
        prefixes = {}
        for name in ("core", "qdnaseq", "ichorcna", "classifier", "gistic2"):
            value = install.get(f"{name}_prefix")
            if value:
                prefix = Path(str(value))
                prefixes[name] = {"path": str(prefix), "exists": prefix.is_dir()}
        checks["prefixes"] = prefixes
    elif backend == "docker":'''
    doctor, count = pattern.subn(replacement, doctor, count=1)
    if count != 1:
        raise SystemExit('ERROR: expected doctor backend block was not found')
    text = head + doctor_marker + doctor
    changed = True

old_success = '''    success = True
    for value in checks.get("commands", {}).values() if isinstance(checks.get("commands"), dict) else []:
        success = success and value.get("returncode") == 0
'''
new_success = '''    success = True
    for value in checks.get("commands", {}).values() if isinstance(checks.get("commands"), dict) else []:
        success = success and bool(value.get("success"))
    if backend == "conda":
        configured = checks.get("prefixes", {})
        success = success and bool(configured) and all(
            bool(value.get("exists")) for value in configured.values()
        )
'''
if old_success in text:
    text = text.replace(old_success, new_success, 1)
    changed = True
elif 'value.get("success")' not in text:
    raise SystemExit('ERROR: unrecognized doctor success calculation')

path.write_text(text, encoding='utf-8')
print('portable_probe_patch=' + ('applied' if changed else 'already_present'))
