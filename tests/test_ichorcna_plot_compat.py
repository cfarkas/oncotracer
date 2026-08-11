import unittest
from pathlib import Path


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


if __name__ == "__main__":
    unittest.main()
