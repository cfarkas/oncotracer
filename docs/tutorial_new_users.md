# Beginner Tutorial

This tutorial is for students who are new to Linux, YAML, Nextflow, and containers.

```bash
pwd
ls -lh
realpath .
```

Read [What Is a Path?](getting_started/paths.md) before editing YAML.

```bash
git clone https://github.com/cfarkas/oncotracer.git
cd oncotracer
cp params/illumina.minimal.yml params/my_illumina.yml
find /home/student/data -maxdepth 2 -type f | head
nano params/my_illumina.yml
nextflow run main.nf -stub-run --docker -params-file params/my_illumina.yml
nextflow run main.nf --docker -params-file params/my_illumina.yml -resume
```

Use `--singularity` instead of `--docker` on many HPC systems.
