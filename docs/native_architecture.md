# Native architecture

OncoTracer v2 is an orchestration application, not a reimplementation of BWA, samtools, Picard, qDNAseq, HMMcopy, ichorCNA, or GISTIC2.

## Stage graph

Each native stage has explicit inputs, outputs, argument-array command, tool environment, validation, trace record, and resume signature. The Python engine coordinates:

- BWA and Picard for Illumina;
- minimap2 for ONT;
- qDNAseq in its pinned R 4.1 environment;
- HMMcopy and ichorCNA 0.5.1 in their pinned R 4.4 environment;
- the existing BAM boundary-refinement, CNA notation, and plotting implementations;
- the optional CNA classifier, GISTIC2, knowledge enrichment, pathology concordance, HTML/PDF reports, and clinician summaries.

The optional classifier is a direct native stage graph over its versioned Python scripts and assets. No `.nf` file is present in the release executable or container.

## Single-file executable

`scripts/build_native_binary.py` creates a compressed Python zipapp containing the CLI and the complete versioned payload (`bin`, examples, parameter templates, environment definitions, and stable source-input provenance). The build embeds the exact Git commit and deterministic `git archive` SHA-256. At first use, payload files are staged and published atomically under `$XDG_CACHE_HOME/oncotracer/2.0.0/<executable-sha256>/payload`. The complete executable SHA-256 gives two different v2.0.0 binaries separate cache roots and locks.

On every reuse, OncoTracer independently derives the expected payload inventory from the executable and verifies each canonical path, file type, normalized mode, size, and SHA-256. Symlinks, special files, missing paths, and extras cannot be accepted as a valid cache. An explicit `--root` or `ONCOTRACER_ROOT` containing `bin/scripts` takes precedence over extraction.

All standalone `--dry-run` commands use a process-scoped temporary payload and remove it after success, validation failure, or interruption. They do not populate the persistent XDG cache or create installation configuration, result directories, Conda environments, container images, or SIF files.

## Installer ownership boundary

Managed Conda/Poetry installations use a strict root marker plus one strict
marker for each fixed child. The records bind canonical paths, a random install
ID, the environment or lock-file digest, exact file inventory, and OncoTracer
source identity. An ownership-checked sibling lock serializes operations. For
each changed child, an authenticated rollback journal records the prior state,
the exact-owned old child moves to a same-filesystem backup, and the replacement
is created directly at its final canonical prefix. Semantic probes and the
inventory run at that final path before commit; unrelated siblings are outside
the transaction. Populated unowned paths, marker mismatches, symlinked paths,
foreign entries, and active prefixes fail before replacement. A shared lock
is held for the complete lifetime of each managed analysis, so the exclusive
installer cannot publish beneath a consumer. A durable staging journal exists
before transaction creation or package-manager execution. If SIGKILL leaves an
unsealed final-prefix tree, recovery restores the prior state and moves the
unknown tree to a reported sibling preservation path instead of deleting it.
After any failed publication, recovery records the restored target in a durable
`rolled_back` journal and seals the exact transaction inventory before retaining
the transaction root, so interruption between root and journal retention resumes.
After commit, the exact inventory-authenticated transaction root is atomically
renamed with no replacement to a transaction-ID-bound retained rollback path.
The canonical journal is then atomically renamed to a target-bound retained
audit record. Recovery accepts either the original transaction name or its
exact retained name, verifies the complete sealed inventory, and resumes this
order idempotently. No installer path recursively deletes transaction contents,
rollback roots, journals, or temporary metadata; later transactions ignore the
retained names. Their exact paths are reported for deliberate administrator
archival or removal after active-use checks.
Poetry's launcher is a fixed isolated `poetry-runtime` child built from an
exact clean checkout in isolated state and installed only by the final target
interpreter; it is never an ambient or checkout-local virtual environment.

Native child processes receive invocation-private HOME, XDG, Matplotlib, and
Fontconfig state below the analysis output's `.oncotracer-native/runtime-cache`.
OncoTracer audits the selected Fontconfig include graph, exposes one private
cache destination, and checks the containment rules before and after each
child. While the shared managed-runtime lock is still held, it also rechecks
every managed child inventory and ownership identity after analysis or doctor
use. These rules cover commands launched by OncoTracer; invoking a managed
prefix's binaries directly is outside that runtime containment boundary.

All final-prefix and backup publications use no-replacement renames. Metadata
updates atomically exchange names and retain the prior bytes under a unique name.

A managed SIF has an adjacent strict sidecar binding its canonical path, image
reference, bytes, install ID, and source identity. Every reuse verifies the
file SHA-256 and executes container `doctor` and `provenance`. Replacement pulls
into a same-directory owned transaction and verifies the candidate before an
atomic, rollback-protected swap. Neither `--force` path adopts or pre-deletes an
unowned destination. Install dry-runs perform the same read-only target checks
but create no locks, markers, transactions, environments, images, or saved
configuration.

The release file can therefore be installed directly:

```bash
sudo install -m 0755 oncotracer /usr/local/bin/oncotracer
oncotracer provenance --json
```

## Native invariant

Normal v2 execution does not invoke Nextflow. The managed Conda and container backends include the Java runtime used internally by Picard for Illumina duplicate marking; users do not need a separate host Java installation. CI parses the source, inspects the built image, executes an offline classifier fixture, and rejects any native command trace containing `nextflow`. The frozen v1.1 workflow is retained only in a separate checkout used to establish parity.
