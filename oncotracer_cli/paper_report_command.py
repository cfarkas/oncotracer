"""Render manuscript figures from an explicit manifest of saved evidence."""
from __future__ import annotations

from pathlib import Path

from .runtime import require_file


def command_paper_report(args) -> int:
    manifest = require_file(Path(args.manifest).expanduser().absolute(), "paper report manifest")
    output = Path(args.outdir).expanduser().absolute()
    # Plotting dependencies are optional: normal setup/run/help never imports them.
    from .paper_report import generate_paper_report

    index = generate_paper_report(manifest, output)
    print(f"Paper report: {index}")
    return 0


def add_paper_report_command(subparsers) -> None:
    parser = subparsers.add_parser(
        "paper-report",
        help="Render manuscript figures from saved evidence (also --paper_report or --paper-report)",
        description="Create English manuscript figures and a report from an explicit evidence manifest.",
    )
    parser.add_argument(
        "--manifest", "--paper-manifest", "--paper_manifest",
        dest="manifest", required=True,
        help="JSON manifest identifying cohort data and saved results to plot",
    )
    parser.add_argument("--outdir", required=True, help="Directory for the paper report and figure exports")
    parser.set_defaults(func=command_paper_report)
