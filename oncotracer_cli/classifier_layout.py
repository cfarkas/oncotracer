"""Publish classifier reports into a stable, purpose-based directory layout.

The manifest records only artifacts managed by this publisher. Audit history and
browser-generated indexes are intentionally outside its ownership contract.
"""
from __future__ import annotations

import csv
import hashlib
import html
import io
import json
import os
import posixpath
import re
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit, urlunsplit

from .runtime import OncoTracerError


class ClassifierLayoutError(OncoTracerError, ValueError):
    """A layout refusal reported cleanly by the command-line entry point."""


SCHEMA = "oncotracer-classifier-layout-v1"
DIRECTORIES = {
    "01_prepared": "diagnostics/prepared",
    "02_classification": "tables/classification",
    "04_gistic2": "diagnostics/gistic",
    "05_gistic2_parsed": "diagnostics/gistic_parsed",
    "06_knowledge": "evidence",
    "07_pathology": "diagnostics/pathology",
}
LINK = re.compile(r"(?P<attribute>\b(?:href|src)\s*=\s*)(?P<quote>['\"])(?P<url>.*?)(?P=quote)", re.I)


def _digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _safe_relative(value: str) -> str:
    path = Path(value)
    if not value or path.is_absolute() or ".." in path.parts or "\\" in value:
        raise ClassifierLayoutError(f"Unsafe classifier layout path: {value!r}")
    return path.as_posix()


def _mapped(path: str, mapping: dict[str, str]) -> str:
    # A directory mapping also resolves links to its generated index page.
    seen = set()
    for _ in range(8):
        if path in seen:
            break
        seen.add(path)
        if path in mapping:
            target = mapping[path]
        else:
            prefix = next((p for p in sorted(mapping, key=len, reverse=True) if path.startswith(p + "/")), None)
            target = mapping[prefix] + path[len(prefix):] if prefix else path
        if target == path:
            break
        path = target
    return path


def classifier_path(stage: Path, legacy_relative: str) -> Path:
    mapping = dict(DIRECTORIES)
    manifest = stage / "layout_manifest.json"
    if manifest.is_file() and not manifest.is_symlink():
        data = json.loads(manifest.read_text())
        if data.get("schema") != SCHEMA:
            raise ClassifierLayoutError("Unrecognized classifier layout manifest")
        mapping.update(data.get("mappings", {}))
    return stage / _mapped(_safe_relative(legacy_relative), mapping)


def _link(value: str, source: str, destination: str, mapping: dict[str, str], stage: Path) -> str:
    value = html.unescape(value)
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc or not parsed.path or parsed.path.startswith("#"):
        return value
    decoded = unquote(parsed.path)
    if decoded.startswith("/"):
        try:
            target = Path(decoded).relative_to(stage).as_posix()
        except ValueError:
            return value
    else:
        target = posixpath.normpath(posixpath.join(posixpath.dirname(source), decoded))
    target = _mapped(target.rstrip("/"), mapping)
    relative = posixpath.relpath(target, posixpath.dirname(destination) or ".")
    if parsed.path.endswith("/"):
        relative += "/"
    return urlunsplit(("", "", quote(relative, safe="/._-"), parsed.query, parsed.fragment))


def _rewrite_html(data: bytes, source: str, destination: str, mapping: dict[str, str], stage: Path) -> bytes:
    def replace(match):
        url = _link(match.group("url"), source, destination, mapping, stage)
        return match.group("attribute") + match.group("quote") + html.escape(url, quote=True) + match.group("quote")
    return LINK.sub(replace, data.decode("utf-8")).encode("utf-8")


def _rewrite_index(data: bytes, source: str, destination: str, mapping: dict[str, str], stage: Path) -> bytes:
    reader = csv.DictReader(io.StringIO(data.decode("utf-8")), delimiter="\t")
    columns = reader.fieldnames
    if not columns:
        return data
    rows = list(reader)
    for row in rows:
        for column in ("html", "pdf"):
            if row.get(column):
                row[column] = _link(row[column], source, destination, mapping, stage)
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=columns, delimiter="\t", lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode()


