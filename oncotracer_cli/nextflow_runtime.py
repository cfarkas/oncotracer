"""Keep native libraries extracted by simultaneous Nextflow JVMs separate."""
from contextlib import contextmanager
import os
from pathlib import Path
import tempfile


@contextmanager
def isolated_nextflow_environment(environment=None):
    """Yield child-only overrides; keep persistent work and resume caches intact.

    Older Nextflow distributions bundle a HawtJNI loader which truncates a fixed
    libjansi filename in java.io.tmpdir. Different JVMs must never share that
    extraction directory, including readiness probes and separate projects.
    NXF_TEMP selects java.io.tmpdir without changing global environment variables.
    """
    env = os.environ if environment is None else environment
    parent = env.get('NXF_TEMP') or env.get('TMPDIR') or tempfile.gettempdir()
    parent = Path(parent).expanduser().resolve()
    # Older launchers lose quoting while expanding the Capsule trampoline.
    # Keep the short-lived JVM directory whitespace-free even for spaced projects.
    if any(character.isspace() for character in str(parent)):
        parent = Path('/tmp')
    with tempfile.TemporaryDirectory(prefix='oncotracer-nextflow-', dir=parent) as scratch:
        yield {'NXF_TEMP': scratch}
