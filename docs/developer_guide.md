# Developer guide

## Source checks

```bash
python3 -m compileall -q oncotracer_cli tests scripts bin
python3 -m unittest -v \
  tests/test_native_cli.py \
  tests/test_native_engine.py \
  tests/test_compare_native_parity.py \
  tests/test_native_docs.py \
  tests/test_poetry_cli.py \
  tests/test_native_classifier.py \
  tests/test_qdnaseq_helper.py \
  tests/test_native_provenance.py
Rscript -e "parse(file='bin/scripts/native_qdnaseq.R')"
Rscript -e "parse(file='bin/scripts/native_qdnaseq_pon.R')"
Rscript -e "parse(file='bin/scripts/native_ichorcna.R')"
```

## Build the copied executable

```bash
SOURCE_COMMIT="$(git rev-parse HEAD)"
SOURCE_SHA256="$(git -c tar.umask=0002 archive --format=tar "$SOURCE_COMMIT" | sha256sum | awk '{print $1}')"
python3 scripts/build_native_binary.py \
  --output dist/oncotracer \
  --source-commit "$SOURCE_COMMIT" \
  --source-sha256 "$SOURCE_SHA256"
cd /tmp
/path/to/dist/oncotracer --version
/path/to/dist/oncotracer provenance --json
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
