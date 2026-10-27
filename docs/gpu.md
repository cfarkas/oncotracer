# GPU usage

OncoTracer can use an NVIDIA GPU for supported inference stages. **GPU usage → Yes** appears in new setup and in **Review and run** for an existing project when an NVIDIA GPU is visible. **No** keeps processing on CPU. CPU sample jobs still run in parallel with the GPU queue.

## Install and check

With the current GitHub application installed, choose your backend:

```bash
oncotracer install --conda --gpu
oncotracer doctor --backend conda --gpu
oncotracer --interactive --backend conda
```

For Docker, install the NVIDIA driver and [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html) on the host first:

```bash
oncotracer install --docker --gpu
oncotracer doctor --backend docker --gpu
oncotracer --interactive --backend docker
```

Installation tests actual TensorFlow and PyTorch CUDA computations. Seeing a GPU in the hardware inventory alone does not establish that its driver and runtime work together. The optional Conda runtimes keep CUDA TensorFlow and CUDA PyTorch separate from the ordinary scientific environments. `oncotracer uninstall --conda --yes --purge` removes these managed GPU runtimes too. Docker contains them in the image.

Allow roughly 25 GiB additional installation/build space for the optional GPU runtimes, beyond ordinary OncoTracer requirements.

The tested configuration uses CUDA 12.2 TensorFlow 2.15.1 and CUDA 12.4 PyTorch 2.6.0 for reports, with the pinned ClairS-TO runtime for its caller. It was exercised on an RTX A5000 with driver 560.35.05. Other GPU/driver combinations must pass `doctor --gpu`. Reserved GPU execution is available through Docker and native Conda/Poetry; Apptainer/Singularity uses CPU.

## Choose GPU usage

Run `oncotracer --detect-resources` (or `--detect_resources`) before setup to inspect CPU sample-job suggestions and visible GPU memory. In the browser, select **GPU usage → Yes**, then save/check and Run. The same setting is offered when continuing an existing analysis.

Nextflow opens the same interface:

```bash
nextflow run main.nf --interactive --backend docker
```

For headless runs, `--gpu` selects GPU use and `--cpu` overrides a saved GPU choice:

```bash
oncotracer run --config /path/to/project/config/run.yml --backend docker --gpu
```

The saved setting is `gpu_usage: true` or `false`. A saved GPU request is retained if the project is opened on a machine without GPU access; the browser asks you to restore access or explicitly choose CPU.

## Which stages use the GPU?

| Stage | Execution with GPU usage Yes |
| --- | --- |
| ONT Clair3 / ClairS-TO | GPU neural-network inference; preprocessing remains CPU. ClairS-TO GPU execution uses the Docker caller. |
| Illumina variant narrative reports | Local model generation uses the GPU. Scientific evidence and variant calls are unchanged by report generation. |
| ONT POD5 basecalling | Dorado uses one reserved GPU and a bounded batch size. |
| MARLIN methylation classification | TensorFlow inference uses a reserved GPU. |
| BWA/minimap2 alignment, BAM indexing, CNA, GLIMPSE2, bcftools, FFPErase, Modkit and Sturgeon | CPU; these installed workflows have no GPU execution path. |

GPU selection does not turn CPU tools into GPU tools. The largest Illumina steps can therefore remain CPU-bound. Required models, reference files and license acknowledgements are unchanged.

## Sharing the machine

Each GPU admits one heavy OncoTracer stage at a time across the same user's native and Docker projects. Other samples wait for the GPU while CPU stages can continue. On multi-GPU hosts, stages can use separate visible devices. `CUDA_VISIBLE_DEVICES` restricts the selection.

Admission rechecks free VRAM while holding the device lock, then reserves **at least 4 GiB or 20% of total VRAM**, whichever is larger, in addition to current desktop/other application use. Caller worker counts and framework allocations are bounded; Dorado uses an explicit batch size. Manual sample counts do not override the GPU queue. The terminal reports waiting and admitted stages.

These limits reduce memory pressure; they cannot prevent an unrelated application or another user's process from consuming more VRAM later. If the requested model cannot fit, OncoTracer reports the stage as incomplete and retains completed work. It does not silently switch that stage to CPU. Stop cancels waiting jobs and releases their locks.

## Validation and precision

CPU/GPU comparisons used a small real ONT region. Clair3 produced the same 1,259 variant keys, genotypes and FILTER values; one LowQual record differed in QUAL by 0.01. ClairS-TO produced the same 58 SNVs and 3 indels; small numerical score differences were recorded. Native and Docker Clair3 GPU variant records matched exactly on that fixture. These are regression checks, not whole-cohort equivalence or a general speed benchmark.

TensorFloat-32 is disabled for caller inference. Versioned repairs bound upstream caller GPU workers, preserve the reserved device, and place ClairS-TO indel models on the selected device. Installation records source hashes and rejects unfamiliar caller code. The models and calling thresholds are unchanged. See [acknowledgements](citation_research_use.md).
