# Annotate variants with ANNOVAR

ANNOVAR adds gene and database descriptions to called variants. OncoTracer uses
an **existing local installation** after variant calling and filtering. It does
not install ANNOVAR, accept its license, download databases or decompress them.

Start with [variant setup](variants.md). In the browser, leave **ANNOVAR annotation**
on **Automatically use an existing local installation**, or choose **Skip ANNOVAR annotation**.
If discovery cannot find your resources, fill the installation and database folders.

## Use Autodetect resources

In variant settings, click **Autodetect resources**. It checks the server's local
installation and database candidates, fills blank fields and preserves paths you
entered. A missing or incomplete installation gets a **Copy commands** box plus
links to ANNOVAR registration and database setup. The button never installs
ANNOVAR or downloads a database.

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

## How automatic discovery works

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

For an existing configuration, the equivalent fields are:

```yaml
variant_annovar: auto
variant_annovar_dir: /resources/annovar
variant_annovar_db: /resources/annovar/humandb
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
