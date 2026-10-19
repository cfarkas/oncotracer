"""Read-only continuation from a saved ONT project into separate CpG projects."""
from __future__ import annotations

import csv
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path

from .discovery import DiscoveredSample, FastqDiscovery, _fastqs
from .engine import parse_ont_samples
from .runtime import OncoTracerError, load_flat_yaml


def _absolute(value, label):
    if not isinstance(value, str) or not value.strip() or not Path(value).expanduser().is_absolute():
        raise OncoTracerError(f"The source ONT project needs an absolute {label} path.")
    return Path(value).expanduser().resolve()


def _hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _overlap(left, right):
    return left == right or left in right.parents or right in left.parents


@dataclass
class CpgProject:
    project: Path
    config_path: Path
    config: dict
    discovery: FastqDiscovery
    selections: list[dict]
    fingerprints: dict[Path, str]
    inputs: dict[Path, tuple]
    protected: tuple[Path, ...]
    control_root: Path | None
    resource_paths: dict[str, str]
    missing_resources: dict[str, list[str]]
    missing_input: bool
    warnings: list[str]

    @property
    def projects(self):
        return {name: str(self.project.with_name(self.project.name + suffix))
                for name, suffix in (("marlin", "_MARLIN"), ("sturgeon", "_Sturgeon"))}

    def validate_unchanged(self):
        for path, expected in self.fingerprints.items():
            if not path.is_file() or _hash(path) != expected:
                raise OncoTracerError("The source ONT configuration or metadata changed; reopen --cpg-from before saving.")
        current = {}
        for sample in self.discovery.samples:
            for path in _fastqs(sample.fastq_dir):
                stat = path.stat()
                current[path] = (stat.st_size, stat.st_mtime_ns, stat.st_ino, stat.st_dev)
        if current != self.inputs:
            raise OncoTracerError("Source ONT FASTQ files changed; reopen --cpg-from and review the original project.")

    def validate_destination(self, path):
        path = Path(path).expanduser().resolve()
        if any(_overlap(path, protected) for protected in self.protected):
            raise OncoTracerError("Choose a separate CpG project outside the source ONT project, outputs, reference and input folders.")

    def validate_selections(self, entries):
        for entry in entries:
            expected = self.selections[entry["scan_index"]]
            if (entry["sample"], entry["sample_type"], entry["analysis_role"]) != (
                expected["name"], expected["label"] if expected["type"] == "custom" else expected["type"],
                expected["role"]):
                raise OncoTracerError("CpG continuation preserves source sample names and roles; change only classifier assignments.")

    def public(self, scan):
        return {"source_project": str(self.project), "projects": self.projects,
                "scan": scan, "selections": self.selections,
                "resource_paths": self.resource_paths, "missing_resources": self.missing_resources,
                "missing_input": self.missing_input, "warnings": self.warnings}


