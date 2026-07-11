# What Is a Path?

A **file** is one named item that contains data, such as a FASTQ, YAML, CSV, or PDF file.

```text
Sample_A_R1.fastq.gz
params/my_illumina.yml
03_cna_codification/cna_events.tsv
```

A **folder**, also called a **directory**, is a container that can hold files and other folders.

```text
/home/student/oncotracer_project
/home/student/data/ont_run/fastq_pass
```

The **filesystem** is the organized tree of folders and files. On Linux, the top of the tree is root, written as `/`.

## Current Working Directory and Repository Root

The **current working directory** is the folder your terminal command is running from now.

```bash
pwd
ls -lh
```

The **repository root** is the top folder of the OncoTracer checkout. It contains `main.nf`, `nextflow.config`, `README.md`, `docs/`, and `params/`.

```bash
cd oncotracer
ls -lh main.nf nextflow.config README.md
```

## File Paths and Directory Paths

A **file path** points to a file:

```text
/home/student/data/Sample_A_R1.fastq.gz
/home/student/oncotracer_project/input/illumina_samplesheet.csv
```

A **directory path** points to a folder:

```text
/home/student/data
/home/student/oncotracer_project/runs/my_first_run
```

Check a file:

```bash
ls -lh /home/student/data/Sample_A_R1.fastq.gz
```

Look inside a folder:

```bash
find /home/student/data -maxdepth 2 -type f | head
```

## Absolute and Relative Paths

An **absolute path** starts at Linux root `/` and works from any current directory.

```text
/home/student/oncotracer_project/params/my_illumina.yml
/data/oncotracer_project/runs/illumina_example
```

Find absolute paths with:

```bash
realpath .
realpath data/sample_R1.fastq.gz
```

A **relative path** starts from your current working directory.

```text
params/my_illumina.yml
data/sample_R1.fastq.gz
../other_folder/file.txt
```

Convert relative paths before putting them in YAML:

```bash
realpath params/my_illumina.yml
realpath data/sample_R1.fastq.gz
```

## Special Symbols

Linux root `/` is the top of the filesystem.

```bash
ls /
```

Home `~` usually means your personal folder in the shell.

```bash
ls ~
```

Current directory `.` means here.

```bash
realpath .
```

Parent directory `..` means one folder up.

```bash
realpath ..
```

For YAML files, prefer the full absolute path from `realpath` instead of `~` or environment variables such as `$HOME`. Do not assume shell shortcuts are expanded inside a Nextflow YAML value.

## Case, Spaces, and Quoting

Linux paths are case-sensitive: `Sample_A_R1.fastq.gz` and `sample_a_R1.fastq.gz` are different.

Spaces in paths require shell quoting:

```bash
ls -lh "/home/student/data/Run 1/Sample A_R1.fastq.gz"
```

Avoid spaces in project, sample, and output folder names when possible.

## Windows and WSL Paths

A Windows path is not a Linux path:

```text
C:\Users\Name\data\sample.fastq.gz
```

In WSL, Windows files are usually visible under `/mnt/c`:

```bash
ls -lh /mnt/c/Users/Name/data/sample.fastq.gz
realpath /mnt/c/Users/Name/data/sample.fastq.gz
```

Files stored inside the Linux WSL filesystem usually look like:

```text
/home/name/data/sample.fastq.gz
```

Use the Linux-accessible path in OncoTracer YAML.

## Containers Must See the Same Root Folder

Docker and Singularity run steps inside containers. The container must see every file named in the YAML. OncoTracer binds `lpwgs_root` into Docker/Singularity by default, so put input files and output folders under `lpwgs_root` when possible.

```yaml
lpwgs_root: /home/student/oncotracer_project
outdir: /home/student/oncotracer_project/runs/illumina_example
illumina_samplesheet: /home/student/oncotracer_project/input/illumina_samplesheet.csv
```
