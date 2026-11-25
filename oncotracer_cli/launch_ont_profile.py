"""Choose a matching ONT caller profile from recorded FASTQ model metadata."""
from __future__ import annotations

import gzip
import re

from .runtime import OncoTracerError
from .ont_layout import pass_fastq_files

# ClairS-TO platform families already supported by the v5 caller runtime.
PROFILES = {
    'r1041_e82_400bps_sup_v500': 'ont_r10_dorado_sup_5khz_ssrs',
    'r1041_e82_400bps_sup_v520': 'ont_r10_dorado_sup_5khz_ssrs',
    'r1041_e82_400bps_sup_v420': 'ont_r10_dorado_sup_5khz_ssrs',
    'r1041_e82_400bps_sup_v410': 'ont_r10_dorado_sup_4khz',
    'r1041_e82_400bps_hac_v410': 'ont_r10_dorado_hac_4khz',
}
MODELS = {
    'dna_r10.4.1_e8.2_400bps_'+quality+'@v'+version: 'r1041_e82_400bps_'+quality+'_v'+version.replace('.', '')
    for quality, version in [('sup','5.0.0'),('sup','5.2.0'),('sup','4.2.0'),('sup','4.1.0'),('hac','4.1.0')]
}
MODEL_FIELD = re.compile(r'(?:^|\s)(?:basecall_)?model_version_id=(\S+)')


def choose_profile(samples, requested='auto'):
    """Inspect one header per FASTQ; absent metadata requires an explicit profile.

    This checks the supplied library metadata, not every read's chemistry. The
    raw-signal Rockfish path separately validates selected POD5 run metadata.
    """
    if requested != 'auto' and requested not in PROFILES:
        raise OncoTracerError('Unsupported managed ONT variant profile: '+requested)
    observed = set()
    missing = []
    evidence = []
    for sample in samples:
        for path in pass_fastq_files(sample.fastq_dir):
            opener = gzip.open if path.name.lower().endswith('.gz') else open
            try:
                with opener(path, 'rt', encoding='utf-8') as stream:
                    header = stream.readline()
            except (OSError, UnicodeError) as error:
                raise OncoTracerError('Cannot read ONT FASTQ metadata: '+str(path)) from error
            if not header.startswith('@'):
                raise OncoTracerError('Invalid ONT FASTQ header: '+str(path))
            match = MODEL_FIELD.search(header)
            model = match.group(1) if match else ''
            evidence.append({'sample':sample.sample,'file':str(path),'basecall_model':model})
            if not model:
                missing.append(sample.sample)
                continue
            profile = MODELS.get(model)
            if profile is None:
                raise OncoTracerError('No managed full-analysis profile matches '+model+' for '+sample.sample+
                    '. Use advanced setup with validated caller models for this protocol, or --analysis cna.')
            observed.add(profile)
    if len(observed)>1:
        raise OncoTracerError('ONT FASTQs contain different basecalling profiles. Use separate launch projects or advanced per-protocol configurations.')
    if requested!='auto':
        if observed and observed!={requested}:
            raise OncoTracerError('--ont-profile conflicts with the basecalling model recorded in the FASTQ headers')
        selected=requested
    elif missing or not observed:
        raise OncoTracerError('ONT FASTQ headers do not identify the basecalling model for '+', '.join(sorted(set(missing)))+
            '. Supply --ont-profile from your sequencing records; see oncotracer launch --help. No model was guessed.')
    else:
        selected=next(iter(observed))
    return {'profile':selected,'clairsto_platform':PROFILES[selected],'selection':'explicit' if requested!='auto' else 'fastq_metadata',
            'header_evidence':evidence}
