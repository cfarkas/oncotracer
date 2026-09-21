"""Reproducible English manuscript panels from explicit, auditable tabular inputs.

This module renders supplied measurements. It does not run callers, infer patient
identity, or turn cross-platform agreement into accuracy against truth.
"""
from __future__ import annotations

import csv
import hashlib
import html
import json
import math
import re
import os
import tempfile
from pathlib import Path

from .runtime import OncoTracerError

SCHEMA = "oncotracer-paper-report-v1"
PALETTE = ("#4C9BD6", "#D95F5F", "#39A96B", "#9978AD", "#DCA44A", "#8C939D")
STYLE = {"font.family": "DejaVu Sans", "font.size": 10.5, "axes.linewidth": 1.4,
         "axes.spines.top": False, "axes.spines.right": False,
         "pdf.fonttype": 42, "ps.fonttype": 42, "svg.fonttype": "none",
         "svg.hashsalt": "oncotracer-paper-v1", "figure.facecolor": "white",
         "axes.facecolor": "white", "savefig.facecolor": "white",
         "text.usetex": False, "axes.titlesize": 12, "axes.titleweight": "bold"}


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _number(value, *, required=True):
    try:
        result = float(value)
        if math.isfinite(result):
            return result
    except (ValueError, TypeError):
        pass
    if required:
        raise OncoTracerError(f"Expected a finite plotted number, received {value!r}")
    return None


def _ordered(values):
    return list(dict.fromkeys(str(v) for v in values))


def _rows(panel, directory):
    path = None
    if "records" in panel:
        if "data" in panel:
            raise OncoTracerError("A panel must select either inline records or a data table")
        rows = panel["records"]
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise OncoTracerError("Panel records must be a list of objects")
    else:
        path = directory / panel.get("data", "")
        if not path.is_file():
            raise OncoTracerError(f"Missing paper panel table: {path}")
        with path.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle, delimiter="," if path.suffix.lower() == ".csv" else "\t")
            fields = reader.fieldnames
            if not fields or any(not field.strip() for field in fields) or len(set(fields)) != len(fields):
                raise OncoTracerError("Paper tables require unique, nonempty column names")
            rows = list(reader)
        if any(None in row or any(value is None for value in row.values()) for row in rows):
            raise OncoTracerError("Paper table rows must have the same number of fields as the header")
    filters = panel.get("filter", {})
    if not isinstance(filters, dict):
        raise OncoTracerError("Panel filter must be an object mapping columns to values")
    if any(key not in row for key in filters for row in rows):
        raise OncoTracerError("Panel filter references a missing column")
    rows = [row for row in rows if all(str(row.get(key, "")) in
            ([str(v) for v in value] if isinstance(value, list) else [str(value)])
            for key, value in filters.items())]
    return rows, path


def _color(panel, label, index):
    return panel.get("colors", {}).get(str(label), PALETTE[index % len(PALETTE)])