def organize_classifier(stage: Path) -> dict:
    """Plan and publish recognized artifacts, refusing unsafe collisions first."""
    stage = Path(stage).absolute()
    if stage.is_symlink() or not stage.is_dir():
        raise ClassifierLayoutError(f"Classifier stage is not a physical directory: {stage}")
    # No symlink may redirect a source, parent, destination or audit entry.
    for ancestor in stage.parents:
        if ancestor.is_symlink():
            raise ClassifierLayoutError(f"Classifier parent is a symlink: {ancestor}")
    for path in stage.rglob("*"):
        if path.is_symlink():
            raise ClassifierLayoutError(f"Classifier layout refuses symlink: {path}")
    manifest_path = stage / "layout_manifest.json"
    prior = {}
    if manifest_path.exists():
        if not manifest_path.is_file():
            raise ClassifierLayoutError("Classifier layout manifest is not a file")
        prior = json.loads(manifest_path.read_text())
        if prior.get("schema") != SCHEMA:
            raise ClassifierLayoutError("Unrecognized classifier layout manifest")
    mapping = dict(DIRECTORIES)
    mapping.update(prior.get("mappings", {}))
    for source, destination in list(mapping.items()):
        _safe_relative(source)
        _safe_relative(destination)
    owned = prior.get("files", {})
    for key in owned:
        _safe_relative(key)
    inventory = {p.relative_to(stage).as_posix(): p for p in stage.rglob("*") if p.is_file() and ".reports" not in p.relative_to(stage).parts}
    moves: dict[str, str] = {}
    removed: dict[str, str] = {}
    previous_samples = prior.get("report_samples", {})
    report_samples = {kind: dict(values) for kind, values in previous_samples.items()}

    def move(source, destination):
        _safe_relative(source)
        _safe_relative(destination)
        mapping[source] = destination
        if source in inventory and source != destination:
            moves[source] = destination

    def discard(source, destination, reason):
        mapping[source] = destination
        if source in inventory:
            removed[source] = reason
            moves.pop(source, None)

    for source in inventory:
        for folder, destination in DIRECTORIES.items():
            if source.startswith(folder + "/"):
                move(source, destination + source[len(folder):])
                break
    # Cache artifacts are operational diagnostics, not evidence for reviewers.
    for prefix in ("06_knowledge", "evidence"):
        for source in inventory:
            if source.startswith(prefix + "/knowledge_http_cache/"):
                move(source, "diagnostics/cache/http/" + source.split("/knowledge_http_cache/", 1)[1])
            elif source == prefix + "/knowledge_cache.json":
                move(source, "diagnostics/cache/knowledge_cache.json")
        mapping[prefix + "/knowledge_http_cache"] = "diagnostics/cache/http"
        mapping[prefix + "/knowledge_cache.json"] = "diagnostics/cache/knowledge_cache.json"

    move("03_report/cna_classifier_report.html", "cohort_report.html")
    for source in inventory:
        if source.startswith("03_report/figures/"):
            name = source.removeprefix("03_report/figures/")
            category = "drivers" if "driver" in name else "cohort" if any(word in name for word in ("recurrent", "heatmap", "pca", "cluster")) else "summary"
            move(source, f"figures/{category}/{name}")

    # Resolve copied report tables only against byte-identical authoritative data.
    for source in inventory:
        if not source.startswith("03_report/report_tables/"):
            continue
        candidates = [name for name in inventory if Path(name).name == Path(source).name
                      and name != source and not name.startswith("03_report/")
                      and (name.split("/", 1)[0] in DIRECTORIES or name.startswith(("tables/", "evidence/", "diagnostics/")))]
        target = next((name for name in sorted(candidates) if _digest(inventory[name]) == _digest(inventory[source])), None)
        if target:
            discard(source, _mapped(target, mapping), "byte-identical report table copy")
        else:
            move(source, "tables/report_copies/" + source.removeprefix("03_report/report_tables/"))

    full_destinations = {}
    for kind, folder, suffix, root_name, combined, index_name in (
        ("knowledge", "llm_reports", "_CNA_knowledge_report", "final_report", "all_sample_CNA_knowledge_reports.pdf", "pdf_html_report_index.tsv"),
        ("clinician", "clinician_reports", "_clinical_driver_summary", "clinician_report", "all_sample_clinician_driver_summaries.pdf", "clinician_report_index.tsv"),
    ):
        prefix = "03_report/" + folder
        reports = {}
        for source in inventory:
            if source.startswith(prefix + "/") and Path(source).suffix in (".html", ".pdf") and Path(source).stem.endswith(suffix):
                slug = Path(source).stem.removesuffix(suffix)
                if not slug or slug in (".", ".."):
                    raise ClassifierLayoutError("Invalid sample report name")
                reports.setdefault(slug, {})[Path(source).suffix] = source
        if reports:
            report_samples[kind] = {slug: slug for slug in sorted(reports)}
        samples = report_samples.get(kind, {})
        multi = len(samples) > 1
        for slug in samples:
            destination = f"samples/{slug}/{root_name}" if multi else root_name
            if kind == "knowledge":
                full_destinations[slug] = destination + ".html"
            for extension in (".html", ".pdf"):
                source = f"{prefix}/{slug}{suffix}{extension}"
                move(source, destination + extension)
        if samples:
            index = prefix + "/index.html"
            if multi:
                move(index, root_name + ".html")
                move(prefix + "/" + combined, root_name + ".pdf")
            else:
                for extension, redundant, reason in (
                    (".html", index, "single-sample index replaced by full report"),
                    (".pdf", prefix + "/" + combined, "single-sample merged PDF replaced by full report"),
                ):
                    target = root_name + extension
                    if target in inventory or target in moves.values():
                        discard(redundant, target, reason)
                    else:
                        move(redundant, target)
        elif prefix + "/index.html" in inventory:
            move(prefix + "/index.html", root_name + ".html")
        target_index = "tables/report_index.tsv" if kind == "knowledge" else "tables/clinician_report_index.tsv"
        move(prefix + "/" + index_name, target_index)
        duplicate = prefix + "/pdf_report_index.tsv"
        if kind == "knowledge" and duplicate in inventory:
            original = prefix + "/" + index_name
            if original in inventory and _digest(inventory[original]) == _digest(inventory[duplicate]):
                discard(duplicate, target_index, "byte-identical report index copy")
            elif original not in inventory:
                move(duplicate, target_index)
            else:
                move(duplicate, "tables/legacy_pdf_report_index.tsv")

    # Basic CNA pages are superseded only by that sample's full report.
    sample_source = "03_report/sample_reports/"
    for source in inventory:
        if source.startswith(sample_source) and source.endswith("_CNA_report.html"):
            slug = Path(source).name.removesuffix("_CNA_report.html")
            target = full_destinations.get(slug)
            if target and (target in inventory or target in moves.values()):
                discard(source, target, "superseded by full sample report")
            else:
                move(source, f"samples/{slug}/cna_report.html")
    # A later rendering pass can supersede basic pages published in an earlier pass.
    for slug, target in full_destinations.items():
        basic = f"samples/{slug}/cna_report.html"
        if basic in owned and basic in inventory and (target in inventory or target in moves.values()):
            if _digest(inventory[basic]) != owned[basic]:
                raise ClassifierLayoutError(f"Previously published classifier artifact changed: {basic}")
            discard(basic, target, "superseded by full sample report")
            for source, destination in list(mapping.items()):
                if destination == basic:
                    mapping[source] = target
    basic_left = any(destination.endswith("/cna_report.html") for destination in moves.values()) or any(
        name.endswith("/cna_report.html") and name not in removed for name in inventory if name.startswith("samples/"))
    if full_destinations and not basic_left:
        discard(sample_source + "index.html", "final_report.html", "sample index superseded by full reports")
        if "samples/index.html" in owned:
            discard("samples/index.html", "final_report.html", "sample index superseded by full reports")
    else:
        move(sample_source + "index.html", "samples/index.html")

    # Preflight every destination and managed removal before writing any file.
    destinations: dict[str, str] = {}
    sources = dict(prior.get("sources", {}))
    for source, destination in moves.items():
        sources[source] = _digest(inventory[source])
        if destination in destinations and _digest(inventory[destinations[destination]]) != sources[source]:
            raise ClassifierLayoutError(f"Classifier artifacts collide at {destination}")
        destinations[destination] = source
        destination_path = stage / destination
        if destination_path.exists() and destination not in moves:
            if not destination_path.is_file():
                raise ClassifierLayoutError(f"Classifier destination is not a file: {destination}")
            actual = _digest(destination_path)
            if actual != sources[source] and (destination not in owned or owned[destination] != actual):
                raise ClassifierLayoutError(f"Refusing to replace foreign or changed classifier artifact: {destination}")
        for ancestor in destination_path.parents:
            if ancestor == stage:
                break
            if ancestor.exists() and not ancestor.is_dir():
                raise ClassifierLayoutError(f"Classifier destination parent is not a directory: {ancestor}")
    for source in removed:
        if source in owned and _digest(inventory[source]) != owned[source]:
            raise ClassifierLayoutError(f"Previously published classifier artifact changed: {source}")

    # The complete mapping permits links to targets published by later stages.
    payloads = {}
    for destination, source in destinations.items():
        path = inventory[source]
        if path.suffix.lower() == ".html":
            payloads[destination] = _rewrite_html(path.read_bytes(), source, destination, mapping, stage)
        elif destination in ("tables/report_index.tsv", "tables/clinician_report_index.tsv", "tables/legacy_pdf_report_index.tsv"):
            payloads[destination] = _rewrite_index(path.read_bytes(), source, destination, mapping, stage)
    # Links in canonical pages may point to a basic page superseded in this pass.
    for name, expected in owned.items():
        if name in inventory and name not in removed and name not in destinations and name.endswith(".html"):
            original = inventory[name].read_bytes()
            rewritten = _rewrite_html(original, name, name, mapping, stage)
            if rewritten != original:
                if _digest(inventory[name]) != expected:
                    raise ClassifierLayoutError(f"Previously published classifier artifact changed: {name}")
                payloads[name] = rewritten

    removals = list(prior.get("removed", []))
    for source, reason in removed.items():
        record = {"path": source, "sha256": _digest(inventory[source]), "reason": reason}
        if record not in removals:
            removals.append(record)
    for destination, source in destinations.items():
        target = stage / destination
        target.parent.mkdir(parents=True, exist_ok=True)
        if destination in payloads:
            target.write_bytes(payloads.pop(destination))
        elif target != inventory[source]:
            os.replace(inventory[source], target)
    for destination, content in payloads.items():
        (stage / destination).write_bytes(content)
    for source in set(moves) | set(removed):
        path = stage / source
        if path.is_file() and source not in destinations:
            path.unlink()
    # Remove only now-empty legacy directories; preserve every unknown file.
    for legacy in (*DIRECTORIES, "03_report"):
        directory = stage / legacy
        if directory.is_dir():
            for path in sorted((p for p in directory.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
                if not any(path.iterdir()):
                    path.rmdir()
            if not any(directory.iterdir()):
                directory.rmdir()
    emptied_parents = {parent for name in set(moves) | set(removed)
                       for parent in (stage / name).parents if parent != stage and stage in parent.parents}
    for directory in sorted(emptied_parents, key=lambda p: len(p.parts), reverse=True):
        if directory.is_dir() and not any(directory.iterdir()):
            directory.rmdir()
    managed = (set(owned) | set(destinations) | set(payloads)) - set(removed)
    presentation_indexes = {"index.html", "diagnostics.html", "evidence/index.html"}
    files = {name: _digest(stage / name) for name in sorted(managed)
             if name not in presentation_indexes and (stage / name).is_file()}
    manifest = {"schema": SCHEMA, "mappings": dict(sorted(mapping.items())), "files": files,
                "sources": sources, "removed": removals, "report_samples": report_samples}
    serialized = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    if not manifest_path.exists() or manifest_path.read_text() != serialized:
        manifest_path.write_text(serialized)
    return manifest
