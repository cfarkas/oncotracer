# Uninstall

Use `oncotracer uninstall` to preview your saved backend, or select tools explicitly.
`oncotracer --uninstall` is an alias with the same options. If an older executable
rejects the flag, use the `uninstall` subcommand; if it also rejects that, update
OncoTracer using the [installation instructions](installation.md).

**Removal needs `--yes`. Reclaiming disk space also needs `--purge`.**
Integrity checks show progress and can take several minutes for large environments.
**No command below removes project data or results.**

## Conda: remove the managed analysis tools

Preview the exact managed Conda paths first:

```bash
oncotracer uninstall --conda --dry-run
```

For a custom installation, use the same parent directory passed to
`oncotracer install --conda --prefix`. For example, if you installed into
`/data/oncotracer-tools`, preview with:

```bash
oncotracer uninstall --conda --prefix /data/oncotracer-tools --dry-run
```

This directory contains `core`, `qdnaseq`, `ichorcna`, `classifier`, and `gistic`.
Include the same `--prefix` in your removal command below.
Separately created `variants` and `ffperase` environments are not part of this
managed installation; remove those separately with Conda if no other analyses use them.

Choose one removal method:

```bash
# Remove from the installed paths, keeping a recovery folder.
oncotracer uninstall --conda --yes

# Or permanently remove the verified tools and reclaim their disk space.
oncotracer uninstall --conda --yes --purge
```

The short summary lists the exact paths and any recovery folder. Use `--json` for
a machine-readable plan or result.

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

## Docker: remove the installed image

After stopping containers that use it, remove the image installed by the
[Docker installation route](installation.md#docker):

```bash
docker image rm carlosfarkas/oncotracer:fastq-variants-20260922
```

If you installed another tag, use that exact name instead. Removing a tag reclaims
its layers only when no other image references them. This leaves mounted project
data and results intact; a system-wide Docker prune is unnecessary.

## Remove the launcher last

For a pip/editable installation, activate the environment used to install it:

```bash
source /absolute/path/to/oncotracer-env/bin/activate
python -m pip uninstall oncotracer
```

This removes the command from that environment. It leaves the environment and
source folders in place.

For a copied standalone executable:

```bash
oncotracer uninstall --launcher /absolute/path/bin/oncotracer --dry-run
oncotracer uninstall --launcher /absolute/path/bin/oncotracer --yes --purge
```

This recognizes copied OncoTracer executables, not arbitrary files. A
system-owned path may require administrator help; uninstall does not escalate
permissions automatically.

## Singularity/Apptainer

For a managed Singularity/Apptainer image:

```bash
oncotracer uninstall --singularity --sif /absolute/path/oncotracer.sif --dry-run
oncotracer uninstall --singularity --sif /absolute/path/oncotracer.sif --yes --purge
```

Conda itself, Python, Docker/Apptainer, reference files, model caches, FASTQs,
POD5s, BAMs, YAML and analysis results are left alone.

## If uninstall did not work

| What you see | What to do |
| --- | --- |
| `Preview only` and tools are still installed | Add `--yes` to the same command. Remove `--dry-run` when ready. |
| A recovery folder remains and disk usage is unchanged | `--yes` keeps a recovery copy. Choose `--yes --purge` at removal time to reclaim space. A later uninstall cannot purge an already removed installation's recovery folder. |
| No target is recorded, or saved settings are invalid | Supply `--conda --prefix /absolute/path/to/envs`, `--singularity --sif /absolute/path/oncotracer.sif`, or `--launcher /absolute/path/bin/oncotracer`. Fully specified targets do not require saved settings. |
| An active process is reported | Finish or stop the analysis, leave the tools directory, and deactivate any environment being removed. Run uninstall from a separate CLI environment or standalone executable. |
| Ownership or file-integrity checks fail | The target is an older, unmanaged, moved, or modified installation. Automatic removal requires the installer's ownership records and unchanged managed files; inspect the reported path before removing it with its original package manager. |

An interrupted installation must first be recovered by rerunning its matching
`oncotracer install` command with the original target. `--force` does not bypass
uninstall checks, and uninstall has no `--force` option.
