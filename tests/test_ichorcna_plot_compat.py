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
        self.assertIn("source(", native)
        self.assertIn('"ichorcna_plot_compat.R"', native)
        self.assertIn(".ichorcna_plot_compat.tsv", native)
        self.assertIn("compat_path", native)

    def test_frozen_v1_profile_uses_samurai_expected_startup_path(self) -> None:
        root = Path(__file__).resolve().parents[1]
        driver = (root / "scripts/ci_native_parity.sh").read_text(encoding="utf-8")

        self.assertIn(
            "-v $REPO/bin/scripts/v1_ichorcna_profile.R:/.Rprofile:ro",
            driver,
        )
        self.assertNotIn("-e R_PROFILE_USER=", driver)


if __name__ == "__main__":
    unittest.main()
