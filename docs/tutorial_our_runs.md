# Tutorial: Example Runs

Use this page with the public example data listed in [Example Data](example_data.md). The workflow is the same as for your own data: copy a template, edit absolute paths, validate, and run with `-params-file`.

```bash
nextflow run main.nf --docker -params-file params/my_illumina.yml -resume
nextflow run main.nf --docker -params-file params/my_ont.yml -resume
```

Example plots are shown in the [Gallery](gallery.md).
