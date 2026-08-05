"""Source provenance embedded by :mod:`scripts.build_native_binary`.

The repository copy intentionally contains unbound values. A source build
from a Git checkout replaces this module inside the staged zipapp with values
for the exact commit being packaged. Container/release builds can supply the
same values explicitly when the build context does not contain ``.git``.
"""

from __future__ import annotations


BUILD_METADATA_SCHEMA = "oncotracer-build-metadata-v1"
SOURCE_SHA256_DEFINITION = (
    "sha256(git -c tar.umask=0002 archive --format=tar COMMIT)"
)
SOURCE_COMMIT: str | None = None
SOURCE_SHA256: str | None = None
SOURCE_TREE_DIRTY: bool | None = None
SOURCE_METADATA_ORIGIN: str | None = None
ONCOTRACER_SOURCE_COMMIT = SOURCE_COMMIT
ONCOTRACER_SOURCE_SHA256 = SOURCE_SHA256
PROVENANCE_PAYLOAD_PATH = "payload/provenance/native-v2-sources.json"
PROVENANCE_PAYLOAD_SHA256: str | None = None
