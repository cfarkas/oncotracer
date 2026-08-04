# Developer guide

## Source checks

```bash
python3 -m compileall -q oncotracer_cli tests scripts bin
python3 -m unittest -v \
  tests/test_native_cli.py \
  tests/test_native_engine.py \
  tests/test_compare_native_parity.py
Rscript -e "parse(file='bin/scripts/native_qdnaseq.R')"
Rscript -e "parse(file='bin/scripts/native_qdnaseq_pon.R')"
Rscript -e "parse(file='bin/scripts/native_ichorcna.R')"
```

## Build the copied executable

```bash
python3 scripts/build_native_binary.py --output dist/oncotracer
cd /tmp
/path/to/dist/oncotracer --version
```

The test must run outside the checkout. Do not import source files accidentally through the working directory.

## Scientific changes

Changes to alignment, callers, reference assets, correction formulas, thresholds, refinement, or output schemas require both complete parity workflows. Do not relax a parity threshold merely to make a changed result pass; document and review an intentional scientific difference.

## Documentation

GitHub Pages is canonical. The repository README remains a concise installation landing page. Build with:

```bash
python3 -m pip install -r docs/requirements.txt
mkdocs build --strict
```
