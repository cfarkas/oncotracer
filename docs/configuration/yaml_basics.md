# YAML and Paths

A YAML file is the run contract: it tells OncoTracer which branch to use, where inputs live, and where every output should be written. Copy a template before editing it.

```bash
cd oncotracer                                              # enter the cloned repository
cp params/illumina.minimal.yml params/my_illumina.yml      # copy the Illumina template
realpath .                                                 # obtain the absolute repository path
realpath /path/to/input.fastq.gz                           # obtain an absolute input-file path
mkdir -p /home/user/oncotracer_project/input               # create an input area
mkdir -p /home/user/oncotracer_project/runs                # create an output area
nano params/my_illumina.yml                                # edit the copied YAML
```

Save with `Ctrl+O`, press Enter, then exit with `Ctrl+X`. Validate and run.

## Path rules

- Use absolute Linux paths beginning with `/`.
- Do not put `$HOME`, `~`, or shell commands in YAML; YAML does not expand them.
- `lpwgs_root` must be a common parent of inputs and outputs because it is mounted into Docker or Singularity.
- Keep `outdir` below `lpwgs_root`; the SAMURAI subdirectory is derived automatically.
- WSL paths use `/mnt/c/...`, not `C:\...`.
- Paths are case-sensitive and spaces are best avoided.
- Confirm each input with `realpath` and `ls -lh` before a full run.

## Illumina YAML, line by line

```yaml
mode: illumina                                      # select paired-end Illumina processing
lpwgs_root: /home/user/oncotracer_project           # common parent mounted into the container
outdir: /home/user/oncotracer_project/runs/sample_a # final OncoTracer run directory
illumina_samplesheet: /home/user/oncotracer_project/input/illumina_samplesheet.csv # CSV with paired FASTQ paths
illumina_analysis_type: solid_biopsy                # SAMURAI analysis preset
illumina_caller: qdnaseq                            # CNA caller used for Illumina
illumina_binsize_kb: 100                            # copy-number bin size in kilobases
run_cna_classifier: false                           # optional classifier/pathology stage
force: false                                        # preserve existing outputs unless intentionally refreshing
```

## ONT YAML, line by line

```yaml
mode: ont                                           # select Oxford Nanopore processing
lpwgs_root: /home/user/oncotracer_project           # common parent mounted into the container
outdir: /home/user/oncotracer_project/runs/ont_a    # final OncoTracer run directory
ont_folder: /home/user/oncotracer_project/input/fastq_pass # folder containing barcode FASTQ directories
ont_barcodes: barcode01,barcode02                   # comma-separated barcode directories
ont_sample_names: Patient_A,Patient_B               # names matching barcode order one-to-one
ont_analysis_type: liquid_biopsy                    # SAMURAI analysis preset
ont_caller: ichorcna                                # CNA caller used for ONT
ont_binsize_kb: 500                                 # copy-number bin size in kilobases
ont_min_age_minutes: 0                              # process completed FASTQ immediately
run_cna_classifier: false                           # optional classifier/pathology stage
force: false                                        # preserve existing outputs unless intentionally refreshing
```

Validate the edited YAML before computation:

```bash
nextflow run main.nf -stub-run --docker -params-file params/my_illumina.yml # parse YAML and test workflow connections
```
