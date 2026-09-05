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
| Low-RAM users must build genome indexes | Streaming, checksum-verified `reference export/install` transfers validated hg38/BWA/minimap2 files without building or loading an index on import |
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
Reference tests cover corruption, unsafe paths, no overwrites and platform selection.
The real 14.76-GiB hg38/BWA/minimap2 bundle was exported from the existing validated
public reference, then imported locally under a 1-GiB address-space limit. Import
completed with 43,456 KiB peak resident memory and no index builds. This measures
transfer/validation only, not alignment or downstream analysis memory. Public
hosting of the reference assets is a separate publication step.

## Remaining limits

- Classifier environments and model files still require separate installation and
  license review. Setup records the assets supplied; it does not authenticate them.
- `check` validates configuration, not biological adequacy or model calibration.
- Hardware figures are planning estimates, not measured peak-memory guarantees.
  Optional models remain explicitly model-dependent; swap is not counted as RAM.
- Citation checks establish source identity and response structure, not whether a
  generated claim is supported scientifically. Generated text remains a reviewable draft.
- Trained MARLIN/Sturgeon inference and the full public-data QuickStarts were not
  rerun for this audit. Synthetic integration is not clinical validation.
- The new commands require this source revision; publishing a new release is a
  separate step. Existing v2.0.0 binaries and containers are unchanged.
