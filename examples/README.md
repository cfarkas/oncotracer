# Examples — start here

[Install OncoTracer](../docs/installation.md) once, then follow a guide below.
Each guide downloads its inputs, verifies checksums, and uses the normal
OncoTracer setup and run commands. Keep your analysis projects outside this
source folder.

| Example | Guide | Files here |
| --- | --- | --- |
| **First run: small Illumina + ONT examples** | [QuickStart 1](../docs/quick_start.md) | `quickstart/`: output verifier |
| Three Illumina libraries | [QuickStart 2](../docs/public_cohort.md) | `hcc1143_lpwgs/`: manifest, checksums, sample labels and verifier |
| Larger public Illumina cohort | [Full tutorial](../docs/full_tutorial.md) | `prjna754199/`: archive inputs, provenance and verifier |
| Six tumors and four independent normals | [Mock-cohort tutorial](../docs/six_tumor_four_normal.md) | Synthetic cohort recipe in the guide |
| Variants from FASTQs or existing BAMs | [Variant guide](../docs/variants.md) · [All command examples](../docs/variants_reference.md) | Commands and sample-manifest examples in the guides |
| Manuscript panels | [Paper report](../docs/paper_report.md) | `paper_report/`: synthetic demonstration inputs |
| Optional pathology table | [Pathology guide](../docs/configuration/pathology.md) | `pathology/`: synthetic example table |

To explore the interface without downloading data, open the
[interactive browser demo](https://cfarkas.github.io/oncotracer/assets/setup-demo/index.html).
For your own FASTQs, use the [setup guide](../docs/setup.md).
