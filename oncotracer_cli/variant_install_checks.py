"""Offline semantic checks for the canonical Illumina variant environment."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import re
import subprocess
import tempfile
from typing import Callable, Sequence

from .runtime import OncoTracerError, runtime_root


def _caller_contract():
    path = Path(__file__).parent / "lpwgs/bin/bcftools_compat.py"
    if not path.is_file():
        path = runtime_root() / "oncotracer_cli/lpwgs/bin/bcftools_compat.py"
    spec = importlib.util.spec_from_file_location("_oncotracer_install_caller_contract", path)
    if spec is None or spec.loader is None:
        raise OncoTracerError(f"bundled caller version contract is unavailable: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def verify_variant_environment(prefix: Path, *, runner: Callable | None = None) -> dict[str, object]:
    """Verify exact tools plus a synthetic diploid call without patient inputs."""
    prefix = Path(prefix).absolute()
    probes = {}
    contract = _caller_contract()
    with tempfile.TemporaryDirectory(prefix="oncotracer-variant-check-") as temporary:
        scratch = Path(temporary)
        environment = os.environ.copy()
        for key in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "JAVA_HOME", "JAVA_TOOL_OPTIONS", "_JAVA_OPTIONS",
                    "LD_LIBRARY_PATH", "LD_PRELOAD", "LD_LIBRARY_PATH_MCR", "PYTHONPYCACHEPREFIX"):
            environment.pop(key, None)
        environment.update(PATH=str(prefix / "bin") + os.pathsep + os.defpath,
                           CONDA_PREFIX=str(prefix), PYTHONDONTWRITEBYTECODE="1", PYTHONNOUSERSITE="1",
                           HOME=str(scratch), XDG_CONFIG_HOME=str(scratch / "config"),
                           XDG_CACHE_HOME=str(scratch / "cache"), TMPDIR=str(scratch))

        def invoke(name: str, arguments: Sequence[str | Path], accepted=(0,)) -> str:
            executable = prefix / "bin" / name
            if not executable.is_file() or not os.access(executable, os.X_OK):
                raise OncoTracerError(f"managed variant tool is missing: {executable}")
            if not executable.resolve().is_relative_to(prefix.resolve()):
                raise OncoTracerError(f"variant executable escapes its selected prefix: {executable}")
            command = [str(executable), *map(str, arguments)]
            try:
                result = (runner(command, environment) if runner else subprocess.run(
                    command, env=environment, capture_output=True, text=True, check=False, timeout=90))
            except (OSError, subprocess.TimeoutExpired) as error:
                raise OncoTracerError(f"variant runtime probe failed: {executable}: {error}") from error
            output = (result.stdout or "") + "\n" + (result.stderr or "")
            if result.returncode not in accepted:
                raise OncoTracerError(f"variant runtime probe failed (exit {result.returncode}): {executable}: {output.strip()[-1500:]}")
            return output

        output = invoke("bcftools", ["--version"])
        try:
            caller = contract.require_version(output, prefix / "bin/bcftools")
        except ValueError as error:
            raise OncoTracerError(str(error)) from error
        probes["bcftools"] = {"success": True, **caller}
        requirements = {
            "samtools": (r"^samtools 1\.23\.1\s*$", r"^Using htslib 1\.24\s*$"),
            "freebayes": (r"version:?\s+v?1\.3\.10(?:\s|$)",),
            "gatk": (r"(?:Genome Analysis Toolkit.*|GATK.*)v?4\.6\.2\.0(?:\s|$)",),
            "varlociraptor": (r"varlociraptor 8\.9\.5(?:\s|$)",),
        }
        for name, patterns in requirements.items():
            output = invoke(name, ["--version"])
            if not all(re.search(pattern, output, re.MULTILINE | re.IGNORECASE) for pattern in patterns):
                raise OncoTracerError(f"variant tool version differs from environments/native-variants.yml: {prefix / 'bin' / name}: {output.strip()[:1000]}")
            probes[name] = {"success": True, "executable": str(prefix / "bin" / name),
                            "version_output": output.strip()[:1000]}
        help_text = invoke("bcftools", ["call", "--help"], accepted=(0, 1, 255))
        for option in ("--multiallelic-caller", "--keep-alts", "--keep-unseen-allele", "--ploidy"):
            if option not in help_text:
                raise OncoTracerError(f"managed bcftools lacks required call option {option}")
        annotation = "-f" if "--format-fields" in help_text else "-a" if "--annotate" in help_text else None
        if annotation is None:
            raise OncoTracerError("managed bcftools cannot emit genotype quality")
        source = scratch / "likelihoods.vcf"
        source.write_text("##fileformat=VCFv4.2\n##contig=<ID=chr1,length=100>\n"
            "##INFO=<ID=QS,Number=R,Type=Float,Description=\"Allele quality sums\">\n"
            "##FORMAT=<ID=PL,Number=G,Type=Integer,Description=\"Genotype likelihoods\">\n"
            "##FORMAT=<ID=DP,Number=1,Type=Integer,Description=\"Depth\">\n"
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tSYNTHETIC\n"
            "chr1\t10\t.\tA\tG,<*>\t.\t.\tQS=0,100,0\tPL:DP\t100,30,0,100,100,100:30\n")
        output = scratch / "calls.vcf"
        invoke("bcftools", ["call", "-m", "-A", "--keep-unseen-allele", annotation, "GQ",
                            "--ploidy", "2", "-Ov", "-o", output, source])
        calls = []
        try:
            for line in output.read_text().splitlines():
                if line.startswith("#"):
                    continue
                fields = line.split("\t")
                sample = dict(zip(fields[8].split(":"), fields[9].split(":")))
                calls.append((int(fields[1]), sample["GT"], int(sample["DP"]), int(sample["GQ"])))
        except (OSError, IndexError, KeyError, ValueError) as error:
            raise OncoTracerError(f"managed bcftools synthetic caller output is invalid: {error}") from error
        if len(calls) != 1 or [(position, gt, dp) for position, gt, dp, gq in calls] != [(10, "1/1", 30)] or any(gq <= 0 for _, _, _, gq in calls):
            raise OncoTracerError(f"managed bcftools failed synthetic diploid genotype/DP/GQ validation: {calls}")
        probes["synthetic_call"] = {"success": True, "calls": [list(call) for call in calls]}
    return {"success": True, "prefix": str(prefix), "probes": probes}
