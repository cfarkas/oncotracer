"""Probe the selected caller before work; support Conda and image executables."""
from functools import lru_cache
import subprocess


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
