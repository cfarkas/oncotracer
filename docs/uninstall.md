# Uninstall

Remove the analysis tools or container image you installed, then remove the Python application. Finish active analyses first and run removal outside any scientific environment being removed. Keep the application available until backend and extra-launcher removal is complete.

These commands preserve input data, references, models, project settings and results.

## Conda: remove the managed analysis tools

Permanently remove the verified environments:

```bash
oncotracer uninstall --conda --yes --purge
```

This includes the managed core, QDNAseq, ichorCNA, classifier/reporting, GISTIC and variant environments, plus an installation-managed Poetry runtime when present. It also removes an extra global command belonging to the same application and tools. Pip’s application and console entry remain installed.

For a custom installation, include the same parent prefix used at installation:

```bash
oncotracer uninstall --conda --prefix /data/oncotracer-tools --yes --purge
```

Independently created `ffperase`, `strelka2`, ONT or other Conda environments are not part of this removal. Remove them separately with their original package manager when no other analysis uses them. Conda itself remains installed.

## Docker: remove the installed image

After stopping containers that use the image:

```bash
docker image rm carlosfarkas/oncotracer:analysis-choice-20261019
```

Use the exact image/tag you installed. **This removes only that Docker image/tag.** It does not uninstall the pip application, remove an extra global launcher, clear saved installation settings, or delete projects, references, models or results. Docker itself remains installed.

Layers shared with other image tags or containers remain, so removing a tag may not reclaim all its disk space. If Docker reports a container still references the image, handle that container separately; this command does not remove containers. Continue with the extra global command, if present, and Python application below.

## Global command

Backend installation may create an additional OncoTracer-owned `~/.local/bin/oncotracer` launcher. Matching Conda or SIF removal already removes this launcher. If it remains—for example, after removing a Docker image—remove it **before uninstalling the Python application**:

```bash
oncotracer uninstall --global-launcher --yes --purge
```

Use this only for the extra launcher created by OncoTracer. A pip-owned console entry is removed by pip instead; if this command says the entry belongs to pip, continue with the next section. A launcher belonging to another application installation is preserved. Run its owning installation to remove it.

## Python application

Use the Python environment in which you ran `pip install oncotracer`:

```bash
python -m pip uninstall oncotracer
```

If you used a virtual environment, activate it first, for example with `source /absolute/path/to/oncotracer-env/bin/activate`.

Pip removes the application and its own console entry. It leaves the Python environment, scientific environments, Docker images, any separate OncoTracer-owned launcher, and editable source checkouts in place. Remove an extra launcher before pip so its owning application is still available.

## Other installation routes

For a managed Singularity/Apptainer image:

```bash
oncotracer uninstall --singularity --sif /absolute/path/oncotracer.sif --yes --purge
```

This removes the verified SIF and its ownership sidecar, plus a matching extra global launcher. It leaves Apptainer/Singularity installed.

For a copied standalone executable:

```bash
oncotracer uninstall --launcher /absolute/path/bin/oncotracer --yes --purge
```

This recognizes copied OncoTracer executables and removes a matching extra launcher. It does not select pip-owned commands or arbitrary files. System-owned paths may require administrator help; uninstall does not escalate permissions.

## Settings and user data

Saved installation settings remain at `$XDG_CONFIG_HOME/oncotracer/config.json`, normally `~/.config/oncotracer/config.json`. Removing a Docker image or Python package does not reset these settings. Reinstall the selected backend before using it again.

All component removals leave FASTQs, POD5s, BAMs, project YAML, analysis outputs, references and model caches intact. Existing installer backups, logs and caches are not cleared by these commands. `--purge` permanently deletes the verified components selected by that uninstall, without retaining a new recovery copy.

## If removal is refused

| Message | Action |
| --- | --- |
| No installed target is recorded | Supply the original `--prefix`, `--sif`, or `--launcher` path. |
| An active process is using the tools | Finish or stop that analysis, deactivate the scientific environment, and run removal from the application environment. |
| Ownership or file checks fail | Inspect the reported installation. Moved, modified or independently installed files are not removed automatically. |
| An interrupted installation is reported | Rerun its matching install command with the original target before retrying removal. |

Unrelated folders and commands are preserved. There is no uninstall `--force` option to bypass ownership or active-process checks.
