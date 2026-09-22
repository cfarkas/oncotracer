#!/usr/bin/env python3
"""Build the standalone, synthetic GitHub Pages demo from the real browser UI.

Run from any directory: python scripts/build_setup_demo.py
Use --check in CI to detect a stale generated page. No third-party packages needed.
"""
from __future__ import annotations

import argparse
import sys
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "docs/assets/setup-demo"
SOURCE = ROOT / "oncotracer_cli/web_ui.py"


def replace_once(page: str, old: str, new: str) -> str:
    if page.count(old) != 1:
        raise ValueError(f"Real UI anchor changed; review demo integration: {old[:90]!r}")
    return page.replace(old, new, 1)


def render() -> str:
    source = SOURCE.read_text(encoding="utf-8")
    sys.path.insert(0, str(ROOT))
    from oncotracer_cli.web_ui import PAGE
    page = PAGE
    from oncotracer_cli.variant_install_help import installation_guides
    mock = (ASSETS / 'mock-api.js').read_text().replace('__VARIANT_INSTALL_GUIDES__', json.dumps({backend: installation_guides(['annovar'], backend=backend, mode='illumina') for backend in ('host', 'docker')}))
    component = (ROOT / "oncotracer_cli/variant_resource_ui.py").read_text(encoding="utf-8")
    form = (ROOT / "oncotracer_cli/variant_form_ui.py").read_text(encoding="utf-8")
    digest = hashlib.sha256((source + component + form).encode()).hexdigest()
    policy = "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; connect-src 'none'; font-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'"
    page = replace_once(page, '<meta name="viewport" content="width=device-width,initial-scale=1">', '<meta name="viewport" content="width=device-width,initial-scale=1">\n<meta http-equiv="Content-Security-Policy" content="' + policy + '">\n<meta name="description" content="Try the OncoTracer setup interface with synthetic examples. Browser-only simulation; no files accessed or analysis run.">')
    page = replace_once(page, '<title>OncoTracer · Analysis setup</title>', '<title>OncoTracer · Interactive setup demo</title>')
    page = replace_once(page, '</style></head>', (ASSETS / 'demo.css').read_text() + '\n</style></head>')
    page = replace_once(page, 'Local analysis workspace', 'Synthetic setup demo')
    page = replace_once(page, '<main>', (ASSETS / 'guide.html').read_text() + '\n<main>')
    page = replace_once(page, '<h1>Configure and run an analysis</h1>', '<h2>Configure a synthetic analysis</h2>')
    page = replace_once(page, 'token=location.hash.slice(1)', "token='synthetic-demo-only'")
    page = replace_once(page, '<script>', '<script>\n' + mock + '\n</script>\n<script>')
    page = replace_once(page, '</script></body></html>', '</script>\n<script>\n' + (ASSETS / 'controls.js').read_text() + '\n</script></body></html>')
    page = page.replace('Browse folders on the computer running OncoTracer. Select the folder containing your FASTQs or barcode folders.', 'Browse the fictional /demo folders. These listings are built into this page; they do not show your computer.')
    page = page.replace('Browse files and folders on the computer running OncoTracer. Click a folder to open it.', 'Browse the synthetic example folders. No local filesystem is connected.')
    page = page.replace('Run analysis validates the saved settings, prepares missing analysis tools, and downloads reference files when needed. Processing can take time. Keep this terminal open to follow progress here.', 'Simulate Run plays an illustrative progress animation. It does not validate resources, install tools, download references, process reads or produce scientific results.')
    page = page.replace('Save configuration and check</button>', 'Preview configuration and simulate checks</button>')
    page = page.replace('>Run analysis</button>', '>Simulate Run</button>')
    page = page.replace('>Stop analysis</button>', '>Stop simulation</button>')
    page = page.replace('>Open results</a>', '>Open simulated results</a>')
    page = page.replace("'Saved: '+prepared.config_path", "'Preview only: '+prepared.config_path")
    page = page.replace("'Configuration checked'", "'Simulated checks passed'")
    for old, new in [('Analysis running…', 'Simulation running…'), ('Analysis completed · exit 0', 'Simulation completed · no analysis was run'), ('Analysis stopped', 'Simulation stopped'), ('Stopping analysis…', 'Stopping simulation…')]:
        page = page.replace(old, new)
    page = page.replace('View the saved configuration', 'View the simulated configuration')
    page = page.replace('Configuration, sample metadata, logs, and results will be saved here. Existing configurations are protected.', 'The demo shows where a real run would save files. Nothing is written to disk here.')
    page = page.replace('Check common installation folders on the computer running OncoTracer.', 'Simulate resource discovery in fictional /demo folders; your computer is not inspected.')
    results = '''<section id="demo-results" class="card demo-result-card" hidden><span class="demo-chip">Illustrative results</span><h2 id="demo-results-heading" tabindex="-1">Your simulated workflow</h2><p>These are the settings you explored. No alignment, CNA call, variant, probability, genotype or clinical interpretation was generated.</p><dl id="demo-result-summary"></dl><p class="notice">A real analysis produces caller VCFs, evidence tables, quality-control summaries and reports. Download and run OncoTracer to process your own data.</p><a href="../../installation/">Install OncoTracer →</a></section>'''
    page = replace_once(page, '</div><footer>OncoTracer runs on this computer. This page loads no external assets and does not upload your files.</footer></main>', '</div>' + results + '<footer class="demo-footer">Built from the actual OncoTracer setup interface. Browser-only synthetic simulation. UI source SHA-256: <code>' + digest[:12] + '</code>.</footer></main>')
    return '<!-- Generated by scripts/build_setup_demo.py; edit demo assets or the actual UI, then rebuild. -->\n' + page + '\n'


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    result = render()
    output = ASSETS / 'index.html'
    if args.check:
        if not output.exists() or output.read_text(encoding='utf-8') != result:
            raise SystemExit('Setup demo is stale. Run python scripts/build_setup_demo.py')
        print('Setup demo matches the actual UI and demo assets.')
    else:
        output.write_text(result, encoding='utf-8')
        print(f'Wrote {output.relative_to(ROOT)}')


if __name__ == '__main__':
    main()
