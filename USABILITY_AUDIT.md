# First-use usability audit

Scope: a first-use walkthrough of installation, public examples, sample selection,
configuration, methylation, and result interpretation. This is an engineering
walkthrough, not a study with recruited non-expert participants.

| First-use problem | Change |
| --- | --- |
| Too many routes and long explanations before the first command | Short task-based landing pages; detailed options kept in linked references |
| Unclear flags, input paths, and output paths | Public `oncotracer` commands with small flag tables, folder examples, and expected results |
| Hand-editing many settings and file hashes | `setup` prompts or explicit flags create commented YAML and record local asset hashes |
| Discovering one missing setting at a time during a run | `check` collects missing resources and paths before displaying the analysis plan |
| Incorrect sample or platform flags can be overlooked | Explicit barcode selection, conflicting-input errors, and no configuration overwrite |
| A download-only example could appear to have completed analysis | Distinct preparation and completion messages; output verification documented separately |
| Existing modified-base BAMs required manual processing | `--modbam` reuses calls with CPU alignment, primary/tag filtering, and duplicate-read checks |
| Methylation unnecessarily coupled to copy-number analysis | `--methylation-only` skips the CNA branch and records `cna_status: not_requested` |
| Low coverage produced an obscure classifier error | Zero covered MARLIN probes produces an explicit no-call, not a leukemia prediction |
| New commands absent from the published executable | Source and v2.0.0 release instructions clearly separated |
| Plain pip installation loses the source identity required by the installer | Current-source instructions retain an editable, clean Git checkout |
| Users cannot tell whether their computer can run the workflow | `system` reports CPU allowance, available RAM, visible container limits, disk and task-specific planning guidance; `check` includes the warnings |
| Low-RAM users must build genome indexes | Published hg38/BWA/minimap2 bundle with streaming, checksum-verified `reference install`; no index construction or loading during import |
| No clear uninstall path | Ownership-checked `uninstall` previews exact tools, refuses active/changed installations, retains recovery copies by default, and provides explicit permanent removal |
| Report LLM ignores local-only settings and guesses model type from its name | Shared CPU loader respects local-only for every model component, supports encoder–decoder/causal models, pins resolved Hub snapshots, and caches failed loads |
| Uncited prose or stray numbers can enter literature interpretation | Structured draft claims require known source IDs; reference selection accepts only visible IDs; rejected generations retain labeled deterministic text |
| Offline catalog placeholders appear LLM/literature-supported | No abstract means no synthesis attempt; HTML/PDF labels distinguish catalog, retrieved excerpts and generated drafts |

## Verification

Regression tests cover prompts, explicit flags, paths with spaces and special
characters, resource reuse, configuration checks, CPU overrides, branch isolation,
resume behavior, and no-call reporting. The installed command is exercised outside
the checkout. Documentation checks enforce short beginner pages and public commands;
MkDocs is built in strict mode.

A synthetic CPU integration test uses real samtools, Dorado alignment, and Modkit.
It verifies BAM-to-CpG extraction without basecalling, GPU use, or a trained-model
prediction. Additional real-samtools fixtures check missing tags and duplicate reads.
No patient data is included in these fixtures or this change.

LLM tests use synthetic evidence and tiny randomly initialized T5/GPT-2 models,
with CPU-only PyTorch 2.6.0 and both Transformers 4.57.6 and 5.16.1. These check
loading, context budgets, citation structure, fallback and rendering, not medical
accuracy. No trained report model or patient input is uploaded to an LLM service.
Uninstall tests use disposable, ownership-marked fixtures, not installed tools.
A copied executable was also removed through the public command, verified in its
recovery folder, restored, and executed successfully.
Reference tests cover corruption, unsafe paths, no overwrites and platform selection.
The real 14.76-GiB hg38/BWA/minimap2 bundle was exported from the existing validated
public reference and [published separately](https://github.com/cfarkas/oncotracer/releases/tag/hg38-reference-v1).
All 23 GitHub asset sizes and SHA-256 digests match the local inventory. A fresh
public-source installation downloaded and installed the complete bundle over
unauthenticated HTTPS under a 1-GiB address-space limit: 42,940 KiB peak resident
memory, 13 minutes 14 seconds, no swap and no index builds. Local transfer separately
peaked at 43,456 KiB. These measure import, not alignment or downstream analysis RAM.

Fresh Bioconda BWA/minimap2 installations matched the bundle's tool hashes and the
builds selected by hosted CI. Normal engine validation and both index readers
accepted the public download; three synthetic short reads and three synthetic
long reads mapped to their expected hg38 loci. This opt-in test used two alignment
threads, a 16-GiB address-space cap, 8,088,628 KiB peak RSS and no index builds.
It is a tiny-read index check, not a full CNA or methylation-classifier run.

The source regression suite passes 363 tests with three opt-in skips; installed
setup, real tiny-model CPU inference and real-reference alignment are exercised
separately. Shell, copy-paste, example and strict documentation checks pass. Fresh
GitHub source installation, tagged-source reference previews, and analysis-tool
installation previews work outside the development checkout.

## Remaining limits

- Classifier environments and model files still require separate installation and
  license review. Setup records the assets supplied; it does not authenticate them.
- `check` validates configuration, not biological adequacy or model calibration.
- Hardware figures are planning estimates, not measured peak-memory guarantees.
  Optional models remain explicitly model-dependent; swap is not counted as RAM.
- Citation checks establish source identity and response structure, not whether a
  generated claim is supported scientifically. Generated text remains a reviewable draft.
- Trained MARLIN/Sturgeon inference and full public-data QuickStarts were not rerun
  locally. Hosted QuickStart parity remains a separate required software-release
  gate; synthetic integration is not clinical validation.
- The new commands require the updated source. Only the reference assets are newly
  released; existing v2.0.0 binaries and containers are unchanged.
