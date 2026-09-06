# Developer guide

This page covers the native v2 source tree, copied executable, documentation, and release checks. Work from a dedicated source checkout and branch; do not develop directly in a released asset directory.

For packaging, installation ownership, and recovery contracts, see
[implementation details](architecture_details.md).

## Source checks

```bash
python3 -m compileall -q oncotracer_cli tests scripts bin
find bin examples tests scripts -type f -name '*.sh' -print0 \
  | xargs -0 -n1 bash -n

python3 -m unittest -v \
  tests/test_native_cli.py \
  tests/test_native_engine.py \
  tests/test_compare_native_parity.py \
  tests/test_native_docs.py \
  tests/test_poetry_cli.py \
  tests/test_native_classifier.py \
  tests/test_qdnaseq_helper.py \
  tests/test_native_provenance.py

python3 tests/test_docs_style.py
python3 tests/test_examples_start_from_clone.py
python3 tests/test_copy_paste_paths.py
```

Parse native R sources with the intended R installation when available:

```bash
Rscript --vanilla -e "invisible(parse(file='bin/scripts/native_qdnaseq.R'))"
Rscript --vanilla -e "invisible(parse(file='bin/scripts/native_ichorcna.R'))"
```

## Build the deterministic copied executable

```bash
SOURCE_COMMIT="$(git rev-parse HEAD)"
SOURCE_SHA256="$(
  git -c tar.umask=0002 archive --format=tar "$SOURCE_COMMIT" \
    | sha256sum | awk '{print $1}'
)"

python3 scripts/build_native_binary.py \
  --output dist/oncotracer \
  --source-commit "$SOURCE_COMMIT" \
  --source-sha256 "$SOURCE_SHA256"

chmod 0755 dist/oncotracer
```

Test from outside the checkout so imports cannot fall back to the working tree:

```bash
BINARY="$(pwd)/dist/oncotracer"
TMP_DIR="$(mktemp -d)"

cd "$TMP_DIR"
"$BINARY" --version
"$BINARY" provenance --json
"$BINARY" --help
```

The zipapp must contain native scripts, environment definitions, classifier assets, and no `.nf` workflow or `nextflow.config` payload.

## Test the public command surface

Test the exact documented setup/check/run examples with synthetic reads, without
downloading genomes or starting scientific tools:

```bash
python3 -m unittest tests.test_documented_workflows tests.test_batch_setup tests.test_setup -v
```

Then follow [QuickStart 1](quick_start.md) and [QuickStart 2](public_cohort.md)
through the intended backend on a suitable validation host. Automated release
validation retains its pinned datasets and comparison gates separately.

## Documentation

The active GitHub Pages site should retain the full tutorial breadth while every normal command uses the current `oncotracer` CLI. Build strictly:

```bash
python3 -m pip install -r docs/requirements.txt
mkdocs build --strict
```

Pull requests validate the site without publishing it. After a merge, the
`Deploy documentation` workflow builds the site once and publishes that artifact
to GitHub Pages from `main`. Set **Settings → Pages → Source** to **GitHub Actions**
and allow `main` in the `github-pages` deployment environment. No generated branch
or write access to repository contents is needed. The live `build-info.json`
records the source commit and deployment run; website publication does not
publish or replace a software release.

Documentation regression checks require:

- one tested installation route for the commands shown in the guides;
- ordinary `setup`/`auto`, `check`, and `run` commands in tutorials;
- every user-authored CSV/TSV table created by a complete `cat` block, with its filename and parent folder clear;
- explicit per-library FASTQ pairs and per-sample ONT barcodes, never guessed identities;
- no duplicate YAML keys or instructions to append keys that already exist;
- a short primary route, expected success output, and a clear next step;
- all Bash blocks to pass `bash -n`;
- no normal user page to invoke the historical executor;
- old configuration, input, pathology, refinement, output, and tutorial subjects to remain available through the native CLI;
- no retired workflow launchers or historical command tutorials in the current tree.

`tests.test_documented_workflows` executes the CSV-creation blocks and validates
the ordinary commands using synthetic reads. It checks public download URLs and
checksums against the pinned manifests; it does not download the cohort or prove
scientific parity. Keep advanced implementation details on reference pages, not
in the first steps of a tutorial. Maintain the word limits in `tests.test_native_docs`.

Useful documentation patterns reviewed for this project include
[nf-core's input-column explanations](https://nf-co.re/rnaseq/latest/docs/usage#samplesheet-input),
[EPI2ME's requirements, demo and input examples](https://github.com/epi2me-labs/wf-human-variation#compute-requirements),
and [Snakemake's separation of setup, basics and advanced topics](https://snakemake.readthedocs.io/en/stable/tutorial/tutorial.html).
These are writing references, not evidence that OncoTracer supports their input
semantics, resource requirements or scientific features.

## Scientific changes

Changes to reference assets, alignment, callers, normal correction, thresholds, refinement, event schemas, or reports require complete QuickStart 1 and QuickStart 2 parity review. Do not weaken a threshold merely to obtain a green check. Document any intentional scientific difference and update the audit schema when the output format changes.

## Release identity

A stable release binds:

- exact Git commit and deterministic `git archive` SHA-256;
- copied-executable SHA-256;
- immutable native container digest;
- exact successful Native v2 CI, QuickStart 1 parity, and QuickStart 2 parity runs;
- checksum-verified parity audit artifacts.

The release workflow must remain fail-closed when the current `main` SHA moves or any required evidence is missing.
