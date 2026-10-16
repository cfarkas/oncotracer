"""Probe the selected caller before work; support Conda and image executables."""
from functools import lru_cache
import subprocess
import re


REQUIRED_BCFTOOLS = '1.23.1'
REQUIRED_HTSLIB = '1.24'


def require_version(version_output, executable):
    """Validate the caller and its linked library, not a separate htslib binary."""
    found = re.search(r'^bcftools\s+(\S+)\s*$', version_output, re.MULTILINE)
    linked = re.search(r'^Using htslib\s+(\S+)\s*$', version_output, re.MULTILINE)
    bcftools = found.group(1) if found else 'unreported'
    htslib = linked.group(1) if linked else 'unreported'
    if (bcftools, htslib) != (REQUIRED_BCFTOOLS, REQUIRED_HTSLIB):
        raise ValueError(
            f'Complete Illumina variant analysis requires bcftools {REQUIRED_BCFTOOLS} '
            f'linked to HTSlib {REQUIRED_HTSLIB}; selected {executable} reports '
            f'bcftools {bcftools}, HTSlib {htslib}. '
            'Run oncotracer install --conda (or --poetry) to create and verify the '
            'managed variants environment; managed Illumina runs select it automatically. '
            'For an unmanaged host, use environments/native-variants.yml and select '
            'variant_tool_prefix (or --tool-prefix). For Docker, rebuild/select an '
            'image from the current Dockerfile and run oncotracer install --docker. '
            'Analysis does not modify installed executables.')
    return {'bcftools': bcftools, 'htslib': htslib, 'executable': str(executable)}


def version_diagnostic(runtime):
    return (f"Variant runtime verified: bcftools {runtime['bcftools']}; "
            f"linked HTSlib {runtime['htslib']}; executable {runtime['executable']}")


@lru_cache(maxsize=16)
def trait_call_options(executable):
    result = subprocess.run([str(executable), 'call', '--help'], text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            timeout=30, check=False)
    help_text = result.stdout
    required = ('--multiallelic-caller', '--keep-alts', '--ploidy')
    if any(option not in help_text for option in required):
        raise ValueError(f'Cannot verify required bcftools call capabilities: {executable}')
    annotation = '-f' if '--format-fields' in help_text else '-a' if '--annotate' in help_text else None
    if annotation is None:
        raise ValueError(f'bcftools call cannot emit genotype quality: {executable}')
    # Older callers cannot retain the symbolic, unobserved ALT. Keep observed
    # alternate alleles and reference sites; downstream depth/GQ gates still
    # apply, and absent likelihoods remain unresolved rather than fabricated.
    unseen = ['--keep-unseen-allele'] if '--keep-unseen-allele' in help_text else []
    return ('-m', '-A', *unseen, annotation, 'GQ', '--ploidy', '2')


def trait_call_command(executable, output, pileup):
    return [str(executable), 'call', *trait_call_options(str(executable)),
            '-Oz', '-o', str(output), str(pileup)]
