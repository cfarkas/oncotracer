# First Illumina Run

```bash
cd oncotracer
cp params/illumina.minimal.yml params/my_illumina.yml
```

Find real paths:

```bash
pwd
realpath .
ls -lh /home/student/data/Sample_A_R1.fastq.gz
ls -lh /home/student/data/Sample_A_R2.fastq.gz
```

Create `/home/student/oncotracer_project/input/illumina_samplesheet.csv`:

```csv
sample,fastq_1,fastq_2,status
Sample_A,/home/student/data/Sample_A_R1.fastq.gz,/home/student/data/Sample_A_R2.fastq.gz,tumor
```

Edit the YAML:

```bash
nano params/my_illumina.yml
```

Required fields:

```yaml
mode: illumina
lpwgs_root: /home/student/oncotracer_project
outdir: /home/student/oncotracer_project/runs/my_first_illumina_run
illumina_samplesheet: /home/student/oncotracer_project/input/illumina_samplesheet.csv
illumina_samurai_outdir: /home/student/oncotracer_project/runs/my_first_illumina_run/01_samurai_illumina
```

Validate and run:

```bash
nextflow run main.nf -stub-run --docker -params-file params/my_illumina.yml
nextflow run main.nf --docker -params-file params/my_illumina.yml -resume
```
