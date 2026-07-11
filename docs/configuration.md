# Configuration

OncoTracer uses manually editable, versioned YAML templates in `params/`.

Start here:

- [YAML Basics](configuration/yaml_basics.md)
- [Illumina YAML](configuration/illumina.md)
- [ONT YAML](configuration/ont.md)
- [Pathology and Classifier](configuration/pathology.md)
- [Advanced Refinement Settings](configuration/refinement.md)
- [Complete Parameter Reference](configuration/parameter_reference.md)

Copy and validate:

```bash
cp params/illumina.minimal.yml params/my_illumina.yml
nextflow run main.nf -stub-run --docker -params-file params/my_illumina.yml
```