def _bar(ax, rows, panel, np):
    category, value, group = panel.get("category", "category"), panel.get("value", "value"), panel.get("group")
    categories = panel.get("order") or _ordered(row[category] for row in rows)
    groups = panel.get("group_order") or (_ordered(row[group] for row in rows) if group else [""])
    horizontal = panel.get("orientation", "vertical") == "horizontal"
    stacked = panel.get("stacked", False)
    positions = np.arange(len(categories), dtype=float)
    base = np.zeros(len(categories))
    width = 0.65 if stacked or not group else 0.76 / len(groups)
    values = {}
    for row in rows:
        key = (str(row[category]), str(row[group]) if group else "")
        if key in values:
            raise OncoTracerError(f"Duplicate bar cell {key}; aggregate explicitly in the analysis script")
        v = _number(row[value])
        if v < 0:
            raise OncoTracerError("Descriptive bar values must be nonnegative")
        values[key] = v
    for i, g in enumerate(groups):
        heights = np.array([values.get((str(c), str(g)), 0.0) for c in categories])
        offset = 0 if stacked or not group else (i - (len(groups) - 1) / 2) * width
        colors = _color(panel, g, i) if group else [_color(panel, c, j) for j, c in enumerate(categories)]
        kwargs = dict(color=colors, edgecolor="#262626", linewidth=0.65, label=g or None, zorder=3)
        bars = (ax.barh(positions + offset, heights, height=width, left=base if stacked else 0, **kwargs)
                if horizontal else ax.bar(positions + offset, heights, width=width, bottom=base if stacked else 0, **kwargs))
        if panel.get("show_values", True):
            for j, (bar, h) in enumerate(zip(bars, heights)):
                if h <= 0:
                    continue
                txt = f"{h:g}"
                if horizontal:
                    ax.annotate(txt, (float(base[j] if stacked else 0) + h / 2 if stacked else h,
                                      bar.get_y() + bar.get_height() / 2),
                                xytext=(0 if stacked else 4, 0), textcoords="offset points",
                                ha="center" if stacked else "left", va="center", fontsize=9.2, fontweight="bold")
                else:
                    ax.annotate(txt, (bar.get_x() + bar.get_width() / 2,
                                     float(base[j] if stacked else 0) + h / 2 if stacked else h),
                                xytext=(0, 0 if stacked else 4), textcoords="offset points",
                                ha="center", va="center" if stacked else "bottom", fontsize=9.2, fontweight="bold")
        if stacked:
            base += heights
    if horizontal:
        ax.set_yticks(positions, categories)
        ax.invert_yaxis()
        ax.grid(axis="x", color="#dddddd", linewidth=0.7, zorder=0)
        ax.margins(x=0.18)
    else:
        ax.set_xticks(positions, categories, rotation=panel.get("rotation", 0))
        ax.grid(axis="y", color="#dddddd", linewidth=0.7, zorder=0)
        ax.margins(y=0.18)
    if group:
        ax.legend(frameon=False, fontsize=8.5, loc=panel.get("legend_loc", "upper right"),
                  ncol=panel.get("legend_columns", 1))


def _distribution(ax, rows, panel, np):
    category, value = panel.get("category", "category"), panel.get("value", "value")
    categories = panel.get("order") or _ordered(row[category] for row in rows)
    rng = np.random.default_rng(panel.get("seed", 20260918))
    labels = []
    n_plotted = 0
    for i, label in enumerate(categories):
        group = [r for r in rows if str(r[category]) == str(label)]
        numbers = [_number(r.get(value), required=False) for r in group]
        good = np.asarray([v for v in numbers if v is not None], dtype=float)
        if panel.get("yscale") == "log" and any(good <= 0):
            raise OncoTracerError("Log-scale distributions require positive values; explicitly flag and filter invalid rows")
        labels.append(str(label) + (f"\nn = {len(good)}" if panel.get("show_n", True) else ""))
        if not len(good):
            continue
        n_plotted += len(good)
        x = i + rng.uniform(-0.18, 0.18, len(good))
        ax.scatter(x, good, s=panel.get("point_size", 30), color=_color(panel, label, i),
                   edgecolors="#202020", linewidths=0.55, alpha=0.88, zorder=3)
        low, med, high = np.percentile(good, [25, 50, 75])
        ax.errorbar(i + 0.30, med, yerr=[[med - low], [high - med]], fmt="D", color="black",
                    mfc="white", mec="black", mew=1.2, ms=6.0, capsize=4, elinewidth=1.6, zorder=5)
    if not n_plotted:
        raise OncoTracerError("Distribution has no finite measurements")
    ax.set_xticks(range(len(categories)), labels, rotation=panel.get("rotation", 0))
    ax.set_xlim(-0.55, len(categories) - 0.4)
    ax.grid(axis="y", color="#dddddd", linewidth=0.7, zorder=0)


