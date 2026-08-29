import subprocess
import tempfile
import unittest
from pathlib import Path


def extract_native_marker_block(driver: str) -> str:
    start = driver.index("  mapfile -t native_markers < <(find")
    anchor = '  cp "${native_markers[0]}" "$CONTEXT/v2-ont-ichorcna-plot-compat.tsv"\n'
    end = driver.index(anchor, start) + len(anchor)
    return driver[start:end]


def run_native_marker_block(
    driver: str, markers: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        context = root / "context"
        context.mkdir()
        for relative, payload in markers.items():
            marker = root / "v2" / "ont" / relative
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text(payload, encoding="utf-8")
        script = (
            "set -Eeuo pipefail\n"
            f'TEST_ROOT={root}\n'
            f'CONTEXT={context}\n'
            + extract_native_marker_block(driver)
            + 'cat "$CONTEXT/v2-ont-ichorcna-plot-compat.tsv"\n'
        )
        return subprocess.run(
            ["bash", "-c", script],
            capture_output=True,
            text=True,
            check=False,
        )


class IchorCnaPlotCompatibilityTests(unittest.TestCase):
    def test_compatibility_shim_is_packaged_and_auditable(self) -> None:
        root = Path(__file__).resolve().parents[1]
        helper = (root / "bin/scripts/ichorcna_plot_compat.R").read_text(encoding="utf-8")
        native = (root / "bin/scripts/native_ichorcna.R").read_text(encoding="utf-8")

        self.assertIn("utils::assignInNamespace", helper)
        self.assertIn('node[["na.rm"]] <- TRUE', helper)
        self.assertIn("target_quantile_calls", helper)
        self.assertIn('zero_median_plot_guard = "placeholder"', helper)
        self.assertIn("median_reads == 0", helper)
        self.assertIn("Correction diagnostic unavailable:", helper)
        self.assertIn("No CNA values were changed.", helper)
        self.assertIn("source(", native)
        self.assertIn('"ichorcna_plot_compat.R"', native)
        self.assertIn(".ichorcna_plot_compat.tsv", native)
        self.assertIn("compat_path", native)
        for expression in (
            'chrTrain = "paste0(\'chr\', c(1:22))"',
            'chrs = "paste0(\'chr\', c(1:22))"',
            'chrNormalize = "paste0(\'chr\', c(1:22))"',
            'plotYLim = "c(-2, 4)"',
            'normal = "c(0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 0.99)"',
            'ploidy = "c(2, 3, 4, 5)"',
            'scStates = "c()"',
        ):
            self.assertIn(expression, native)

    def test_frozen_v1_profile_uses_samurai_expected_startup_path(self) -> None:
        root = Path(__file__).resolve().parents[1]
        driver = (root / "scripts/ci_native_parity.sh").read_text(encoding="utf-8")
        server_driver = (root / "scripts/validate_v2_release.sh").read_text(
            encoding="utf-8"
        )

        self.assertIn(
            "-v $REPO/bin/scripts/v1_ichorcna_profile.R:/.Rprofile:ro",
            driver,
        )
        self.assertNotIn("-e R_PROFILE_USER=", driver)
        self.assertIn(
            "-v $REPOSITORY_ROOT/bin/scripts/v1_ichorcna_profile.R:/.Rprofile:ro",
            server_driver,
        )
        self.assertIn(
            "from verify_nested_samurai import find_compat_marker",
            server_driver,
        )
        self.assertIn('"ichorcna_plot_compat": compatibility', server_driver)
        self.assertIn('"task_hash": task_hash', server_driver)
        self.assertIn('"relative_path": marker_relative.as_posix()', server_driver)

    def test_native_ont_marker_evidence_binds_to_content_not_layout(self) -> None:
        root = Path(__file__).resolve().parents[1]
        driver = (root / "scripts/ci_native_parity.sh").read_text(encoding="utf-8")
        marker = (
            "key\tvalue\n"
            "schema\toncotracer-ichorcna-plot-compat-v1\n"
            "status\tpatched\n"
        )
        published = "01_samurai_ont/results/ichorcna/DRR165691.ichorcna_plot_compat.tsv"
        nested = (
            "01_samurai_ont/results/ichorcna/DRR165691/"
            "DRR165691.ichorcna_plot_compat.tsv"
        )

        # The native caller publishes per-sample files to the results root, so
        # the one ONT marker is legitimately discovered twice.
        duplicated = run_native_marker_block(
            driver, {published: marker, nested: marker}
        )
        self.assertEqual(duplicated.returncode, 0, duplicated.stderr)
        self.assertEqual(duplicated.stdout, marker)

        missing = run_native_marker_block(driver, {})
        self.assertEqual(missing.returncode, 1)
        self.assertIn("missing native ichorCNA plot-compat marker", missing.stderr)

        divergent = run_native_marker_block(
            driver, {published: marker, nested: marker + "status\tunpatched\n"}
        )
        self.assertEqual(divergent.returncode, 1)
        self.assertIn(
            "divergent native ichorCNA plot-compat markers", divergent.stderr
        )


if __name__ == "__main__":
    unittest.main()
