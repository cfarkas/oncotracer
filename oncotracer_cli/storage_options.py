"""Storage choices that do not change scientific inputs or variant calling."""
from .runtime import OncoTracerError


def validate_mode(value):
    if value is None:
        return 'compact'
    if not isinstance(value, str) or value not in ('full', 'compact'):
        raise OncoTracerError('variant_storage_mode must be full or compact')
    return value


def add_arguments(parser):
    parser.add_argument(
        '--storage-mode', '--storage_mode', '--variant-storage-mode',
        dest='variant_storage_mode', choices=('full', 'compact'), default=None,
        help='Illumina work storage: compact (default) removes completed GLIMPSE split-reference scratch while preserving resume; full keeps scratch',
    )
