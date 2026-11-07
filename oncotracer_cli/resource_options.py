"""Execution-only ONT resource choices; explicit YAML values retain precedence."""
from .runtime import OncoTracerError

def validate_mode(value):
    if value is None:return 'dynamic'
    if value not in ('dynamic','fixed') or not isinstance(value,str):
        raise OncoTracerError('variant_resource_mode must be dynamic or fixed')
    return value

def add_arguments(parser):
    parser.add_argument('--resource-mode','--resource_mode','--variant-resource-mode',
        dest='variant_resource_mode',choices=('dynamic','fixed'),default=None,
        help='ONT chunk scheduling: dynamic (default) shares freed CPU/RAM within the analysis budget; fixed retains initial allocations')