def _scatter(ax, rows, panel, np):
    xcol, ycol, group = panel.get("x", "x"), panel.get("y", "y"), panel.get("group")
    groups = panel.get("group_order") or (_ordered(r[group] for r in rows) if group else [""])
    markers = ("o", "^", "s", "D", "v")
    n_plotted = 0
    for i, label in enumerate(groups):
        points = [(_number(r.get(xcol), required=False), _number(r.get(ycol), required=False))
                  for r in rows if not group or str(r[group]) == str(label)]
        points = [(x, y) for x, y in points if x is not None and y is not None]
        if not points:
            continue
        n_plotted += len(points)
        x, y = np.asarray(points).T
        if ((panel.get("yscale") == "log" and any(y <= 0)) or
                (panel.get("xscale") == "log" and any(x <= 0))):
            raise OncoTracerError("Log-scale scatter requires positive plotted values")
        ax.scatter(x, y, s=panel.get("point_size", 35), marker=markers[i % len(markers)],
                   color=_color(panel, label, i), edgecolors="#222222", linewidths=0.55,
                   alpha=0.88, label=(f"{label} (n = {len(points)})" if panel.get("show_n", True) else str(label)) if group else None, zorder=3)
    if not n_plotted:
        raise OncoTracerError("Scatter has no paired finite measurements")
    for ref in panel.get("vlines", []):
        ax.axvline(float(ref), color="#777777", linestyle="--", linewidth=1.0, zorder=1)
    ax.grid(axis="y", color="#dddddd", linewidth=0.7, zorder=0)
    if group:
        ax.legend(frameon=False, fontsize=8.5, loc=panel.get("legend_loc", "best"))


def _line(ax, rows, panel, np):
    group = panel.get("group")
    groups = panel.get("group_order") or (_ordered(r[group] for r in rows) if group else [""])
    for i, label in enumerate(groups):
        sub = sorted([r for r in rows if not group or str(r[group]) == str(label)], key=lambda r: _number(r[panel.get("x", "x")]))
        x = [_number(r[panel.get("x", "x")]) for r in sub]
        y = [_number(r[panel.get("y", "y")]) for r in sub]
        if ((panel.get("xscale") == "log" and any(v <= 0 for v in x)) or
                (panel.get("yscale") == "log" and any(v <= 0 for v in y))):
            raise OncoTracerError("Log-scale lines require positive plotted values")
        ax.plot(x, y, "o-", ms=5, linewidth=1.6, color=_color(panel, label, i), label=label or None)
        if panel.get("lower") and panel.get("upper"):
            lo = [_number(r[panel["lower"]]) for r in sub]
            hi = [_number(r[panel["upper"]]) for r in sub]
            if any(not a <= b <= c for a, b, c in zip(lo, y, hi)):
                raise OncoTracerError("Intervals must enclose the estimate")
            if panel.get("yscale") == "log" and any(v <= 0 for v in lo):
                raise OncoTracerError("Log-scale intervals require positive lower bounds")
            ax.fill_between(x, lo, hi, color=_color(panel, label, i), alpha=0.16, linewidth=0)
    ax.grid(axis="y", color="#dddddd", linewidth=0.7)
    if group:
        ax.legend(frameon=False, fontsize=8.5)


