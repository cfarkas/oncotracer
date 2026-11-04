"""Keep unfinished native caller work available after errors or interruption."""
from contextlib import contextmanager
from pathlib import Path
import shutil
import sys
import tempfile

from .runtime import atomic_write_json, utc_now


@contextmanager
def sample_work(root, sample, result):
    work = Path(tempfile.mkdtemp(prefix=f'.{sample}-', dir=root))
    failure = None
    try:
        yield work
    except BaseException as error:
        failure = type(error).__name__ + ': ' + str(error)
        raise
    finally:
        if failure is None and result.get('status') in {'complete', 'not_applicable'}:
            shutil.rmtree(work)
        else:
            result['retained_work_dir'] = str(work)
            try:
                atomic_write_json(work / 'retained_work.json', {
                    'schema': 'oncotracer-retained-variant-work-v1',
                    'sample': sample,
                    'retained_at': utc_now(),
                    'reason': failure or result.get('status', 'unfinished'),
                    'automatic_partial_resume': False,
                    'note': 'Completed published outputs are independently validated on resume. '
                            'Unfinished caller files are preserved for diagnosis and recovery; '
                            'they are not declared complete or silently reused.',
                })
            except OSError:
                # A full disk must not replace the original error or delete evidence.
                pass
            print(f'Unfinished variant work retained for {sample}: {work}', file=sys.stderr, flush=True)
