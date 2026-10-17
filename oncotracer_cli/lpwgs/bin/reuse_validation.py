"""Fast, read-only structural checks on provenance-qualified resume candidates.

Completion receipts and input/code fingerprints are checked by the collectors.
These checks additionally reject truncated streams, unreadable indexes/headers,
wrong sample names, invalid JSON and corrupt compressed tabular reports. BAM/BCF
checks inspect structure and indexed records, not every alignment/genotype byte.
"""
import gzip
import json
from pathlib import Path
import zipfile

BGZF_EOF = bytes.fromhex('1f8b08040000000000ff0600424302001b0003000000000000000000')


class Validator:
    def __init__(self):
        self.checked = set()
        self.rejected = []

    def file(self, value, sample=None, indexed=False):
        path = Path(value)
        stat = path.stat()
        if not path.is_file() or stat.st_size == 0:
            raise ValueError(f'Empty/missing file: {path}')
        identity = (str(path.resolve()), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, sample, indexed)
        if identity in self.checked:
            return
        name = path.name
        if name.endswith(('.bam', '.bcf', '.vcf.gz')):
            import pysam
            with path.open('rb') as handle:
                handle.seek(-len(BGZF_EOF), 2)
                if handle.read() != BGZF_EOF:
                    raise ValueError(f'Missing BGZF end marker: {path}')
            if name.endswith('.bam'):
                try:
                    pysam.quickcheck(str(path))
                except pysam.utils.SamtoolsError as error:
                    raise ValueError(str(error)) from error
                with pysam.AlignmentFile(str(path), 'rb') as handle:
                    if sample and {r.get('SM') for r in handle.header.to_dict().get('RG', [])} != {sample}:
                        raise ValueError(f'BAM sample does not match {sample}: {path}')
                    if indexed:
                        handle.check_index()
                        next(handle.fetch(handle.references[0]), None)
            else:
                with pysam.VariantFile(str(path)) as handle:
                    if sample and list(handle.header.samples) != [sample]:
                        raise ValueError(f'VCF/BCF sample does not match {sample}: {path}')
                    if indexed:
                        if handle.index is None:
                            raise ValueError(f'Unreadable/missing variant index: {path}')
                        # An indexed fetch opens and parses a data block too.
                        for contig in handle.header.contigs:
                            next(handle.fetch(contig), None)
                            break
        elif name.endswith('.json'):
            json.loads(path.read_text())
        elif name.endswith('.gz'):
            # Streaming through EOF verifies all gzip member CRCs without
            # retaining a potentially large evidence table in memory.
            with gzip.open(path, 'rb') as handle:
                while handle.read(1024 * 1024):
                    pass
        elif name.endswith('.xlsx'):
            with zipfile.ZipFile(path) as handle:
                bad = handle.testzip()
                if bad:
                    raise ValueError(f'Corrupt workbook member {bad}: {path}')
        after = path.stat()
        if (after.st_size, after.st_mtime_ns, after.st_ctime_ns) != (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns):
            raise ValueError(f'File changed while validating: {path}')
        self.checked.add(identity)

    def group(self, stage, key, files, sample=None):
        paths = [Path(p) for p in files]
        try:
            for path in paths:
                indexed = any(str(p) in {str(path)+'.bai', str(path)+'.tbi', str(path)+'.csi'} for p in paths)
                self.file(path, sample=sample if path.name.endswith(('.bam', '.bcf', '.vcf.gz')) else None,
                          indexed=indexed)
            return True
        except (OSError, ValueError, EOFError, zipfile.BadZipFile) as error:
            self.rejected.append({'stage': stage, 'key': key, 'reason': str(error), 'files': list(map(str, paths))})
            return False

    def core(self, reused, sample):
        for key, files in list(reused.items()):
            stage = key.split(':')[0]
            work = Path(files[0]).parent
            extra = []
            if stage == 'merge':
                extra = [work/(key.split(':')[1]+'.coverage.txt')]
            elif stage == 'annotate':
                prefix = f'{sample}.{key.split(":")[-1]}'
                extra = [work/(prefix+suffix) for suffix in ('.all_variants.tsv', '.annotated.vcf.gz', '.annotated.vcf.gz.tbi')]
            expected_sample = key.split(':')[1] if stage in {'merge', 'align', 'germline', 'somatic', 'annotate', 'normal_mutect'} else None
            if not self.group('core', key, [*files, *extra], expected_sample):
                reused.pop(key)
        # Do not reuse a derived result after rejecting its prerequisite.
        if f'merge:{sample}' not in reused:
            for key in list(reused):
                if key.startswith(('germline:', 'somatic:', 'annotate:', 'ancestry:')):
                    reused.pop(key)
        for kind in ('germline', 'somatic'):
            if f'{kind}:{sample}' not in reused or 'prepare_clinvar' not in reused:
                reused.pop(f'annotate:{sample}:{kind}', None)

    def imputation(self, reused, sample):
        for kind in ('references', 'phases', 'ligated'):
            for key, files in list(reused[kind].items()):
                chrom = key.split(':')[0]
                parent_missing = kind != 'references' and chrom not in reused['references']
                failed_phase = kind == 'ligated' and any(r['stage'] == 'phases' and r['key'].split(':')[0] == chrom for r in self.rejected)
                if parent_missing or failed_phase or not self.group(kind, key, files, None if kind == 'references' else sample):
                    reused[kind].pop(key)

    def analyses(self, reused, sample):
        for stage, items in reused.items():
            for key, value in list(items.items()):
                if not self.group(stage, key, value['files'], sample):
                    items.pop(key)
        for key in list(reused.get('pgs_score', {})):
            if key not in reused.get('pgs_prepare', {}):
                reused['pgs_score'].pop(key)


def task_statuses(outputs, work_dir=None):
    """Count historical task attempts, independent of eligibility for reuse."""
    result = {'successful_attempts': 0, 'failed_attempts': 0, 'unfinished_attempts': 0}
    roots = {Path(o)/'work' for o in outputs}
    if work_dir:
        roots.add(Path(work_dir))
    for root in roots:
        for script in root.glob('*/*/.command.sh'):
            try:
                end = script.parent/'.exitcode'
                code = end.read_text().strip() if end.is_file() else ''
                key = 'successful_attempts' if code == '0' else 'failed_attempts' if code else 'unfinished_attempts'
                result[key] += 1
            except OSError:
                result['unfinished_attempts'] += 1
    return result


def summary(config, core, imputation, analyses, validator, outputs, work_dir=None):
    result = {'sample': config['sample'], 'validation': 'completion, code/input provenance, file metadata, BGZF/header/index checks; gzip reports fully CRC checked',
              'core': len(core), **{k: len(v) for k, v in imputation.items()},
              'analyses': {k: len(v) for k, v in analyses.items()},
              'rejected': validator.rejected, 'checked_files': len(validator.checked),
              'historical_tasks': task_statuses(outputs, work_dir)}
    print(f'Resume check {config["sample"]}: {result["core"]} core tasks, '
          f'{result["references"]} references, {result["phases"]} chunks, '
          f'{result["ligated"]} chromosomes, {sum(result["analyses"].values())} downstream analyses verified; '
          f'{len(validator.rejected)} rejected. Failed/unfinished tasks will run again.', flush=True)
    for item in validator.rejected:
        print(f'  Rejected {item["stage"]}:{item["key"]}: {item["reason"]}', flush=True)
    return result
