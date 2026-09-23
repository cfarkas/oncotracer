# HCC1143 public example

The [QuickStart 2 guide](../../docs/public_cohort.md) gives the downloads,
checksums, three-row Illumina samplesheet and ordinary `oncotracer setup`,
`oncotracer check` and `oncotracer run` commands.

This folder supplies the six-file [manifest](manifest.tsv),
[checksums](checksums.md5), sample labels and output verifier used by tests.
The three libraries are HCC1143_DMSO, HCC1143_BEZ235 and HCC1143_TRAMETINIB.
DMSO is a treatment control, not a normal genome.

## Terminal / headless version

Install the launcher and Conda tools first. Set the source checkout and writable
analysis directory paths below, then run these blocks in order. This downloads
all six files from the bundled manifest, checks each MD5 and gzip stream, then
uses the same three-library settings as QuickStart 2. No browser is started.

```bash
cd /path/to/my/analyses_dir/
set -euo pipefail
ONCOTRACER_EXAMPLES="/path/to/my/oncotracer_source/examples"
mkdir -p "$PWD/oncotracer-quickstart2/input"

while IFS=$'\t' read -r sample treatment accession mate filename bytes checksum url; do
  [[ "$sample" == "sample_name" ]] && continue
  curl --fail --location --continue-at - \
    --output "$PWD/oncotracer-quickstart2/input/$filename" "$url"
  printf '%s  %s\n' "$checksum" "$PWD/oncotracer-quickstart2/input/$filename" | md5sum -c -
  gzip -t "$PWD/oncotracer-quickstart2/input/$filename"
done < "$ONCOTRACER_EXAMPLES/hcc1143_lpwgs/manifest.tsv"
```

Continue only if every checksum reports `OK` and gzip reports no errors.

```bash
cd /path/to/my/analyses_dir/
cat > "$PWD/oncotracer-quickstart2/input/samplesheet.csv" <<CSV
sample,fastq_1,fastq_2,status
HCC1143_DMSO,"$PWD/oncotracer-quickstart2/input/HCC1143_DMSO_R1.fastq.gz","$PWD/oncotracer-quickstart2/input/HCC1143_DMSO_R2.fastq.gz",tumor
HCC1143_BEZ235,"$PWD/oncotracer-quickstart2/input/HCC1143_BEZ235_R1.fastq.gz","$PWD/oncotracer-quickstart2/input/HCC1143_BEZ235_R2.fastq.gz",tumor
HCC1143_TRAMETINIB,"$PWD/oncotracer-quickstart2/input/HCC1143_TRAMETINIB_R1.fastq.gz","$PWD/oncotracer-quickstart2/input/HCC1143_TRAMETINIB_R2.fastq.gz",tumor
CSV
oncotracer setup --non-interactive \
  --project "$PWD/oncotracer-quickstart2/analysis" \
  --mode illumina --analysis cna --backend conda \
  --samplesheet "$PWD/oncotracer-quickstart2/input/samplesheet.csv" \
  --hg38_build --threads 4
oncotracer check --config "$PWD/oncotracer-quickstart2/analysis/config/run.yml"
oncotracer run --backend conda \
  --config "$PWD/oncotracer-quickstart2/analysis/config/run.yml"
```

This CNA example uses QDNAseq at 100 kb and automatic hg38 reference preparation.
For preservation metadata, add `--variant-specimen-type fresh` or `ffpe` according
to the specimen records; that choice alone does not enable variants. Resume by
repeating only the same `run` command. Results are below `analysis/results/`.
There is no separate example launcher.
