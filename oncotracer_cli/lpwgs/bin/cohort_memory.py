"""Process-owned RAM admission for local report models across sample workflows."""
import contextlib
import fcntl
import os
from pathlib import Path
import time


@contextlib.contextmanager
def report_slot(config):
    directory = config.get('llm_memory_pool')
    if not directory:
        yield
        return
    slots = config.get('llm_memory_slots')
    if type(slots) is not int or slots < 1:
        raise ValueError('Shared report memory requires a positive slot count')
    root = Path(directory)
    if root.is_symlink():
        raise ValueError('Report memory pool must be a physical directory')
    root.mkdir(parents=True, exist_ok=True)
    flags = os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0)
    with os.fdopen(os.open(root/'capacity', flags, 0o600), 'r+') as capacity:
        fcntl.flock(capacity, fcntl.LOCK_EX)
        previous = capacity.read().strip()
        if previous and previous != str(slots):
            raise ValueError('Shared report memory capacity changed during the cohort')
        if not previous:
            capacity.write(str(slots)); capacity.flush()
        fcntl.flock(capacity, fcntl.LOCK_UN)
    acquired = None
    started = time.monotonic(); announced = started
    try:
        while acquired is None:
            for index in range(slots):
                descriptor = os.open(root/('slot-'+str(index)), flags, 0o600)
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    os.close(descriptor)
                else:
                    acquired = descriptor
                    break
            if acquired is None:
                if time.monotonic() - announced >= 30:
                    print('Waiting for report model RAM; other sample analyses continue.', flush=True)
                    announced = time.monotonic()
                time.sleep(.25)
        print('Report model RAM admitted after %.1f seconds.' % (time.monotonic()-started), flush=True)
        yield
    finally:
        if acquired is not None:
            os.close(acquired)  # Kernel also releases the lease on SIGKILL/process exit.
