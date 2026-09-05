# Uninstall

Use `oncotracer uninstall` to remove selected tools.
**No command below removes project data or results.**

## Remove the analysis tools

Preview the exact managed Conda paths first:

```bash
oncotracer uninstall --conda --dry-run
```

For a custom installation, add `--prefix /absolute/path/to/envs` — the parent
containing `core`, `qdnaseq`, `ichorcna`, `classifier`, and `gistic`.

Choose one removal method:

```bash
# Remove from the installed paths, keeping a recovery folder.
oncotracer uninstall --conda --yes

# Or permanently remove the verified tools and reclaim their disk space.
oncotracer uninstall --conda --yes --purge
```

Without `--yes`, uninstall only previews. `--dry-run` always wins over `--yes`.
The default recovery folder is printed; it still occupies disk space. Do not run
the two removal commands consecutively: they are alternatives.

Uninstall refuses active environments, foreign folders, symlinked targets,
changed installation inventories, and interrupted installations. It preserves
unrelated folders even inside a managed parent. A managed Poetry runtime inside
that parent is removed with the Conda tools.

Saved installation settings are retained. Reinstall the backend before using it
again. To restore a recovery copy, move its named entries back to the original
paths listed in `uninstall.json`; do not overwrite a newer installation.

## Remove a container or launcher

For a managed Singularity/Apptainer image:

```bash
oncotracer uninstall --singularity --sif /absolute/path/oncotracer.sif --dry-run
oncotracer uninstall --singularity --sif /absolute/path/oncotracer.sif --yes --purge
```

For a copied standalone executable, remove it **last**:

```bash
oncotracer uninstall --launcher /absolute/path/bin/oncotracer --dry-run
oncotracer uninstall --launcher /absolute/path/bin/oncotracer --yes --purge
```

This recognizes copied OncoTracer executables, not arbitrary files. A
system-owned path may require administrator help; uninstall does not escalate
permissions automatically.

For a pip/editable installation, activate the environment used to install it:

```bash
python -m pip uninstall oncotracer
```

For Docker, remove only the OncoTracer image you installed, after stopping its
containers:

```bash
docker image rm ghcr.io/cfarkas/oncotracer:2.0.0
```

Do not use a system-wide Docker prune. Conda itself, Python, Docker/Apptainer,
reference files, model caches, FASTQs, POD5s, BAMs, YAML and analysis results are
left alone.
