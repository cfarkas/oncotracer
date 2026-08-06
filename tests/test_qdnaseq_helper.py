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
    def test_exact_rscript_clean_environment_and_provenance_cache(self) -> None:
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
                "#!/usr/bin/env bash\ntouch \"$FOREIGN_MARKER\"\nexit 97\n",
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
""",
                encoding="utf-8",
            )
            fake_curl.chmod(0o755)

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

            first = subprocess.run(command, text=True, capture_output=True, env=environment, check=False)
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
                fields["source_commit"],
                "cf7c07e39de0ac64a9c38cb030cba4626e2aae83",
            )
            self.assertEqual(fields["source_rda_sha256"], hashlib.sha256(source.read_bytes()).hexdigest())
            self.assertEqual(fields["rds_sha256"], hashlib.sha256(rds.read_bytes()).hexdigest())
            for line in r_log.read_text(encoding="utf-8").splitlines():
                self.assertIn(
                    "R_HOME=unset R_LIBS=unset R_LIBS_USER=unset R_LIBS_SITE=unset",
                    line,
                )

            second = subprocess.run(command, text=True, capture_output=True, env=environment, check=False)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(curl_log.read_text(encoding="utf-8").splitlines(), ["download"])

            lines = provenance.read_text(encoding="utf-8").splitlines()
            provenance.write_text(
                "\n".join(
                    "rds_sha256\t" + "0" * 64 if line.startswith("rds_sha256\t") else line
                    for line in lines
                )
                + "\n",
                encoding="utf-8",
            )
            repaired = subprocess.run(command, text=True, capture_output=True, env=environment, check=False)
            self.assertEqual(repaired.returncode, 0, repaired.stderr)
            self.assertEqual(curl_log.read_text(encoding="utf-8").splitlines(), ["download", "download"])
            repaired_fields = dict(
                line.split("\t", 1)
                for line in provenance.read_text(encoding="utf-8").splitlines()[1:]
            )
            self.assertEqual(repaired_fields["rds_sha256"], hashlib.sha256(rds.read_bytes()).hexdigest())

    def test_rscript_is_required(self) -> None:
        completed = subprocess.run(
            [str(HELPER), "--binsize", "100", "--cache-dir", "/tmp/unused-qdnaseq-cache"],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 2)
        self.assertIn("--rscript is required", completed.stderr)


if __name__ == "__main__":
    unittest.main()
