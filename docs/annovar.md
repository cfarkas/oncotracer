# Annotate variants with ANNOVAR

ANNOVAR adds gene and database descriptions to called variants. OncoTracer uses
an **existing local installation** after variant calling and filtering. It does
not install ANNOVAR, accept its license, download databases or decompress them.

Start with [variant setup](variants.md). Open **4 · Annotation** and leave
**ANNOVAR annotation** on **Automatically use an existing local installation**,
or choose **Skip ANNOVAR annotation**. Open the path controls to inspect or enter
the installation and database folders.

For a remote browser session, use the [SSH tunnel guide](headless.md).
For execution with no browser, use the complete terminal example under
[Set paths explicitly](#set-paths-explicitly).

## Use Autodetect resources

Use **Autodetect** beside the installation or database path to check that resource,
or **Autodetect resources** to check all selected variant resources. These checks
run on the server, fill blank fields and preserve paths you entered. If several
candidates are found, review them and choose a folder.

The software installation and database are detected separately. Finding ANNOVAR
can fill its installation path even when a matching gene database is missing;
annotation still needs both. The resource details dialog provides **Copy commands**
boxes and official registration/setup links for missing resources. Discovery
never installs ANNOVAR or downloads a database.

Obtain resources separately under their terms, enter their paths, then rerun
Autodetect. You can also choose **Skip ANNOVAR annotation** and retain variant
calling. Software discovery cannot determine the reference build from your
intent: choose databases matching the actual alignment/reference.

## What must already be available?

| Resource | Required locally |
| --- | --- |
| Perl | An executable `perl` on the tool search path. |
| ANNOVAR installation | Readable, executable `table_annovar.pl`, `annotate_variation.pl`, `convert2annovar.pl` and `coding_change.pl`. |
| Gene database | A matching, unpacked RefSeq TXT and mRNA FASTA pair for the requested build. |
| ClinVar | Optional unpacked database named `BUILD_clinvar_YYYYMMDD.txt`. |

For hg38, a complete gene pair is either `hg38_refGeneWithVer.txt` with
`hg38_refGeneWithVerMrna.fa`, or `hg38_refGene.txt` with `hg38_refGeneMrna.fa`.
Automatic selection prefers `refGeneWithVer` when both pairs exist. Compressed
archives alone do not satisfy the check. Database indexes are included when available.

The build must be exactly **hg38 or hg19** and match the alignment/reference.
An hg19 annotation database is never substituted for hg38. If several compatible
ClinVar TXT files exist, automatic annotation selects the newest date in their
filenames. It does not check online for updates. A RefSeq-only installation can
annotate genes without ClinVar.

Obtain software and resources under the applicable terms using the
[official ANNOVAR guide](https://annovar.openbioinformatics.org/en/latest/user-guide/startup/).
No ANNOVAR software or database is bundled in OncoTracer's Docker image.

## How annotation resolves paths

Browser Autodetect checks a bounded set of common local folders and lets you
review candidates. During annotation, saved paths and environment settings take
the following priority.

For the installation folder, OncoTracer checks:

1. Your explicit browser/configuration path.
2. `ANNOVAR_HOME`, then `ANNOVAR_DIR`.
3. The folder containing `table_annovar.pl` on `PATH`.
4. `~/annovar` in your home folder.

For databases, it uses your explicit database path, then `ANNOVAR_DB`, then
`ANNOVAR_DATABASE_DIR`; otherwise it checks `humandb` under the installation.
An explicit path or environment override is respected: an invalid override
produces a recorded skip instead of silently choosing another installation.
Discovery does not recursively search all mounted disks.

### Set paths explicitly

Replace these example paths with your existing folders:

```bash
oncotracer setup --variants --variant-annovar auto \
  --variant-annovar-dir /resources/annovar \
  --variant-annovar-db /resources/annovar/humandb
```

**Terminal only: CNA, Mutect2 and annotation for one Fresh Illumina sample.**
The following is an alternative to the browser command. It specifies the input
library, preservation, callers, backend and ANNOVAR paths; use a new project folder.
Core Conda tools and the [optional variant environment](variants_reference.md#conda-and-docker)
must already be installed.

```bash
oncotracer setup --non-interactive --project "$PWD/annotated-study" \
  --mode illumina --analysis cna --backend conda --threads 4 \
  --sample-name TUMOR01 --status tumor \
  --fastq-1 /data/illumina/TUMOR01_R1.fastq.gz \
  --fastq-2 /data/illumina/TUMOR01_R2.fastq.gz \
  --hg38_build --variants \
  --variant-specimen-type fresh --variant-callers mutect2 \
  --variant-tool-prefix "$HOME/.local/share/oncotracer/optional-tools/variants" \
  --variant-ffperase off --variant-varlociraptor off \
  --variant-annovar auto --variant-annovar-dir /resources/annovar \
  --variant-annovar-db /resources/annovar/humandb
oncotracer check --config "$PWD/annotated-study/config/run.yml"
oncotracer run --backend conda --config "$PWD/annotated-study/config/run.yml"
```

The native FASTQ workflow uses hg38; the selected database pair must match it.
For the Docker equivalent, follow the [terminal Docker example](variants_reference.md#run-cna-and-variants-with-docker)
and add the three ANNOVAR flags above to its setup command. Use absolute host
paths; the runner mounts the resources for annotation.

For an existing configuration, the equivalent fields are:

```yaml
variant_annovar: auto
variant_annovar_dir: /resources/annovar
variant_annovar_db: /resources/annovar/humandb
```

For existing BAMs, add those fields to the
[complete BAM configuration](variants_reference.md#call-from-existing-bams-without-rerunning-cna),
set its output to a new folder, and save it as `/data/variants-annotated.yml`.
Then run directly with the configured local tools:

```bash
oncotracer variants --config /data/variants-annotated.yml --dry-run
oncotracer variants --config /data/variants-annotated.yml --threads 4
```

Use paths without spaces or shell metacharacters: ANNOVAR's internal commands
cannot safely handle them. The FASTQ Docker workflow discovers host resources
and mounts them read-only; choose absolute host paths in its browser form.
When invoking Docker manually, [mount resources explicitly](variants_reference.md#conda-and-docker)
and use paths valid inside the container.

## Check annotation status separately

Successful calling and successful annotation are separate outcomes:

| Status | Meaning |
| --- | --- |
| `complete` | ANNOVAR ran and its expected outputs were verified. |
| `skipped` | Annotation was disabled or matching local resources were unavailable; the reason is recorded. |
| `not_applicable` | The successful caller returned zero variant records. |
| `failed` | Annotation encountered a runtime error; completed caller output is retained. |

A skipped annotation is not an empty variant callset. Read the caller's record
count and annotation reason in **08 · Small variants** and `variant_status.json`.
A runtime annotation error can make the overall run partially complete.

Without a browser, inspect the FASTQ example's status and annotation table:

```bash
python3 -m json.tool "$PWD/annotated-study/results/08_variants/variant_status.json"
head -n 5 "$PWD/annotated-study/results/08_variants/samples/TUMOR01/mutect2/annovar.hg38_multianno.txt"
```

The table exists only after successful annotation; the JSON records a skip or
failure even when no table was produced.


## Where are the annotations?

For each sample and caller, successful annotation writes:

- `08_variants/samples/SAMPLE/CALLER/annovar.BUILD_multianno.txt`
- `08_variants/samples/SAMPLE/CALLER/annovar.BUILD_multianno.vcf`

The caller VCF and evidence table remain available. `variant_provenance.json`
records selected databases and the inspected script identity. Scripts and small
assets receive content hashes; larger databases record file size and modification
time with an explicit note that their content was not hashed.

A ClinVar match is a database annotation, not a final clinical interpretation.
This adapter does not supply Chilean population minor-allele frequencies or
infer missing genotypes. See the [variant reference](variants_reference.md#optional-local-annovar)
for configuration and output details.
