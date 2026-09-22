# Find your way around the repository

## For your first analysis

Start with the [README](https://github.com/cfarkas/oncotracer#readme),
[install OncoTracer](installation.md), then use the [browser setup](setup.md).
Run analyses in your own project folders. The source checkout supplies the
software and should stay in place after an editable installation.

You do not need to edit source code or run scripts from `bin/`.

## Which folder do I need?

| Folder or file | Purpose | Who usually needs it? |
| --- | --- | --- |
| `README.md` | Installation, uninstall and demo links | Everyone |
| `docs/` | Guides published on this documentation site | Users and contributors |
| `examples/` | Public-data manifests, checksums and example inputs | Tutorial users |
| `params/` | Advanced configuration templates | Users editing YAML manually |
| `environments/` | Conda tool specifications | Users adding optional tools |
| `oncotracer` | Launcher from the source checkout | Installation and development |
| `oncotracer_cli/` | Python application and browser interface | Developers |
| `bin/` | Analysis helpers called by OncoTracer | Developers |
| `scripts/` | Build, release and maintenance tools | Contributors |
| `tests/` | Automated validation and browser checks | Contributors |
| `Dockerfile`, `docker-compose.yml` | Container build and runtime definitions | Container maintainers |
| `provenance/` | Recorded software provenance | Reproducibility reviewers |

## Choose an example

Use [QuickStart 1](quick_start.md) first. It walks through small public Illumina
and ONT inputs. [QuickStart 2](public_cohort.md) covers three Illumina libraries;
the [full tutorial](full_tutorial.md) covers a larger archive.

The [examples index](https://github.com/cfarkas/oncotracer/tree/main/examples)
links each dataset to its guide. Example manifests describe public or explicitly
synthetic data; they are not a place to add private patient files.

## Where does my work go?

Browser setup writes `config/run.yml` and sample tables in the project folder
you select. Analysis writes `results/` there, and prepared reference files are
reused from the configured reference folder. Your FASTQs stay in their original
location. See [output files](outputs.md) and [uninstall](uninstall.md).

For development, see the [developer guide](developer_guide.md).
