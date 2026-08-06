# Legacy v1.1

The immutable `v1.1` tag contains the historical Nextflow implementation and its original documentation. It remains available for reproducing earlier analyses and as the independent baseline used by the v2 parity gate.

New analyses should use the global v2 `oncotracer` executable. The v1.1 host-side Java/Nextflow launcher requirement, nested SAMURAI workflow, and Nextflow work directories are not part of the v2 orchestration runtime. Managed v2 backends may still bundle Java for Picard duplicate marking.
