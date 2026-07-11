# YAML Basics

YAML is plain text:

```yaml
setting_name: value
```

Examples:

```yaml
mode: illumina
outdir: /home/student/oncotracer_project/runs/my_run
run_cna_classifier: false
pathology_csv: null
```

Rules:

- Use spaces, not tabs.
- Keep one setting per line.
- Lines beginning with `#` are comments.
- Use `true` or `false` for yes/no settings.
- Use `null` for an optional value you are not using.
- Prefer absolute Linux paths that start with `/`.
- Do not assume `$HOME` or `~` will be expanded in a YAML value.

Validate YAML and workflow parameter parsing with a stub run. This checks the workflow setup without running the full analysis commands:

```bash
nextflow run main.nf -stub-run --docker -params-file params/my_illumina.yml
```
