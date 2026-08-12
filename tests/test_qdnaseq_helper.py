#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "bin" / "scripts" / "prepare_qdnaseq_bin_data.sh"


class QdnaSeqHelperTests(unittest.TestCase):
    def test_exact_rscript_clean_environment_and_empty_staging(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            exact_rscript = root / "qdnaseq" / "bin" / "Rscript"
            exact_rscript.parent.mkdir(parents=True)
            exact_rscript.write_text(
                """#!/usr/bin/env bash
set -Eeuo pipefail
{
  printf 'R_HOME=%s R_LIBS=%s R_LIBS_USER=%s R_LIBS_SITE=%s ARGS=' \
    "${R_HOME-unset}" "${R_LIBS-unset}" "${R_LIBS_USER-unset}" "${R_LIBS_SITE-unset}"
  printf '%q ' "$@"
  printf '\\n'
} >> "$R_LOG"
[[ "${1:-}" == "--vanilla" && "${2:-}" == "-" ]] || exit 90
if [[ $# -eq 3 ]]; then
  [[ -s "$3" ]]
elif [[ $# -eq 5 ]]; then
  cp -- "$3" "$4"
else
  exit 91
fi
""",
                encoding="utf-8",
            )
            exact_rscript.chmod(0o755)

            foreign_bin = root / "foreign" / "bin"
            foreign_bin.mkdir(parents=True)
            foreign_rscript = foreign_bin / "Rscript"
            foreign_rscript.write_text(
                '#!/usr/bin/env bash\ntouch "$FOREIGN_MARKER"\nexit 97\n',
                encoding="utf-8",
            )
            foreign_rscript.chmod(0o755)
            fake_curl = foreign_bin / "curl"
            fake_curl.write_text(
                """#!/usr/bin/env bash
set -Eeuo pipefail
output=''
while [[ $# -gt 0 ]]; do
  case "$1" in
    --output) output="$2"; shift 2 ;;
    *) shift ;;
  esac
done
[[ -n "$output" ]]
printf 'pinned-qdnaseq-source\\n' > "$output"
printf 'download\\n' >> "$CURL_LOG"
if [[ -n "${RACE_TARGET:-}" ]]; then
  printf 'protected-existing-cache-file\\n' > "$RACE_TARGET"
fi
""",
                encoding="utf-8",
            )
            fake_curl.chmod(0o755)
            fake_sha = foreign_bin / "sha256sum"
            fake_sha.write_text(
                """#!/usr/bin/env bash
set -Eeuo pipefail
path="${@: -1}"
if [[ "$path" == *.rda ]]; then
  printf '450b77a74dbba381e2f664334de90e41ec5e9eb6a5a8946d036c4b3534254d98  %s\\n' "$path"
else
  /usr/bin/sha256sum -- "$path"
fi
""",
                encoding="utf-8",
            )
            fake_sha.chmod(0o755)

            cache = root / "cache"
            r_log = root / "r.log"
            curl_log = root / "curl.log"
            foreign_marker = root / "foreign-rscript-used"
            environment = os.environ.copy()
            environment.update(
                {
                    "PATH": f"{foreign_bin}{os.pathsep}{environment.get('PATH', '')}",
                    "R_HOME": "/foreign/R",
                    "R_LIBS": "/foreign/libs",
                    "R_LIBS_USER": "/foreign/user",
                    "R_LIBS_SITE": "/foreign/site",
                    "R_LOG": str(r_log),
                    "CURL_LOG": str(curl_log),
                    "FOREIGN_MARKER": str(foreign_marker),
                }
            )
            command = [
                str(HELPER),
                "--rscript",
                str(exact_rscript),
                "--binsize",
                "100",
                "--cache-dir",
                str(cache),
            ]

            first = subprocess.run(
                command, text=True, capture_output=True, env=environment, check=False
            )
            self.assertEqual(first.returncode, 0, first.stderr)
            rds = Path(first.stdout.strip().splitlines()[-1])
            source = cache / "QDNAseq.hg38.100kbp.SR50.source.rda"
            provenance = Path(f"{rds}.provenance.tsv")
            self.assertTrue(rds.is_file())
            self.assertTrue(source.is_file())
            self.assertFalse(foreign_marker.exists())
            fields = dict(
                line.split("\t", 1)
                for line in provenance.read_text(encoding="utf-8").splitlines()[1:]
            )
            self.assertEqual(
                fields["source_rda_sha256"],
                "450b77a74dbba381e2f664334de90e41ec5e9eb6a5a8946d036c4b3534254d98",
            )
            self.assertEqual(
                fields["rds_sha256"], hashlib.sha256(rds.read_bytes()).hexdigest()
            )
            for line in r_log.read_text(encoding="utf-8").splitlines():
                self.assertIn(
                    "R_HOME=unset R_LIBS=unset R_LIBS_USER=unset R_LIBS_SITE=unset",
                    line,
                )

            validated = subprocess.run(
                [*command, "--validate-only"],
                text=True,
                capture_output=True,
                env=environment,
                check=False,
            )
            self.assertEqual(validated.returncode, 0, validated.stderr)
            self.assertEqual(Path(validated.stdout.strip()), rds)
            self.assertEqual(
                curl_log.read_text(encoding="utf-8").splitlines(), ["download"]
            )

            missing_cache = root / "missing-validate-only"
            missing = subprocess.run(
                [*command[:-1], str(missing_cache), "--validate-only"],
                text=True,
                capture_output=True,
                env=environment,
                check=False,
            )
            self.assertNotEqual(missing.returncode, 0)
            self.assertIn("cache does not exist", missing.stderr)
            self.assertFalse(missing_cache.exists())

            repeated = subprocess.run(
                command, text=True, capture_output=True, env=environment, check=False
            )
            self.assertNotEqual(repeated.returncode, 0)
            self.assertIn("must be empty", repeated.stderr)
            self.assertEqual(
                curl_log.read_text(encoding="utf-8").splitlines(), ["download"]
            )

            second_cache = root / "second-cache"
            second_command = [*command[:-1], str(second_cache)]
            second = subprocess.run(
                second_command,
                text=True,
                capture_output=True,
                env=environment,
                check=False,
            )
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(
                curl_log.read_text(encoding="utf-8").splitlines(),
                ["download", "download"],
            )

            race_cache = root / "race-cache"
            race_target = race_cache / "QDNAseq.hg38.100kbp.SR50.source.rda"
            race_environment = {**environment, "RACE_TARGET": str(race_target)}
            raced = subprocess.run(
                [*command[:-1], str(race_cache)],
                text=True,
                capture_output=True,
                env=race_environment,
                check=False,
            )
            self.assertNotEqual(raced.returncode, 0)
            self.assertIn("refusing to overwrite", raced.stderr)
            self.assertEqual(
                race_target.read_bytes(), b"protected-existing-cache-file\n"
            )
            self.assertFalse((race_cache / "QDNAseq.hg38.100kbp.SR50.rds").exists())
            self.assertFalse(
                (race_cache / "QDNAseq.hg38.100kbp.SR50.rds.provenance.tsv").exists()
            )

    def test_unpinned_download_is_rejected_before_publication(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tools = root / "tools"
            tools.mkdir()
            rscript = tools / "Rscript"
            rscript.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            rscript.chmod(0o755)
            curl = tools / "curl"
            curl.write_text(
                """#!/bin/sh
while [ "$1" != "--output" ]; do shift; done
printf 'wrong-source\\n' > "$2"
""",
                encoding="utf-8",
            )
            curl.chmod(0o755)
            cache = root / "cache"
            environment = os.environ.copy()
            environment["PATH"] = f"{tools}{os.pathsep}/usr/bin:/bin"
            completed = subprocess.run(
                [
                    str(HELPER),
                    "--rscript",
                    str(rscript),
                    "--binsize",
                    "100",
                    "--cache-dir",
                    str(cache),
                ],
                text=True,
                capture_output=True,
                env=environment,
                check=False,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("source SHA-256 mismatch", completed.stderr)
            self.assertEqual(list(cache.iterdir()), [])

    def test_rscript_is_required(self) -> None:
        completed = subprocess.run(
            [
                str(HELPER),
                "--binsize",
                "100",
                "--cache-dir",
                "/tmp/unused-qdnaseq-cache",
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 2)
        self.assertIn("--rscript is required", completed.stderr)


if __name__ == "__main__":
    unittest.main()
