# Copy commands and create sample tables

Examples use a Linux Bash terminal. Follow one tutorial from top to bottom;
you do not need to understand Python or write a special run script.

## Paths and command lines

- Replace `/path/to/my/analyses_dir/` with an existing folder where you keep analyses.
- `cd` changes the current folder; `pwd` prints it. `$PWD` inserts its absolute path.
- `mkdir -p` creates missing folders. It does not supply the example FASTQ files.
- Keep quotes around paths, especially if a folder name contains spaces.
- A final `\` continues one command on the next line. Paste the entire block;
  do not add spaces after the backslash.
- A flag starts with `--`. In `--config /work/study/config/run.yml`, the flag
  selects which settings file to read.

## Create a CSV with cat

CSV means comma-separated values: a header followed by one row per sample.
This example creates a real file, not just text displayed in the terminal:

```bash
mkdir -p "$PWD/example-input"
cat > "$PWD/example-input/samples.csv" <<'CSV'
sample_name,status
sampleA,TUMOR
sampleB,NORMAL
CSV
```

Paste everything through the last `CSV`, which must be on its own line.
If the terminal shows `>` and waits, it is still expecting that closing line;
Ctrl+C cancels an unfinished command. `cat >` replaces an existing file of the
same name. Choose a new filename if you need to keep the old table.

`<<'CSV'` keeps the table text literal. Examples that need `$PWD` expanded into
file paths use `<<CSV` without quotes. Follow the delimiter shown in each example.
Keep the header unchanged. Quote a CSV field containing a comma; use simple,
de-identified sample IDs without spaces.

## Which table do I need?

| Command / purpose | Header | One row represents |
| --- | --- | --- |
| `auto --mode illumina` | `sample_name,status` | One library with matching FASTQ filenames |
| `setup --samplesheet` | `sample,fastq_1,fastq_2,status` | One library with explicit read paths |
| `auto --mode ont` | `barcode,sample_name,status` | One sample in one barcode folder |
| Optional pathology | `illumina_sample_id,case_code,final_diagnosis` | One matched, de-identified sample |

Do not pass the two-column table to `setup --samplesheet`. Follow [batch setup](auto_params.md)
or [explicit FASTQ paths](setup.md#illumina-multiple-libraries) for complete examples.

## Check before running

`setup` and `auto` save settings. `check` lists selected samples and catches
configuration problems without starting analysis. `run` starts analysis.
Read each result before continuing; if a command fails, fix the error first.
Do not paste a `run` command while the previous command is still running.

A successful `check` is not a read-quality assessment or a confirmed diagnosis.
Review the [run summary and QC](outputs.md) after analysis.
