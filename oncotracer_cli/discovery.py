"""Read-only FASTQ discovery for the guided setup.

Discovery proposes technical sample IDs, never clinical roles. Illumina inputs
must already fit the runner's one-file-per-mate samplesheet contract; discovery
does not merge lanes or guess how unrelated files belong together.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re

from .engine import _resolve_fastq_pass, _single_ont_sample_folder
from .runtime import OncoTracerError, require_directory


FASTQ_SUFFIXES = (".fastq.gz", ".fq.gz", ".fastq", ".fq")
_BARCODE = re.compile(r"barcode\d+", re.IGNORECASE)
# Preserve the whole non-mate suffix to prevent cross-lane/chunk pairing.
_MATE = re.compile(r"^(?P<stem>.*?)(?P<separator>[._-])(?P<label>R?)(?P<mate>[12])(?P<chunk>(?:[._-]\d+)?)$", re.IGNORECASE)
_BARE_MATE = re.compile(r"^R(?P<mate>[12])(?P<chunk>(?:[._-]\d+)?)$", re.IGNORECASE)
_LANE = re.compile(r"[._-]L\d{3}$", re.IGNORECASE)


@dataclass(frozen=True)
class DiscoveredSample:
    sample: str
    files: tuple[Path, ...]
    fastq_1: Path | None = None
    fastq_2: Path | None = None
    barcode: str | None = None
    fastq_dir: Path | None = None


@dataclass(frozen=True)
class FastqDiscovery:
    mode: str
    root: Path
    samples: tuple[DiscoveredSample, ...]
    warnings: tuple[str, ...] = ()
    single_sample: bool = False


def _fastq_stem(path: Path) -> str | None:
    return next((path.name[:-len(suffix)] for suffix in FASTQ_SUFFIXES if path.name.endswith(suffix)), None)


def _fastqs(folder: Path) -> tuple[Path, ...]:
    paths: list[Path] = []
    identities: dict[Path, Path] = {}

    def fail(error: OSError) -> None:
        raise OncoTracerError(f"Cannot inspect FASTQ folder: {error}") from error

    # Do not follow directory symlinks: a run tree may contain links back to
    # parents or into another run. Explicitly selected root symlinks are resolved.
    for parent, directories, filenames in os.walk(folder, onerror=fail, followlinks=False):
        directories.sort()
        for name in sorted(filenames):
            path = Path(parent) / name
            if _fastq_stem(path) is None:
                continue
            if not path.is_file() or path.stat().st_size == 0:
                raise OncoTracerError(f"FASTQ is missing or empty: {path}")
            resolved = path.resolve()
            if resolved in identities:
                raise OncoTracerError(f"The same FASTQ appears more than once: {identities[resolved]} and {path}")
            identities[resolved] = path
            paths.append(path)
    return tuple(sorted(paths))


def _barcode(name: str) -> bool:
    return name == "unclassified" or _BARCODE.fullmatch(name) is not None


def detect_fastq_mode(folder: str | Path) -> str | None:
    """Infer platform from conventional layout, or return None for plain FASTQs.

    A filename alone cannot establish whether an unpaired FASTQ is Illumina or
    ONT. The wizard should ask the user when no platform-specific layout exists.
    """
    root = require_directory(Path(folder), "FASTQ folder")
    # Follow the same pass-folder selection as ONT discovery. Failed or partial
    # batches elsewhere in a MinKNOW run must not block its completed pass data.
    scan_root = root
    if not _barcode(root.name):
        try:
            scan_root = _resolve_fastq_pass(root)
        except OncoTracerError:
            pass
    paths = _fastqs(scan_root)
    if not paths:
        raise OncoTracerError(f"No FASTQ files found in {scan_root}")
    if _barcode(scan_root.name) or any(
        _barcode(part) for path in paths for part in path.relative_to(scan_root).parts[:-1]
    ):
        return "ont"
    if any(_MATE.fullmatch(_fastq_stem(path) or "") or _BARE_MATE.fullmatch(_fastq_stem(path) or "") for path in paths):
        return "illumina"
    return None


def _sample_id(stem: str, warnings: list[str]) -> str:
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._-") or "sample"
    if name != stem:
        warnings.append(f"Suggested sample name {name!r} for {stem!r}; confirm or rename it in setup.")
    return name


def _illumina(root: Path) -> FastqDiscovery:
    paths = _fastqs(root)
    if not paths:
        raise OncoTracerError(f"No FASTQ files found in {root}")
    warnings: list[str] = []
    pairs: dict[tuple[Path, str], dict[str, list[tuple[Path, str]]]] = {}
    singles: list[tuple[str, Path]] = []
    for path in paths:
        stem = _fastq_stem(path)
        assert stem is not None
        match = _MATE.fullmatch(stem)
        bare = _BARE_MATE.fullmatch(stem) if match is None else None
        if match or bare:
            if match:
                sample_stem = _LANE.sub("", match["stem"])
                # Lane and mate spelling are part of the pair identity too.
                identity = match["stem"] + match["separator"] + match["label"] + "{mate}" + match["chunk"]
                mate = match["mate"]
            else:
                assert bare is not None
                sample_stem = path.parent.name
                identity = "R{mate}" + bare["chunk"]
                mate = bare["mate"]
            group = pairs.setdefault((path.parent, sample_stem), {"1": [], "2": []})
            group[mate].append((path, identity))
        else:
            singles.append((stem, path))

    samples: list[DiscoveredSample] = []
    for (_, stem), mates in sorted(pairs.items()):
        if len(mates["1"]) != 1 or len(mates["2"]) != 1:
            names = ", ".join(str(path) for items in mates.values() for path, _ in items)
            raise OncoTracerError(
                f"Cannot identify one complete R1/R2 pair for {stem!r}: "
                f"found {len(mates['1'])} R1 and {len(mates['2'])} R2 files ({names}). "
                "Keep one matched pair per library. For intentional R1-only single-end data, "
                "use setup --manual --fastq-1 or an explicit samplesheet; combine sequencing lanes before setup."
            )
        (r1, first_identity), (r2, second_identity) = mates["1"][0], mates["2"][0]
        if first_identity != second_identity:
            raise OncoTracerError(
                f"FASTQ mate names do not match for {stem!r}: {r1.name} and {r2.name}; "
                "lane, chunk, and naming convention must agree. Use an explicit samplesheet if this pairing is intentional."
            )
        samples.append(DiscoveredSample(_sample_id(stem, warnings), (r1, r2), fastq_1=r1, fastq_2=r2))
    for stem, path in singles:
        samples.append(DiscoveredSample(_sample_id(stem, warnings), (path,), fastq_1=path))
    by_name: dict[str, DiscoveredSample] = {}
    for sample in samples:
        if sample.sample in by_name:
            raise OncoTracerError(
                f"Ambiguous sample name {sample.sample!r} from multiple FASTQ files or folders: "
                f"{by_name[sample.sample].files[0]} and {sample.files[0]}. "
                "Choose a narrower folder or provide an explicit samplesheet with unique sample names."
            )
        by_name[sample.sample] = sample
    return FastqDiscovery("illumina", root, tuple(sorted(samples, key=lambda sample: sample.sample)), tuple(warnings))


def _ont(folder: Path) -> FastqDiscovery:
    selected = folder.name if _barcode(folder.name) else None
    try:
        root = _resolve_fastq_pass(folder.parent if selected else folder)
    except OncoTracerError:
        # Preserve the error for ambiguous MinKNOW runs; an explicit plain
        # folder can instead contain all FASTQ batches of one ligation library.
        if selected or any(path.is_dir() for path in folder.rglob("fastq_pass")):
            raise
        root = folder
    if selected and folder.parent != root:
        raise OncoTracerError(f"The selected barcode folder must be directly below the FASTQ parent: {folder}")
    paths = _fastqs(folder if selected else root)
    if not paths:
        raise OncoTracerError(f"No FASTQ files found in {folder}")
    if not any(_barcode(part) for path in paths for part in path.relative_to(root).parts[:-1]):
        _single_ont_sample_folder(root)
        warnings: list[str] = []
        stem = root.parent.name if root.name == "fastq_pass" else root.name
        name = _sample_id(stem, warnings)
        warnings.append(
            f"All {len(paths)} FASTQs in this folder form one nonbarcoded sample; "
            "select it only when these batches belong to one library."
        )
        sample = DiscoveredSample(name, paths, barcode=".", fastq_dir=root)
        return FastqDiscovery("ont", root, (sample,), tuple(warnings), single_sample=True)
    grouped: dict[str, list[Path]] = {}
    for path in paths:
        relative = path.relative_to(root)
        if len(relative.parts) < 2 or not _barcode(relative.parts[0]):
            raise OncoTracerError(
                f"ONT FASTQ is outside a barcode or unclassified folder: {path}. "
                "Choose a fastq_pass folder containing demultiplexed barcode folders."
            )
        grouped.setdefault(relative.parts[0], []).append(path)
    samples = tuple(
        DiscoveredSample(barcode, tuple(files), barcode=barcode, fastq_dir=root / barcode)
        for barcode, files in sorted(grouped.items())
    )
    warnings = (
        ("Unclassified reads have no assigned barcode; include them only when they represent a known sample.",)
        if "unclassified" in grouped else ()
    )
    return FastqDiscovery("ont", root, samples, warnings)


def discover_fastqs(folder: str | Path, mode: str) -> FastqDiscovery:
    """Return validated, deterministic mappings without writing or reading data bodies.

    File sizes and naming/layout are checked here. Sequence contents, read-pair
    identity, and compressed-stream integrity are outside this discovery step.
    """
    root = require_directory(Path(folder), "FASTQ folder")
    try:
        if mode == "illumina":
            return _illumina(root)
        if mode == "ont":
            return _ont(root)
        raise OncoTracerError("FASTQ discovery mode must be illumina or ont")
    except OSError as error:
        raise OncoTracerError(f"Cannot inspect FASTQ folder {root}: {error}") from error


def discover_ont_inputs(folder: str | Path, *, fastq_root: Path | None = None) -> dict:
    """Suggest only signal inputs belonging to the selected/discovered ONT run.

    Match conventional sibling directories without following directory symlinks
    outside that run. This is path discovery, not MM/ML or read-ID validation.
    The optional FASTQ root is supplied by the completed FASTQ discovery so a
    nested MinKNOW run cannot accidentally borrow a different run's signals.
    """
    selected = require_directory(Path(folder), "ONT input folder")
    notes: list[str] = []
    fastq_names = {"fastq_pass", "fastq", "fastqs"}
    signal_names = {"pod5_pass", "pod5", "bam_pass", "modbam"}

    def children(path: Path) -> list[Path]:
        try:
            with os.scandir(path) as entries:
                paths = []
                for index, entry in enumerate(entries):
                    if index >= 256:
                        notes.append(f"Only the first 256 entries in {path} were checked; browse explicitly if needed.")
                        break
                    paths.append(Path(entry.path))
            return sorted(paths)
        except OSError:
            notes.append(f"Could not inspect signal inputs in {path}; browse explicitly if needed.")
            return []

    if fastq_root is not None:
        fastq = require_directory(fastq_root, "Discovered ONT FASTQ folder")
        if _barcode(selected.name) and selected.parent == fastq:
            fastq = selected
    elif _barcode(selected.name) or selected.name in fastq_names:
        fastq = selected
    elif selected.name in signal_names:
        fastq = selected.parent / "fastq_pass"
        if not fastq.is_dir():
            fastq = selected.parent
    elif (selected / "fastq_pass").is_dir():
        fastq = (selected / "fastq_pass").resolve()
    else:
        # A run-selection button can start one or two folders above a MinKNOW
        # run. Search at most three levels and 128 directories; never global disks.
        notes_before_search = len(notes)
        matches, pending, visited = [], [(selected, 0)], 0
        while pending and visited < 128:
            parent, depth = pending.pop(0)
            visited += 1
            for child in children(parent):
                if child.is_symlink() or not child.is_dir():
                    continue
                if child.name == "fastq_pass":
                    matches.append(child)
                elif depth < 2 and child.name not in signal_names and not _barcode(child.name):
                    pending.append((child, depth + 1))
        if pending or len(notes) > notes_before_search:
            raise OncoTracerError("ONT run search was incomplete; select the specific run or its FASTQ folder.")
        if len(matches) > 1:
            raise OncoTracerError("Multiple ONT runs found; choose one run or its fastq_pass folder.")
        fastq = matches[0] if matches else selected

    parent = fastq.parent if _barcode(fastq.name) else fastq
    run = parent.parent if parent.name in fastq_names else parent
    selected_barcode = fastq.name if _barcode(fastq.name) else None

    def local_directory(path: Path) -> Path | None:
        try:
            resolved = path.resolve()
            if path.is_dir() and (resolved == run or run in resolved.parents):
                return path
        except OSError:
            pass
        return None

    def signal_directory(names: tuple[str, ...]) -> Path | None:
        for name in names:
            candidate = local_directory(run / name)
            if candidate is not None:
                if selected_barcode:
                    specific = local_directory(candidate / selected_barcode)
                    if specific is not None:
                        return specific
                return candidate
        return None

    pod5 = signal_directory(("pod5_pass", "pod5"))
    modbam = signal_directory(("bam_pass", "modbam"))
    direct = children(run) if pod5 is None or modbam is None else []
    direct_files = []
    for path in direct:
        try:
            if path.is_file() and not path.is_symlink() and path.stat().st_size > 0:
                direct_files.append(path)
        except OSError:
            notes.append(f"Could not inspect {path}; browse explicitly if needed.")
    if pod5 is None and any(path.suffix.lower() == ".pod5" for path in direct_files):
        pod5 = run
    if modbam is None:
        bams = [path for path in direct_files if path.suffix.lower() == ".bam"]
        if len(bams) == 1:
            modbam = bams[0]
        elif len(bams) > 1:
            notes.append("Multiple BAM files exist in this run; select the intended modified-base BAM or folder explicitly.")
    return {"run": str(run), "fastq": str(fastq), "pod5": str(pod5) if pod5 else "",
            "modbam": str(modbam) if modbam else "", "notes": notes}