def _matrix(ax, rows, panel, np):
    rcol, ccol, vcol = panel.get("row", "row"), panel.get("column", "column"), panel.get("value", "value")
    rlabels = panel.get("row_order") or _ordered(r[rcol] for r in rows)
    clabels = panel.get("column_order") or _ordered(r[ccol] for r in rows)
    values = np.full((len(rlabels), len(clabels)), np.nan)
    seen = set()
    for row in rows:
        key = (str(row[rcol]), str(row[ccol]))
        if key in seen:
            raise OncoTracerError("Matrix cells must be unique")
        seen.add(key)
        values[rlabels.index(key[0]), clabels.index(key[1])] = _number(row[vcol], required=False)
    if not np.isfinite(values).any():
        raise OncoTracerError("Matrix has no finite measurements")
    ax.imshow(values, cmap=panel.get("cmap", "Blues"), aspect="auto", vmin=panel.get("vmin", 0), vmax=panel.get("vmax"))
    top = np.nanmax(values) if np.isfinite(values).any() else 1
    for i in range(len(rlabels)):
        for j in range(len(clabels)):
            v = values[i, j]
            ax.text(j, i, f"{v:g}" if np.isfinite(v) else "NA", ha="center", va="center",
                    fontsize=11, fontweight="bold", color="white" if v > 0.6 * top else "#202020")
    ax.set_xticks(range(len(clabels)), clabels, rotation=panel.get("rotation", 0))
    ax.set_yticks(range(len(rlabels)), rlabels)
    ax.tick_params(length=0)
    for spine in ax.spines.values():
        spine.set_visible(False)


