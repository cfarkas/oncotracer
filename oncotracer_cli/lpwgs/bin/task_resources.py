"""Apply a Nextflow task reservation to tools without changing scientific inputs."""
import math


def for_task(config, *, threads, memory_gb=None):
    if type(threads) is not int or threads < 1:
        raise ValueError('Task threads must be a positive integer')
    if threads > int(config.get('threads', threads)):
        raise ValueError('Task threads exceed the sample CPU budget')
    result = dict(config)
    result['threads'] = threads
    if memory_gb is not None:
        if isinstance(memory_gb, bool):
            raise ValueError('Task memory must be a positive finite GiB amount')
        amount = float(memory_gb)
        if not math.isfinite(amount) or amount <= 0:
            raise ValueError('Task memory must be a positive finite GiB amount')
        if amount > float(config.get('memory_gb', amount)) + 1e-6:
            raise ValueError('Task memory exceeds the sample memory budget')
        result['memory_gb'] = amount
        result['memory'] = amount
    return result
