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

## Remaining limits

- Classifier environments and model files still require separate installation and
  license review. Setup records the assets supplied; it does not authenticate them.
- `check` validates configuration, not biological adequacy or model calibration.
- Trained MARLIN/Sturgeon inference and the full public-data QuickStarts were not
  rerun for this audit. Synthetic integration is not clinical validation.
- The new commands require this source revision; publishing a new release is a
  separate step. Existing v2.0.0 binaries and containers are unchanged.
