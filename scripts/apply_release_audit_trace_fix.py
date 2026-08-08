#!/usr/bin/env python3
"""Align the sealed parity audit with the resume-safe nested trace contract."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_exact(path: Path, old: str, new: str, *, count: int = 1) -> None:
    text = path.read_text(encoding="utf-8")
    observed = text.count(old)
    if observed != count:
        raise SystemExit(
            f"{path}: expected {count} exact occurrence(s), observed {observed}"
        )
    path.write_text(text.replace(old, new, count), encoding="utf-8")


def patch_audit() -> None:
    path = ROOT / "tests" / "parity_audit.py"
    replace_exact(
        path,
        '''from verify_nested_samurai import (  # noqa: E402
    CONTRACTS,
    REQUIRED_TRACE_COLUMNS,
    normalize_container,
    normalize_process,
    parse_compat,
)
''',
        '''from verify_nested_samurai import (  # noqa: E402
    CONTRACTS,
    ONT_RESUME_TRACE_IMAGES,
    ONT_RESUME_TRACE_PROCESSES,
    Contract,
    evaluate_trace,
    parse_compat,
)
''',
    )
    replace_exact(
        path,
        '''def verify_trace(path: Path, contract, pins: dict[str, str]) -> None:
    with require_file(path).open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle, delimiter="\\t"))
    if not rows or len(rows) != contract.expected_rows:
        raise AuditError(f"trace row count mismatch for {contract.label}: {len(rows)}")
    if not REQUIRED_TRACE_COLUMNS <= set(rows[0]):
        raise AuditError(f"trace lacks required columns: {path}")
    if any(
        row["status"] not in {"COMPLETED", "CACHED"}
        or row["exit"] != "0"
        or not row["container"].strip()
        for row in rows
    ):
        raise AuditError(f"failed or uncontainerized task in {path}")
    processes = {normalize_process(row["name"]) for row in rows}
    images = {normalize_container(row["container"], pins) for row in rows}
    if processes != set(contract.processes):
        raise AuditError(f"trace process set mismatch for {contract.label}")
    if images != set(contract.images):
        raise AuditError(f"trace image set mismatch for {contract.label}")
''',
        '''def verify_trace(path: Path, contract: Contract, pins: dict[str, str]) -> str:
    """Independently re-evaluate the selected combined trace and evidence mode."""
    ok, reason, _rows, _images = evaluate_trace(path, contract, pins)
    if ok:
        return "complete-combined-trace"

    if contract.label == "quickstart1-ont":
        resume_contract = Contract(
            label=contract.label,
            root_arg=contract.root_arg,
            expected_rows=4,
            processes=ONT_RESUME_TRACE_PROCESSES,
            images=ONT_RESUME_TRACE_IMAGES,
            require_ichorcna_compat=True,
        )
        resume_ok, resume_reason, _resume_rows, _resume_images = evaluate_trace(
            path, resume_contract, pins
        )
        if resume_ok:
            return "exact-ont-final-resume-trace"
        reason = f"full={reason}; exact-resume={resume_reason}"

    raise AuditError(
        f"selected nested trace does not satisfy {contract.label}: {reason}: {path}"
    )
''',
    )
    replace_exact(
        path,
        '''        path = context / filename
        verify_trace(path, contract, pins)
        selected = selection_by_run.get(contract.label)
        if selected is None or selected[4] != filename or selected[5] != sha256(path):
            raise AuditError(f"selected trace manifest mismatch: {contract.label}")
        if int(selected[1]) < 1 or int(selected[2]) < 1:
            raise AuditError(f"selected trace counts invalid: {contract.label}")
''',
        '''        path = context / filename
        evidence_mode = verify_trace(path, contract, pins)
        selected = selection_by_run.get(contract.label)
        if selected is None or selected[4] != filename or selected[5] != sha256(path):
            raise AuditError(f"selected trace manifest mismatch: {contract.label}")
        if int(selected[1]) < 1 or int(selected[2]) < 1:
            raise AuditError(f"selected trace counts invalid: {contract.label}")
        if not selected[3].startswith(evidence_mode + ":"):
            raise AuditError(
                f"selected trace evidence mode mismatch for {contract.label}: "
                f"expected {evidence_mode!r}, observed {selected[3]!r}"
            )
''',
    )


def patch_tests() -> None:
    path = ROOT / "tests" / "test_compare_native_parity.py"
    insertion = '''
    def test_sealed_audit_accepts_only_the_exact_ont_resume_contract(self) -> None:
        verify_spec = importlib.util.spec_from_file_location(
            "verify_nested_samurai", ROOT / "tests" / "verify_nested_samurai.py"
        )
        self.assertIsNotNone(verify_spec)
        self.assertIsNotNone(verify_spec.loader)
        verify_module = importlib.util.module_from_spec(verify_spec)
        sys.path.insert(0, str(ROOT / "tests"))
        sys.modules[verify_spec.name] = verify_module
        try:
            verify_spec.loader.exec_module(verify_module)
            audit_spec = importlib.util.spec_from_file_location(
                "parity_audit", ROOT / "tests" / "parity_audit.py"
            )
            self.assertIsNotNone(audit_spec)
            self.assertIsNotNone(audit_spec.loader)
            audit_module = importlib.util.module_from_spec(audit_spec)
            sys.modules[audit_spec.name] = audit_module
            audit_spec.loader.exec_module(audit_module)
        finally:
            sys.modules.pop("parity_audit", None)
            sys.modules.pop(verify_spec.name, None)
            sys.path.pop(0)

        full_contract = verify_module.CONTRACTS["quickstart1"][1]
        process_rows = (
            (
                "SAMURAI:SAMTOOLS_INDEX",
                "quay.io/biocontainers/samtools:1.22.1--h96c455f_0",
            ),
            (
                "SAMURAI:BAM_QC_PICARD:PICARD_COLLECTMULTIPLEMETRICS",
                "community.wave.seqera.io/library/picard:3.4.0--e9963040df0a9bf6",
            ),
            (
                "SAMURAI:BAM_QC_PICARD:PICARD_COLLECTWGSMETRICS",
                "community.wave.seqera.io/library/picard:3.4.0--e9963040df0a9bf6",
            ),
            (
                "SAMURAI:LIQUID_BIOPSY:ICHORCNA:HMMCOPY_READCOUNTER_ICHORCNA",
                "community.wave.seqera.io/library/hmmcopy_samtools:875db3767c6d4ea2",
            ),
        )
        pins = {image: "sha256:" + format(index + 1, "064x") for index, image in enumerate(full_contract.images)}

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            exact = root / "exact.tsv"
            with exact.open("w", newline="") as handle:
                writer = csv.DictWriter(
                    handle,
                    ["task_id", "hash", "name", "status", "exit", "container"],
                    delimiter="\\t",
                )
                writer.writeheader()
                for index, (process, image) in enumerate(process_rows, start=1):
                    writer.writerow(
                        {
                            "task_id": index,
                            "hash": f"hash-{index}",
                            "name": f"DINCALCILAB_SAMURAI:{process} (DRR165691)",
                            "status": "COMPLETED",
                            "exit": "0",
                            "container": image,
                        }
                    )
            self.assertEqual(
                audit_module.verify_trace(exact, full_contract, pins),
                "exact-ont-final-resume-trace",
            )

            incomplete = root / "incomplete.tsv"
            rows = list(csv.DictReader(exact.open(newline=""), delimiter="\\t"))[:-1]
            with incomplete.open("w", newline="") as handle:
                writer = csv.DictWriter(handle, rows[0].keys(), delimiter="\\t")
                writer.writeheader()
                writer.writerows(rows)
            with self.assertRaises(audit_module.AuditError):
                audit_module.verify_trace(incomplete, full_contract, pins)
'''
    replace_exact(
        path,
        '\n\nif __name__ == "__main__":\n    unittest.main()\n',
        insertion + '\n\nif __name__ == "__main__":\n    unittest.main()\n',
    )


def main() -> None:
    patch_audit()
    patch_tests()
    print("Applied guarded parity-audit nested-trace fix")


if __name__ == "__main__":
    main()
