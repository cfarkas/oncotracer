#!/usr/bin/env python3
"""Rewrite only run/cache destinations in one flat OncoTracer YAML."""
from __future__ import annotations
import argparse
from pathlib import Path


def scalar(value: str) -> str:
    return value.strip()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--lpwgs-root", type=Path, required=True)
    parser.add_argument("--outdir", type=Path, required=True)
    args = parser.parse_args()
    values: dict[str, str] = {}
    order: list[str] = []
    for raw in args.source.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, value = raw.split(":", 1)
        key = key.strip()
        if key not in values:
            order.append(key)
        values[key] = scalar(value)
    values["lpwgs_root"] = str(args.lpwgs_root.resolve())
    values["outdir"] = str(args.outdir.resolve())
    values["force"] = "true"
    for key in ("lpwgs_root", "outdir", "force"):
        if key not in order:
            order.append(key)
    args.destination.parent.mkdir(parents=True, exist_ok=True)
    args.destination.write_text("\n".join(f"{key}: {values[key]}" for key in order) + "\n", encoding="utf-8")
    print(args.destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