def prepare_cpg_setup(args):
    """Validate the saved project and prefill a namespace, without any writes."""
    from .setup import EXECUTABLES, RESOURCE_FLAGS, RESOURCE_FILES, _resource_keys

    existing = getattr(args, "_cpg_context", None)
    if existing is not None:
        return existing
    forbidden = ("run", "terminal", "manual", "non_interactive", "variant_config", "variants",
                 "input_folder", "reads_folder", "samplesheet", "fastq_1", "fastq_2",
                 "sample_name", "sample_names", "barcodes", "status", "project", "build_reference")
    selected = ["--" + name.replace("_", "-") for name in forbidden if getattr(args, name, None)]
    if selected:
        raise OncoTracerError("--cpg-from opens separate browser setup without starting analysis; omit " + ", ".join(selected))
    if getattr(args, "mode", None) not in (None, "ont") or getattr(args, "analysis", None) not in (None, "methylation"):
        raise OncoTracerError("--cpg-from requires ONT methylation-only setup.")
    backend = getattr(args, "backend", None) or "conda"
    if backend not in ("conda", "host", "poetry"):
        raise OncoTracerError("CpG classification requires --backend conda, host, or poetry.")
    project = Path(args.cpg_from).expanduser().resolve()
    config_path = project / "config/run.yml"
    if not project.is_dir() or not config_path.is_file():
        raise OncoTracerError("--cpg-from needs an existing ONT project containing config/run.yml.")
    config = load_flat_yaml(config_path)
    if config.get("mode") != "ont":
        raise OncoTracerError("--cpg-from requires an ONT project, not an Illumina project.")
    for key in ("ont_folder", "ont_normal_folder"):
        if config.get(key):
            _absolute(config[key], key)
    reference = _absolute(config.get("lpwgs_root"), "lpwgs_root reference")
    if not reference.is_dir():
        raise OncoTracerError("The source ONT reference is missing; restore it before opening CpG continuation.")
    output = _absolute(config.get("outdir"), "outdir")
    for key in ("hg38_build", "reference_root"):
        supplied = getattr(args, key, None)
        if supplied is not None and (not supplied or Path(supplied).expanduser().resolve() != reference):
            raise OncoTracerError("--cpg-from reuses the source project's reference; omit a different reference option.")
    native_samples = parse_ont_samples(config)
    single = config.get("ont_single_sample", False)
    study = [sample for sample in native_samples if sample.status == "tumor"]
    if not study:
        raise OncoTracerError("CpG continuation requires at least one saved ONT study sample; a control-only project is not supported.")
    root = study[0].fastq_dir if single else study[0].fastq_dir.parent
    controls = [sample for sample in native_samples if sample.status == "normal"]
    control_root = controls[0].fastq_dir.parent if controls else None
    fingerprints = {config_path: _hash(config_path)}
    metadata = {}
    metadata_path = (_absolute(config["sample_metadata"], "sample_metadata") if config.get("sample_metadata")
                     else project / "config/sample_metadata.csv")
    if metadata_path.is_file():
        with metadata_path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            if not {"sample", "sample_type", "analysis_role"}.issubset(reader.fieldnames or []):
                raise OncoTracerError("The source sample metadata needs sample, sample_type and analysis_role columns.")
            for row in reader:
                name = row["sample"]
                if not name or name in metadata:
                    raise OncoTracerError("Source sample metadata contains an empty or repeated sample name.")
                metadata[name] = row
        if set(metadata) != {sample.sample for sample in native_samples}:
            raise OncoTracerError("Source sample metadata does not match its configured ONT sample identities.")
        fingerprints[metadata_path] = _hash(metadata_path)
    elif config.get("sample_metadata"):
        raise OncoTracerError("The source ONT sample metadata file is missing.")
    discovered, selections, inputs, identities = [], [], {}, set()
    for index, sample in enumerate(native_samples):
        files = _fastqs(sample.fastq_dir)
        if not files:
            raise OncoTracerError("A configured ONT sample has no FASTQ files; restore its original inputs.")
        for path in files:
            stat = path.stat()
            identity = (stat.st_size, stat.st_mtime_ns, stat.st_ino, stat.st_dev)
            if path in inputs or identity[2:] in identities:
                raise OncoTracerError("Source ONT samples include the same FASTQ file more than once.")
            inputs[path] = identity
            identities.add(identity[2:])
        row = metadata.get(sample.sample, {})
        role = row.get("analysis_role", sample.status)
        label = row.get("sample_type", "normal" if role == "normal" else "cancer")
        if role != sample.status or not label or len(label) > 160 or any(ord(char) < 32 for char in label):
            raise OncoTracerError("Source ONT sample metadata has an invalid or inconsistent role/type.")
        kind = label if label in ("normal", "cancer") else "custom"
        if label.casefold() in ("normal", "control", "cancer"):
            kind = "normal" if label.casefold() in ("normal", "control") else "cancer"
            if (kind == "normal") != (role == "normal"):
                raise OncoTracerError("Source ONT sample type conflicts with its analysis role.")
            label = kind
        selections.append({"id": index, "name": sample.sample, "type": kind,
                           "label": label if kind == "custom" else "", "role": role})
        discovered.append(DiscoveredSample(sample.sample, files, barcode=sample.barcode,
                                           fastq_dir=sample.fastq_dir))
    resources = dict(config)
    if getattr(args, "resources", None):
        resource_file = Path(args.resources).expanduser().resolve()
        if not resource_file.is_file():
            raise OncoTracerError("The supplied methylation resource YAML is missing.")
        resources.update(load_flat_yaml(resource_file))
        fingerprints[resource_file] = _hash(resource_file)
    allowed = set(EXECUTABLES) | set(RESOURCE_FLAGS) | set(RESOURCE_FILES["marlin"]) | set(RESOURCE_FILES["sturgeon"])
    resource_paths = {}
    for key in allowed:
        value = getattr(args, key, None) or resources.get(key)
        if isinstance(value, str) and value.strip():
            path = Path(value).expanduser()
            if path.is_absolute() and path.exists():
                # Resolving an executable symlink can leave its Python/R environment.
                resource_paths[key] = str(path.absolute() if key in EXECUTABLES else path.resolve())
                setattr(args, key, resource_paths[key])
    modbam = getattr(args, "modbam", None)
    pod5 = getattr(args, "pod5_dir", None)
    if not modbam and not pod5:
        modbam = config.get("methylation_modbam")
        pod5 = None if modbam else config.get("methylation_pod5_dir")
    signal = modbam or pod5
    signal_path = _absolute(signal, "original methylation input") if signal else None
    missing_input = signal_path is None or not signal_path.exists()
    missing_resources = {}
    for classifier in ("marlin", "sturgeon"):
        required = [*_resource_keys(classifier), *RESOURCE_FILES[classifier]]
        if pod5:
            required += ["methylation_dorado_model", "methylation_dorado_modbase_model"]
        missing_resources[classifier] = [key for key in required if key not in resource_paths or
                                        (key in EXECUTABLES and (not Path(resource_paths[key]).is_file() or
                                                               not os.access(resource_paths[key], os.X_OK)))]
    warnings = []
    if missing_input:
        warnings.append("Choose the original modified-base BAMs (MM/ML calls) or POD5 files. A CNA alignment is not a substitute.")
    if any(missing_resources.values()):
        warnings.append("Some optional classifier tools/models are missing. Supply their existing paths before Save/check; no models are downloaded.")
    warnings.append("Inherited resource paths are not a scientific readiness result; Save/check validates the selected classifier configuration.")
    from .reference_bundle import reference_paths
    protected = [project, output, reference_paths(reference)[0],
                 *(sample.fastq_dir for sample in native_samples)]
    if signal_path:
        protected.append(signal_path)
    context = CpgProject(project, config_path, config, FastqDiscovery("ont", root, tuple(discovered), single_sample=single),
                         selections, fingerprints, inputs, tuple(protected), control_root,
                         resource_paths, missing_resources, missing_input, warnings)
    args.mode, args.analysis, args.backend, args.variants = "ont", "methylation", backend, False
    args.input_folder, args.hg38_build = str(root), str(reference)
    args.resources = getattr(args, "resources", None) or str(config_path)
    args.modbam = str(signal_path) if modbam and signal_path else None
    args.pod5_dir = str(signal_path) if pod5 and signal_path else None
    if args.threads is None:
        threads = config.get("threads", 4)
        args.threads = threads if type(threads) is int and threads > 0 else 4
    if getattr(args, "variant_specimen_type", None) is None:
        args.variant_specimen_type = config.get("variant_specimen_type")
    if getattr(args, "gpu", None) is None:
        args.gpu = config.get("methylation_gpu") is True
    args.project = context.projects[getattr(args, "classifier", None) or "marlin"]
    context.validate_destination(args.project)
    args._cpg_context = context
    return context
