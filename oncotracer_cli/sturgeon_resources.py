"""User-authorized Sturgeon model acquisition; never bundled or auto-accepted."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import tempfile
import zipfile

from .methylation import SUPPORTED_CLASSIFIER_INTERFACE_COMMITS
from .runtime import (OncoTracerError, atomic_write_json, download, exclusive_lock,
                      sha256_file, utc_now)

COMMIT = SUPPORTED_CLASSIFIER_INTERFACE_COMMITS["sturgeon"]
LICENSE_URL = "https://github.com/UMCUGenetics/sturgeon/blob/main/LICENSE"
LICENSE_TEXT_URL = "https://raw.githubusercontent.com/UMCUGenetics/sturgeon/main/LICENSE"
MODEL_URL = "https://www.dropbox.com/s/yzca4exl40x9ukw/general.zip?dl=1"
PROBES_URL = f"https://raw.githubusercontent.com/UMCUGenetics/sturgeon/{COMMIT}/sturgeon/include/static/probes_hg38.bed"


def validate_model(path):
    """Check the upstream archive contract without extracting/executing its contents."""
    try:
        with zipfile.ZipFile(path) as archive:
            required = {"model.onnx", "decoding.json", "probes.csv"}
            if not required.issubset(archive.namelist()):
                raise ValueError("missing model.onnx, decoding.json or probes.csv")
            if any(archive.getinfo(name).file_size == 0 for name in required):
                raise ValueError("empty required member")
            if archive.getinfo("decoding.json").file_size > 1024 * 1024:
                raise ValueError("oversized class dictionary")
            if not isinstance(json.loads(archive.read("decoding.json")), dict):
                raise ValueError("invalid class dictionary")
            if archive.testzip():
                raise ValueError("archive CRC mismatch")
    except (OSError, ValueError, zipfile.BadZipFile, KeyError) as error:
        raise OncoTracerError(f"Invalid Sturgeon model archive: {error}") from error


def prepare_sturgeon(*, acknowledged, cache=None):
    if acknowledged is not True:
        raise OncoTracerError("Read and acknowledge the applicable Sturgeon license before downloading: " + LICENSE_URL)
    root = Path(cache or Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) /
                "oncotracer/models/sturgeon").expanduser().absolute()
    if root.is_symlink():
        raise OncoTracerError("Sturgeon model cache must not be a symlink")
    root.mkdir(parents=True, exist_ok=True)
    with exclusive_lock(root / ".prepare.lock"):
        receipt_path = root / "resources.json"
        if receipt_path.is_symlink():
            raise OncoTracerError("Sturgeon resource receipt must not be a symlink")
        receipt = json.loads(receipt_path.read_text()) if receipt_path.exists() else None
        if receipt:
            for key, filename in (("sturgeon_model", "general.zip"), ("sturgeon_probes", "probes_hg38.bed"), ("license", "LICENSE.txt")):
                path = root / filename
                if (path.is_symlink() or not path.is_file() or
                        sha256_file(path) != receipt.get(key + "_sha256")):
                    raise OncoTracerError("Sturgeon cached resources changed; choose a new cache or restore the recorded files.")
            if receipt.get("interface_commit") != COMMIT:
                raise OncoTracerError("Sturgeon cache belongs to another interface version; use a new cache.")
        else:
            if any((root / name).exists() for name in ("general.zip", "probes_hg38.bed", "LICENSE.txt")):
                raise OncoTracerError("Unverified Sturgeon files already exist in this cache; select them manually or use a new cache.")
            with tempfile.TemporaryDirectory(prefix=".download-", dir=root) as directory:
                staging = Path(directory)
                license_file = download(LICENSE_TEXT_URL, staging / "LICENSE.txt")
                model = download(MODEL_URL, staging / "general.zip")
                validate_model(model)
                probes = download(PROBES_URL, staging / "probes_hg38.bed")
                with probes.open() as handle:
                    first = next((line for line in handle if line.strip() and not line.startswith("#")), "")
                fields = first.split()
                # Upstream hg38 BED has a header; both header and coordinate forms are accepted.
                if len(fields) < 3 or not (fields[1].isdigit() or "start" in first.lower()):
                    raise OncoTracerError("Downloaded Sturgeon probes are not a BED coordinate table")
                receipt = {"schema": "oncotracer-sturgeon-resources-v1", "interface_commit": COMMIT,
                           "license_url": LICENSE_URL, "license_acknowledged": True,
                           "acknowledged_at": utc_now(), "model_url": MODEL_URL, "probes_url": PROBES_URL}
                for key, source in (("sturgeon_model", model), ("sturgeon_probes", probes), ("license", license_file)):
                    receipt[key + "_sha256"] = sha256_file(source)
                    os.replace(source, root / source.name)
                atomic_write_json(receipt_path, receipt)
        values = {"sturgeon_model": str(root / "general.zip"),
                  "sturgeon_probes": str(root / "probes_hg38.bed"),
                  "sturgeon_license_acknowledged": True,
                  "sturgeon_license_url": LICENSE_URL,
                  "sturgeon_license_acknowledged_at": receipt["acknowledged_at"],
                  "sturgeon_resource_receipt": str(receipt_path),
                  **{key: value for key, value in receipt.items() if key.endswith("_sha256")}}
        executable = shutil.which("sturgeon")
        if executable:
            values["sturgeon_executable"] = executable
        return values
