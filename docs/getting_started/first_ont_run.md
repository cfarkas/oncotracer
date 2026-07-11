# First ONT Run

```bash
cd oncotracer
cp params/ont.minimal.yml params/my_ont.yml
```

Find the ONT folder:

```bash
find /home/student/data/ont_run -maxdepth 2 -type f | head
realpath /home/student/data/ont_run/fastq_pass
find /home/student/data/ont_run/fastq_pass -maxdepth 2 -type d | head
```

Edit the YAML:

```bash
nano params/my_ont.yml
```

Required fields:

```yaml
mode: ont
lpwgs_root: /home/student/oncotracer_project
outdir: /home/student/oncotracer_project/runs/my_first_ont_run
ont_folder: /home/student/data/ont_run/fastq_pass
ont_barcodes: barcode01
ont_sample_names: Sample_ONT_1
ont_samurai_outdir: /home/student/oncotracer_project/runs/my_first_ont_run/01_samurai_ont
```

Multiple barcodes must keep matching order:

```yaml
ont_barcodes: barcode01,barcode02
ont_sample_names: Patient_1,Patient_2
```

Validate and run:

```bash
nextflow run main.nf -stub-run --docker -params-file params/my_ont.yml
nextflow run main.nf --docker -params-file params/my_ont.yml -resume
```