def _render_paper_report(manifest_path: Path, outdir: Path) -> Path:
    """Render versioned table-based figures and return an English HTML index."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np
    except ImportError as error:
        raise OncoTracerError("Paper panels require matplotlib and numpy; install with pip install 'oncotracer[paper]' or python -m pip install -e '.[paper]'") from error
    manifest_path, outdir = Path(manifest_path).expanduser().resolve(), Path(outdir).expanduser().absolute()
    try:
        spec = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise OncoTracerError(f"Cannot read paper manifest: {error}") from error
    if not isinstance(spec, dict):
        raise OncoTracerError("Paper manifest must be a JSON object")
    if spec.get("schema") != SCHEMA or spec.get("language") != "en":
        raise OncoTracerError(f"Paper manifest requires schema={SCHEMA!r} and language='en'")
    figures = spec.get("figures")
    if not isinstance(figures, list) or not figures:
        raise OncoTracerError("Paper manifest must contain at least one figure")
    for parent in (*reversed(outdir.parents), outdir):
        if parent.is_symlink():
            raise OncoTracerError("Paper output paths must not contain symlinks")
    planned = []
    stems = set()
    functions = {"bar": _bar, "distribution": _distribution, "scatter": _scatter, "line": _line, "matrix": _matrix}
    for figure in figures:
        if not isinstance(figure, dict):
            raise OncoTracerError("Every figure must be an object")
        stem = figure.get("stem", figure.get("id", ""))
        if not isinstance(stem, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", stem) or stem in stems:
            raise OncoTracerError("Figure stems must be unique safe filenames")
        stems.add(stem)
        evidence = figure.get("evidence_status")
        if evidence not in {"descriptive", "concordance", "independent_validation", "illustrative"}:
            raise OncoTracerError("Every figure must declare its evidence_status")
        truth = figure.get("truth_source", "")
        if not isinstance(truth, str):
            raise OncoTracerError("truth_source must describe the independent evidence in text")
        figure["truth_source"] = truth.strip()
        if evidence == "independent_validation" and not figure["truth_source"]:
            raise OncoTracerError("Independent validation figures require an explicit truth_source")
        panels = figure.get("panels", [])
        if not isinstance(panels, list) or not panels:
            raise OncoTracerError("A figure must contain measured panels; empty placeholder figures are not rendered")
        prepared = []
        letters = set()
        for i, panel in enumerate(panels):
            if not isinstance(panel, dict):
                raise OncoTracerError("Every panel must be an object")
            letter = panel.get("letter", chr(65 + i))
            if not isinstance(letter, str) or not re.fullmatch(r"[A-Z]", letter) or letter in letters or panel.get("type") not in functions:
                raise OncoTracerError("Panel letters must be unique A-Z and panel type must be supported")
            letters.add(letter)
            rows, source = _rows(panel, manifest_path.parent)
            if not rows:
                raise OncoTracerError(f"Figure {stem} panel {letter} has no measured rows after filtering")
            prepared.append((panel, letter, rows, source))
        layout = figure.get("layout", {})
        if not isinstance(layout, dict):
            raise OncoTracerError("Figure layout must be an object")
        ncols = int(layout.get("cols", 2))
        if ncols <= 0:
            raise OncoTracerError("Figure layout columns must be positive")
        nrows = int(layout.get("rows", math.ceil(len(panels) / ncols)))
        if nrows <= 0 or ncols <= 0 or nrows * ncols < len(panels):
            raise OncoTracerError("Figure layout has insufficient panel slots")
        planned.append((figure, stem, prepared, nrows, ncols))
    outdir.mkdir(parents=True, exist_ok=True)
    for name in ("panels", "legends", "source_data", "provenance"):
        directory = outdir / name
        if directory.is_symlink():
            raise OncoTracerError(f"Refusing symlink output directory: {directory}")
        directory.mkdir(exist_ok=True)
    marker = outdir / "provenance/render_manifest.json"
    old = json.loads(marker.read_text()) if marker.exists() else {}
    if old and old.get("schema") != SCHEMA:
        raise OncoTracerError("Existing paper rendering provenance belongs to another producer")
    outputs, input_records, rendered = [], [], []
    def target(relative):
        path = outdir / relative
        if path.is_symlink():
            raise OncoTracerError(f"Refusing symlink output: {path}")
        if path.exists() and relative not in old.get("outputs", {}):
            raise OncoTracerError(f"Refusing to overwrite an unowned output: {path}")
        outputs.append(relative)
        return path
    try:
        with plt.rc_context(STYLE):
            for figure, stem, prepared, nrows, ncols in planned:
                layout = figure.get("layout", {})
                fig, axes = plt.subplots(nrows, ncols, figsize=(layout.get("width", 11.2), layout.get("height", 12.2)), squeeze=False)
                fig.subplots_adjust(left=layout.get("left", 0.10), right=layout.get("right", 0.97),
                                    bottom=layout.get("bottom", 0.08), top=layout.get("top", 0.91),
                                    hspace=layout.get("hspace", 0.60), wspace=layout.get("wspace", 0.45))
                if figure.get("show_title", True):
                    fig.suptitle(figure.get("title", stem), fontsize=15, fontweight="bold", x=0.10, ha="left", y=0.975)
                if figure.get("show_title", True) and figure.get("subtitle"):
                    fig.text(0.10, 0.943, figure["subtitle"], fontsize=10.5, color="#505050", va="top")
                used_axes = []
                for ax, (panel, letter, rows, source) in zip(axes.flat, prepared):
                    used_axes.append((ax, letter))
                    functions[panel["type"]](ax, rows, panel, np)
                    ax.set_title(panel.get("title", ""), loc="left", pad=15)
                    ax.text(-0.19, 1.085, letter, transform=ax.transAxes, fontsize=20, fontweight="bold", va="top")
                    ax.set_xlabel(panel.get("xlabel", ""), labelpad=8)
                    ax.set_ylabel(panel.get("ylabel", ""), labelpad=8)
                    ax.tick_params(labelsize=9.5)
                    ax.set_axisbelow(True)
                    for key in ("xscale", "yscale", "xlim", "ylim"):
                        if key in panel:
                            getattr(ax, "set_" + key)(panel[key])
                    if panel.get("note"):
                        ax.text(0, panel.get("note_y", -0.28), panel["note"], transform=ax.transAxes, fontsize=8.4, color="#555555", va="top")
                    table_rel = f"source_data/{stem}_{letter}.tsv"
                    table = target(table_rel)
                    with table.open("w", newline="", encoding="utf-8") as handle:
                        fields = list(dict.fromkeys(k for row in rows for k in row))
                        writer = csv.DictWriter(handle, fields, delimiter="\t")
                        writer.writeheader(); writer.writerows(rows)
                    input_records.append({"figure": stem, "panel": letter, "source": str(source) if source else "inline_manifest_records",
                                          "source_sha256": _sha(source) if source else _sha(manifest_path),
                                          "selected_rows": len(rows), "exported_table": table_rel})
                for ax in list(axes.flat)[len(prepared):]:
                    ax.set_axis_off()
                dpi = int(spec.get("dpi", 600))
                if not 72 <= dpi <= 1200:
                    raise OncoTracerError("Paper export dpi must be between 72 and 1200")
                for ext in ("pdf", "png", "svg"):
                    meta = {"CreationDate": None, "ModDate": None} if ext == "pdf" else {"Date": None} if ext == "svg" else None
                    fig.savefig(target(f"{stem}.{ext}"), dpi=dpi, metadata=meta)
                fig.canvas.draw()
                renderer = fig.canvas.get_renderer()
                for ax, letter in used_axes:
                    bbox = ax.get_tightbbox(renderer).transformed(fig.dpi_scale_trans.inverted()).expanded(1.035, 1.05)
                    for ext in ("pdf", "png", "svg"):
                        meta = {"CreationDate": None, "ModDate": None} if ext == "pdf" else {"Date": None} if ext == "svg" else None
                        fig.savefig(target(f"panels/{stem}_{letter}.{ext}"), bbox_inches=bbox, dpi=dpi, metadata=meta)
                plt.close(fig)
                caption = figure.get("caption", "")
                evidence_note = "Evidence status: " + figure["evidence_status"].replace("_", " ") + "."
                if figure["truth_source"]:
                    evidence_note += " Truth source: " + figure["truth_source"]
                target(f"legends/{stem}.md").write_text(f"# {figure.get('title', stem)}\n\n{caption}\n\n{evidence_note}\n", encoding="utf-8")
                for panel, letter, _, _ in prepared:
                    target(f"legends/{stem}_{letter}.txt").write_text(panel.get("caption", panel.get("title", "")) + "\n", encoding="utf-8")
                rendered.append({"stem": stem, "title": figure.get("title", stem), "caption": caption,
                                 "evidence_status": figure["evidence_status"], "truth_source": figure["truth_source"],
                                 "evidence_note": evidence_note, "panels": len(prepared)})
    except (KeyError, ValueError, TypeError, OSError, OncoTracerError) as error:
        plt.close("all")
        raise OncoTracerError(f"Invalid paper panel data or specification: {error}") from error
    index = target("index.html")
    sections = []
    for item in rendered:
        stem = html.escape(item["stem"])
        sections.append(f'<section><h2>{html.escape(item["title"])}</h2><p class="kind">{html.escape(item["evidence_status"].replace("_", " "))}</p>'
                        f'<img src="{stem}.png" alt="{html.escape(item["title"])}"><p class="links"><a href="{stem}.pdf">PDF</a> · '
                        f'<a href="{stem}.svg">SVG</a> · <a href="{stem}.png">PNG</a> · <a href="legends/{stem}.md">Caption</a></p>'
                        f'<p>{html.escape(item["caption"])}</p><p>{html.escape(item["evidence_note"])}</p></section>')
    index.write_text('<!doctype html><html lang="en"><meta charset="utf-8"><title>OncoTracer paper panels</title>'
                     '<style>body{font:16px/1.6 system-ui,sans-serif;color:#20242a;max-width:1150px;margin:45px auto;padding:0 24px;background:#fff}'
                     'h1,h2{line-height:1.2}header{border-bottom:2px solid #20242a;padding-bottom:20px}section{margin:45px 0}img{width:100%;height:auto}'
                     'a{color:#216da0}.kind{color:#666;text-transform:uppercase;font-size:12px;letter-spacing:.12em}.links{font-weight:600}</style>'
                     '<header><p class="kind">OncoTracer · manuscript figures</p><h1>' + html.escape(spec.get("title", "Paper panels")) +
                     '</h1><p>English figures with source tables, captions and reproducible plotting provenance.</p></header>' + ''.join(sections) +
                     '<footer><a href="provenance/render_manifest.json">Rendering provenance</a></footer></html>\n', encoding="utf-8")
    result = {"schema": SCHEMA, "language": "en", "manifest_sha256": _sha(manifest_path),
              "renderer_sha256": _sha(Path(__file__)), "matplotlib": matplotlib.__version__, "numpy": np.__version__,
              "style": STYLE, "dpi": int(spec.get("dpi", 600)), "figures": rendered, "inputs": input_records,
              "outputs": {relative: _sha(outdir / relative) for relative in sorted(outputs)}}
    marker.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return index


def generate_paper_report(manifest_path: Path, outdir: Path) -> Path:
    """Stage a complete report before replacing unchanged, owned artifacts.

    Unrelated files (such as manuscript scripts and raw tables) are preserved.
    A rendering or validation failure leaves an earlier report untouched.
    """
    destination = Path(outdir).expanduser().absolute()
    for parent in (*reversed(destination.parents), destination):
        if parent.is_symlink():
            raise OncoTracerError("Paper output paths must not contain symlinks")
    destination.parent.mkdir(parents=True, exist_ok=True)
    marker_rel = "provenance/render_manifest.json"
    marker = destination / marker_rel
    try:
        if marker.is_symlink():
            raise OncoTracerError("Refusing symlink rendering provenance")
        previous = json.loads(marker.read_text()) if marker.exists() else {}
        if not isinstance(previous, dict):
            raise OncoTracerError("Existing rendering provenance must be an object")
        if marker.exists() and previous.get("schema") != SCHEMA:
            raise OncoTracerError("Existing rendering provenance belongs to another producer")
        owned = previous.get("outputs", {})
        if not isinstance(owned, dict):
            raise OncoTracerError("Existing rendering provenance outputs must be an object")
        with tempfile.TemporaryDirectory(prefix=".paper-render-", dir=destination.parent) as tmp:
            stage = Path(tmp) / "new"
            _render_paper_report(manifest_path, stage)
            new = json.loads((stage / marker_rel).read_text())
            new_files = set(new["outputs"]) | {marker_rel}
            all_files = new_files | set(owned)
            for relative in all_files:
                rel = Path(relative)
                if rel.is_absolute() or ".." in rel.parts:
                    raise OncoTracerError("Invalid artifact path in rendering provenance")
                target = destination / rel
                for component in (target, *target.parents):
                    if component == destination.parent:
                        break
                    if component.is_symlink():
                        raise OncoTracerError(f"Refusing symlink output: {component}")
                if target.exists():
                    if not target.is_file():
                        raise OncoTracerError(f"Output is not a regular file: {target}")
                    if relative != marker_rel and relative not in owned:
                        raise OncoTracerError(f"Refusing to overwrite an unowned output: {target}")
                    if relative in owned and _sha(target) != owned[relative]:
                        raise OncoTracerError(f"Generated output has been edited; preserve it before rerendering: {target}")
            backup = Path(tmp) / "previous"
            moved, installed = [], []
            try:
                for relative in sorted(all_files):
                    target = destination / relative
                    if target.exists():
                        saved = backup / relative
                        saved.parent.mkdir(parents=True, exist_ok=True)
                        os.replace(target, saved)
                        moved.append(relative)
                for relative in sorted(new_files - {marker_rel}) + [marker_rel]:
                    target = destination / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(stage / relative, target)
                    installed.append(relative)
            except BaseException:
                for relative in installed:
                    (destination / relative).unlink()
                for relative in moved:
                    os.replace(backup / relative, destination / relative)
                raise
    except (OSError, ValueError, TypeError, KeyError, ZeroDivisionError) as error:
        raise OncoTracerError(f"Cannot generate paper report: {error}") from error
    return destination / "index.html"
