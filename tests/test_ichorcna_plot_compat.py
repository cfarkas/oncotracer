from pathlib import Path


def test_native_ichorcna_compatibility_shim_is_packaged_and_auditable() -> None:
    root = Path(__file__).resolve().parents[1]
    helper = (root / "bin/scripts/ichorcna_plot_compat.R").read_text(encoding="utf-8")
    native = (root / "bin/scripts/native_ichorcna.R").read_text(encoding="utf-8")

    assert "assignInNamespace" in helper
    assert 'node[["na.rm"]] <- TRUE' in helper
    assert "target_quantile_calls" in helper
    assert 'source(' in native
    assert '"ichorcna_plot_compat.R"' in native
    assert '.ichorcna_plot_compat.tsv' in native
    assert 'compat_path' in native
