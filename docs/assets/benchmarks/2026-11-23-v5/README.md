# OncoTracer v5 study benchmark

The full methods, commands and interpretation are in the versioned guide:
https://github.com/cfarkas/oncotracer/blob/v5.0.0/docs/benchmarks/2026-11-23.md

benchmark_summary.json and cohort_summary.csv: validated completion and cohort resources.
stage_timings.csv: each recorded native/methylation stage and variant envelopes.
nextflow_processes.csv: each Illumina variant subprocess and its recorded resource use.
resource_timeseries.csv: last sample per five-minute interval; summary peaks use full data.
source_evidence.json: hashes of the local source records, without private paths or identifiers.
resource_usage.svg/pdf/png: aggregate CPU, RAM, page-cache-inclusive memory and disk plots.

Benchmark source: 76da6c1b707f3ee14f0dc67f7521e394ff934e5d. The release source and published image
have separate provenance. ONT retains its QC-limited exit and sample exclusions.
Task-time sums overlap and must not be interpreted as cohort wall time.
These data measure execution and output integrity, not diagnostic accuracy.

CPU allocations distinguish physical cores from logical hardware threads. `cpu_topology.json` confirms the host topology at publication; affinity comes from the original run manifest. GPU memory/activity series include tracked analysis processes only. Allocation, mean CPU usage, RAM limits, application RAM, page cache and GPU VRAM are separate quantities.
