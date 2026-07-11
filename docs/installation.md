# Installation

OncoTracer requires Linux, Java/Nextflow, Git, and one container runtime. Docker is the simplest route for a workstation.

```bash
git --version                                                     # confirm that Git is installed
java -version                                                     # confirm Java 17 or newer is available
curl -s https://get.nextflow.io | bash                            # download the Nextflow launcher
mkdir -p $HOME/bin                                                # create a personal executable directory
mv nextflow $HOME/bin/                                            # install the launcher without administrator privileges
export PATH=$HOME/bin:$PATH                                       # make Nextflow available in this shell
nextflow -version                                                 # verify the Nextflow installation
docker --version                                                   # verify Docker; use Apptainer/Singularity on HPC
git clone https://github.com/cfarkas/oncotracer.git               # clone OncoTracer
cd oncotracer                                                     # enter the repository; always run main.nf from here
current_dir=$(pwd)                                                # save the absolute repository path
echo $current_dir                                                 # confirm the working directory
docker pull carlosfarkas/oncotracer:latest                        # download the maintained workflow container
nextflow run main.nf --make_test                                  # download public FASTQ tests and create ready-to-run YAML files
```

For HPC, install Apptainer and replace `--docker` with `--singularity` in workflow commands. A Conda fallback remains available with `conda env create -f environment.yml`, `conda activate oncotracer`, and the `--conda` runtime flag.
