# QuickStart 2: three HCC1143 libraries

QuickStart 2 downloads all six paired-end FASTQs for three public HCC1143 libraries, validates each exact size and MD5 checksum, creates the three-row tumor sample table, runs Automatic Setup, and performs the complete native Illumina analysis.

```bash
oncotracer install --conda
oncotracer quickstart 2 \
  --backend conda \
  --test-root "$PWD/oncotracer-quickstart2"
```

The libraries are:

| Sample | Treatment | ENA/SRA run |
| --- | --- | --- |
| `HCC1143_DMSO` | 0.05% DMSO | `SRR7085656` |
| `HCC1143_BEZ235` | 1 µM BEZ235 | `SRR7085655` |
| `HCC1143_TRAMETINIB` | 1 µM trametinib | `SRR7085657` |

The repository manifest records URL, filename, bytes, and MD5 for every FASTQ. The release parity workflow archives the generated sample mapping, v1.1 and v2 summaries, event concordance, refined-bin concordance, command trace, and checksums.

## Resume

Repeat the same command. Valid downloaded files, reference indexes, alignments, and content-matched native stages are reused. Use `--force` only when deliberately recomputing outputs.
