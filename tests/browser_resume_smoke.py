"""Optional Firefox check of resume and model controls using synthetic files only.

Requires Playwright and its Firefox browser. No model download or analysis runs.
"""
import argparse
import json
from pathlib import Path
import sys
import threading
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.test_web import WebTests
from tests.test_sturgeon_resources import SturgeonResourcesTests
from oncotracer_cli.web import WebServer
from oncotracer_cli.output_safety import _create_owner, current_runtime_identity
from oncotracer_cli.sturgeon_resources import prepare_sturgeon
from oncotracer_cli.runtime import StageLedger, load_flat_yaml


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--firefox", required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    from playwright.sync_api import sync_playwright, expect
    fixture, assets = WebTests(), SturgeonResourcesTests()
    fixture.setUp()
    assets.setUp()
    server = None
    try:
        fixture.fastq("reads/case_R1.fastq.gz")
        fixture.fastq("reads/case_R2.fastq.gz")
        fixture.prepare()
        old = fixture.root / "project"
        (old / "results").mkdir()
        _create_owner(old / "results", current_runtime_identity())
        product = old / "results/completed.txt"
        product.write_text("synthetic completed output")
        StageLedger(old / "results/.oncotracer-native/state.json").complete("completed", "fixture", [product])
        moved = fixture.root / "moved-project"
        old.rename(moved)
        server = WebServer(0, fixture.state)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        url = server.origin + "/#" + fixture.state.token
        with patch("oncotracer_cli.sturgeon_resources.download", side_effect=assets.download), \
             patch("oncotracer_cli.sturgeon_resources.prepare_sturgeon",
                   side_effect=lambda **kw: prepare_sturgeon(cache=assets.cache, **kw)):
            with sync_playwright() as playwright:
                browser = playwright.firefox.launch(executable_path=args.firefox, headless=True)
                page = browser.new_page(viewport={"width": 1300, "height": 1000})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(url)
                page.wait_for_selector("main:not([inert])")
                page.locator("#resume-project").fill(str(moved))
                page.locator("#resume-project-button").click()
                expect(page.locator("#check-badge")).to_have_text("Resume checked", timeout=30000)
                assert page.locator("#review-card").is_visible()
                assert page.locator("#run").is_enabled()
                assert "case (cancer)" in page.locator("#check-messages").inner_text()
                assert fixture.state.job is None
                page.screenshot(path=str(args.output / "resume.png"), full_page=True)
                page.goto(url)
                page.wait_for_selector("main:not([inert])")
                moved_again = fixture.root / "moved-again"
                moved.rename(moved_again)
                page.locator("#choose-illumina").click()
                page.locator("#variant-fresh").click()
                page.locator("#input-folder").fill(str(fixture.root / "reads"))
                page.locator("#scan").click()
                page.wait_for_selector("#settings-card:not([hidden])")
                page.locator(".sample .type-select").first.select_option("cancer")
                page.locator("#project-mode").select_option("new")
                page.locator("#project-parent").fill(str(fixture.root))
                page.locator("#project-name").fill(moved_again.name)
                page.locator("#threads").fill("3")
                page.locator("#prepare").click()
                expect(page.locator("#check-badge")).to_have_text("Configuration checked", timeout=30000)
                expect(page.locator("#check-messages")).to_contain_text("1/1 recorded stages passed")
                assert load_flat_yaml(moved_again / "config/run.yml")["threads"] == 2
                assert page.locator("#run").is_enabled()
                assert fixture.state.job is None
                page.screenshot(path=str(args.output / "create-existing.png"), full_page=True)
                page.goto(url)
                page.wait_for_selector("main:not([inert])")
                page.locator("#choose-ont").click()
                page.locator("#variant-fresh").click()
                page.locator("#input-folder").fill(str(fixture.root / "reads"))
                # Show settings with synthetic discovered ONT reads through the actual scan route.
                ont = fixture.fastq("ont/fastq_pass/barcode01/read.fastq.gz").parents[1]
                fixture.fastq("ont/fastq_pass/barcode02/read.fastq.gz")
                page.locator("#input-folder").fill(str(ont))
                page.locator("#scan").click()
                page.wait_for_selector("#settings-card:not([hidden])")
                page.locator(".sample[data-id='0'] .type-select").select_option("cancer")
                page.locator(".sample[data-id='1'] .type-select").select_option("normal")
                assert page.locator("#caller option[value='ichorcna']").is_enabled()
                expect(page.locator("#caller")).to_have_value("ichorcna")
                page.locator("#caller").select_option("qdnaseq")
                expect(page.locator("#binsize")).to_have_value("100")
                page.locator("#caller").select_option("ichorcna")
                expect(page.locator("#binsize")).to_have_value("500")
                page.locator(".sample[data-id='1'] .type-select").select_option("")
                page.locator(".sample[data-id='1'] .type-select").select_option("normal")
                expect(page.locator("#caller")).to_have_value("ichorcna")
                page.locator("#project-parent").fill(str(fixture.root))
                page.locator("#project-name").fill("ont-with-controls")
                page.locator("#prepare").click()
                expect(page.locator("#check-badge")).to_have_text("Configuration checked", timeout=30000)
                ont_config = load_flat_yaml(fixture.root / "ont-with-controls/config/run.yml")
                assert ont_config["ont_caller"] == "ichorcna"
                assert ont_config["ont_normal_barcodes"] == "barcode02"
                assert fixture.state.job is None
                page.screenshot(path=str(args.output / "ont-ichorcna-controls.png"), full_page=True)
                page.locator("#analysis").select_option("methylation")
                page.locator("#classifier").select_option("sturgeon")
                page.locator("#prepare-sturgeon").click()
                expect(page.locator("#error")).to_contain_text("acknowledge")
                assert not assets.cache.exists()
                page.locator("#license").check()
                page.locator("#prepare-sturgeon").click()
                expect(page.locator("#sturgeon-download-status")).to_contain_text("Model archive", timeout=30000)
                assert page.locator("#sturgeon_model").input_value().endswith("general.zip")
                assert page.locator("#sturgeon_probes").input_value().endswith("probes_hg38.bed")
                assert fixture.state.job is None
                assert not errors, errors
                page.screenshot(path=str(args.output / "sturgeon.png"), full_page=True)
                browser.close()
        (args.output / "receipt.json").write_text(json.dumps({"passed": True,
            "analysis_started": False, "real_models_downloaded": False,
            "checks": ["moved project loaded through Firefox", "saved sample role retained",
                       "Run waits for user", "Create destination detects moved results and retains saved settings",
                       "intermediate integrity summary displayed", "unchecked license blocks download",
                       "ONT ichorCNA remains selectable with Normal samples and saves a valid configuration",
                       "explicit synthetic acknowledgement prepares fixture model/probes"]}, indent=2))
    finally:
        if server:
            server.shutdown()
            server.server_close()
        assets.doCleanups()
        fixture.doCleanups()


if __name__ == "__main__":
    main()
