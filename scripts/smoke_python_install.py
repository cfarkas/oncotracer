#!/usr/bin/env python3
"""Exercise an installed wheel outside the checkout without starting an analysis."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument("--expected-source-sha256", required=True)
    parser.add_argument("--expected-version", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    python = os.path.abspath(args.python)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    records = []
    with tempfile.TemporaryDirectory(prefix="oncotracer-pip-smoke-") as scratch:
        root = Path(scratch); cwd = root / "unrelated working directory"; cwd.mkdir()
        environment = {key: value for key, value in os.environ.items() if not key.startswith(("PYTHON", "CONDA", "ONCOTRACER", "LD_"))}
        environment.update({"HOME": str(root / "home"), "XDG_CONFIG_HOME": str(root / "config"),
                            "XDG_DATA_HOME": str(root / "data"), "XDG_CACHE_HOME": str(root / "cache"), "TMPDIR": scratch,
                            "PATH": "/usr/bin:/bin"})
        def run(name, arguments):
            completed = subprocess.run([python, "-I", "-B", *arguments], cwd=cwd, env=environment,
                                       text=True, capture_output=True, timeout=120)
            log = args.output.parent / (args.output.stem + "-" + name + ".log")
            log.write_text(completed.stdout + completed.stderr)
            records.append({"name": name, "exit_code": completed.returncode, "log": str(log)})
            if completed.returncode:
                raise RuntimeError(f"installed-wheel {name} failed; see {log}")
            return completed.stdout
        for name, flags in (("no-arguments", []), ("help", ["--help"]), ("version", ["--version"]), ("setup-help", ["setup", "--help"])):
            run(name, ["-m", "oncotracer_cli.cli", *flags])
        probe = r'''
import importlib.metadata, json
from pathlib import Path
from oncotracer_cli import __version__
from oncotracer_cli.runtime import runtime_root
from oncotracer_cli.provenance import get_provenance, verify_distribution_files
from oncotracer_cli.install_safety import _source_identity
root = runtime_root()
assert verify_distribution_files()
for name in ('core', 'qdnaseq', 'ichorcna', 'classifier', 'gistic2', 'variants', 'ffperase', 'strelka2'):
    assert (root / ('environments/native-' + name + '.yml')).is_file(), name
for name in ('bin/scripts/native_qdnaseq.R', 'main.nf', 'nextflow.config', 'oncotracer_cli/lpwgs/main.nf', 'oncotracer_cli/lpwgs/bin/bcftools_compat.py'):
    assert (root / name).is_file(), name
metadata = importlib.metadata.metadata('oncotracer')
assert metadata['Name'] == 'oncotracer'
assert metadata['Version'] == __version__
print(json.dumps({'version': __version__, 'runtime_root': str(root), 'provenance': get_provenance(), 'installer_identity': _source_identity()}))
'''
        installed = json.loads(run("assets-provenance", ["-c", probe]))
        provenance = installed["provenance"]
        if installed["version"] != args.expected_version or provenance["source_commit"] != args.expected_commit or provenance["source_sha256"] != args.expected_source_sha256 or provenance["source_tree_dirty"] is not False:
            raise RuntimeError("installed wheel identity does not match the release candidate")
        for backend in ("conda", "docker", "singularity"):
            run(backend + "-preview", ["-m", "oncotracer_cli.cli", "install", "--" + backend, "--dry-run", "--no-launcher"])
        # Every operation above is read-only. A missing settings/data/cache home
        # also proves the previews did not save a backend or create tool assets.
        changed = [str(root / name) for name in ("home", "config", "data", "cache") if (root / name).exists()]
        if changed:
            raise RuntimeError("installation preview unexpectedly wrote user state: " + ", ".join(changed))
    result = {"passed": True, "source_commit": args.expected_commit, "source_sha256": args.expected_source_sha256,
              "version": args.expected_version, "analysis_started": False, "installed": installed, "checks": records}
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"passed": True, "checks": len(records), "source_commit": args.expected_commit}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
