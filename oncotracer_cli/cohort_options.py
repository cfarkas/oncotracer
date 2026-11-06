"""Shared command-line choice for the optional Illumina cohort VCF merge."""


def add_arguments(parser):
    choice = parser.add_mutually_exclusive_group()
    choice.add_argument(
        '--merge-vcfs', '--merge_vcfs', '--variant-merge-vcfs',
        dest='variant_merge_vcfs', action='store_true', default=None,
        help='merge Illumina sample VCFs into cohort VCFs (default: enabled unless disabled in YAML)',
    )
    choice.add_argument(
        '--no-merge-vcfs', '--no_merge_vcfs', '--no-variant-merge-vcfs',
        dest='variant_merge_vcfs', action='store_false', default=None,
        help='skip the Illumina cohort merge; keep all per-sample VCFs and reports',
    )
