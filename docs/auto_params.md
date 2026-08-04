# Automatic Setup

Automatic Setup validates FASTQ names and creates a flat YAML plus an exact analysis samplesheet. It does not start the scientific analysis.

## Illumina

Put one R1/R2 pair per sample in one folder and create:

```csv
sample_name,status
TUMOR_01,TUMOR
TUMOR_02,TUMOR
CONTROL_01,NORMAL
CONTROL_02,NORMAL
```

Run:

```bash
oncotracer auto \
  --mode illumina \
  --reads-folder "$PWD/project/input/fastq" \
  --sample-table "$PWD/project/input/samples.csv" \
  --config-dir "$PWD/project/config" \
  --outdir "$PWD/project/results"

oncotracer run --backend conda --config "$PWD/project/config/illumina.auto.yml"
```

Zero normal rows run without a local panel. Exactly one normal is rejected. Two or more normals build and apply a native median-log₂ qDNAseq panel; normal samples remain reference/QC inputs and tumor samples are exported downstream.

## ONT

Use one barcode folder per sample beneath `fastq_pass` and create a table using the barcode and sample names expected by the generator. Then run the same `oncotracer auto --mode ont ...` pattern and analyze `ont.auto.yml`.

Generated paths are absolute. Keep the YAML, input FASTQs, result directory, and `lpwgs_root` on storage visible to the selected backend.
