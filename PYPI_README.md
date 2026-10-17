# OncoTracer

OncoTracer analyzes Illumina and Oxford Nanopore reads for DNA copy-number changes and optional small variants. Configure samples, choose Fresh or FFPE preservation, and review results through its browser interface. For research use.

## Install

OncoTracer supports 64-bit Linux and Python 3.10–3.13. Install the command in your Python environment:

```bash
pip install oncotracer
```

No Git checkout is required. Pip installs the application and its bundled workflow resources. Choose one backend to prepare and verify the scientific tools before analysis.

With Conda available:

```bash
oncotracer install --conda
oncotracer --interactive --backend conda
```

With Docker running and accessible:

```bash
oncotracer install --docker
oncotracer --interactive --backend docker
```

Choose the sequencing platform and **Fresh** or **FFPE** inside the browser, then select inputs, settings and a project folder. Save and check before choosing Run. Optional callers, models and licensed annotation resources are configured separately.

For concurrent analyses, open another terminal and use a different project/output folder. Sessions select available browser ports; allocate CPU and memory for each run.

## Uninstall

To remove an additional global launcher while keeping scientific tools and projects:

```bash
oncotracer uninstall --global-launcher --yes
python -m pip uninstall oncotracer
```

Use the Python environment where you installed the package for the pip command. To remove managed scientific tools too, first preview `oncotracer uninstall --conda --dry-run` and then use `oncotracer uninstall --conda --yes`.

## Documentation

- [Installation and backend requirements](https://cfarkas.github.io/oncotracer/installation/)
- [Browser setup](https://cfarkas.github.io/oncotracer/setup/)
- [Variants and FFPE resources](https://cfarkas.github.io/oncotracer/variants/)
- [Nextflow entry point](https://cfarkas.github.io/oncotracer/nextflow_entry/)
- [Source and issues](https://github.com/cfarkas/oncotracer)
