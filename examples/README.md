# Examples — start here

[Install OncoTracer](../docs/installation.md) once, then follow a guide below.
Each analysis guide includes clearly labeled **Terminal / headless** commands
for its own inputs, setup, checks and execution. Public-data guides also show
downloads and checksums. Keep your analysis projects outside this
source folder.

| Example | Guide | Files here |
| --- | --- | --- |
| **First run: small Illumina + ONT examples** | [QuickStart 1](../docs/quick_start.md) | `quickstart/`: output verifier |
| Mock Illumina sample assignments: two tumors + one normal | [Browser and terminal walkthrough](illumina_multiple_libraries/README.md) | `illumina_multiple_libraries/`: fictional three-library CSV template |
| Three Illumina libraries | [QuickStart 2](../docs/public_cohort.md) · [Terminal commands here](hcc1143_lpwgs/README.md#terminal--headless-version) | `hcc1143_lpwgs/`: manifest, checksums, sample labels and verifier |
| Larger public Illumina cohort | [Full tutorial](../docs/full_tutorial.md) · [Terminal commands here](prjna754199/README.md#terminal--headless-version) | `prjna754199/`: archive inputs, provenance and verifier |
| Six tumors and four independent normals | [Mock-cohort tutorial](../docs/six_tumor_four_normal.md) | Synthetic cohort recipe in the guide |
| Variants from FASTQs or existing BAMs | [Variant guide](../docs/variants.md) · [All command examples](../docs/variants_reference.md) | Commands and sample-manifest examples in the guides |
| Manuscript panels | [Paper report](../docs/paper_report.md) | `paper_report/`: synthetic demonstration inputs |
| Optional pathology table | [Pathology guide](../docs/configuration/pathology.md) | `pathology/`: synthetic example table |

To explore the interface without downloading data, open the
[interactive browser demo](https://cfarkas.github.io/oncotracer/assets/setup-demo/index.html).
The demo uses fictional files, so it has no real analysis command. For your own
FASTQs, the matching **terminal-only setup and run** is:

```bash
oncotracer setup --terminal --run --project "$PWD/my-study" --backend conda
```

Choose platform, inputs and sample roles in the prompts; add
`--variant-specimen-type fresh` or `ffpe` from your records. For unattended input
selection, use the complete platform commands in the [setup guide](../docs/setup.md).
