"""Adopt verified outputs from a previous run without writing through source links."""
import json
import os
from pathlib import Path


def annotation_outputs(reused, sample):
    for kind in ('germline', 'somatic'):
        key = f'annotate:{sample}:{kind}'
        if key not in reused:
            continue
        work = Path(reused[key][0]).parent
        names = [f'{sample}.{kind}.all_variants.tsv', f'{sample}.{kind}.annotated.vcf.gz',
                 f'{sample}.{kind}.annotated.vcf.gz.tbi']
        end = work/'.exitcode'
        if not end.is_file() or end.read_text().strip() != '0' or any(
            not (work/n).is_file() or (work/n).stat().st_size == 0 or
            (work/n).stat().st_mtime_ns > end.stat().st_mtime_ns for n in names):
            reused.pop(key)
    return reused


def link_file(source, destination):
    source, destination = Path(source).resolve(), Path(destination)
    if not source.is_file():
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_symlink():
        if destination.resolve() == source:
            return
        destination.unlink()
    elif destination.exists():
        # A new task may already have published a regular file. Keep that file.
        return
    destination.symlink_to(os.path.relpath(source, destination.parent))


def publish_reused(output, sample, core, imputation):
    """Only adopt files belonging to verified tasks that this run will skip."""
    output = Path(output)
    manifest=output/'pipeline_info/reused_output_links.json'
    previous=json.loads(manifest.read_text()) if manifest.is_file() else []
    adopted = []
    def adopt(source,destination):
        link_file(source,destination)
        if destination.is_symlink() and destination.resolve()==source.resolve():
            adopted.append({'source':str(source),'destination':str(destination)})
    for key, files in core.items():
        parts = key.split(':'); stage = parts[0]; work = Path(files[0]).parent
        patterns = []; folder = None
        if stage == 'merge':
            name = parts[1]; folder = 'alignment' if name == sample else 'panel_of_normals/alignment'
            patterns = [f'{name}.*']
        elif stage == 'align' and parts[1] == sample:
            folder = 'qc/lanes'; patterns = [f'{parts[2]}*.json', f'{parts[2]}*.html']
        elif stage in {'germline', 'somatic'}:
            folder = 'variants/germline' if stage == 'germline' else 'variants/somatic_candidates'
            patterns = [f'{sample}.{stage}.*']
        elif stage == 'annotate':
            folder = f'annotation/{parts[2]}'; patterns = [f'{sample}.{parts[2]}.*']
        elif stage == 'ancestry':
            folder = 'ancestry'; patterns = [f'{sample}.*']
        elif stage == 'normal_mutect':
            folder = 'panel_of_normals/calls'; patterns = [f'{parts[1]}.pon.*']
        elif stage == 'create_pon':
            folder = 'panel_of_normals'; patterns = ['somatic_pon.*']
        elif stage == 'prepare_clinvar':
            folder = 'pipeline_info'; patterns = ['clinvar.provenance.json']
        if folder:
            finish = (work/'.exitcode').stat().st_mtime_ns
            for pattern in patterns:
                for source in sorted(work.glob(pattern)):
                    if source.is_file() and source.stat().st_mtime_ns <= finish:
                        destination = output/folder/source.name
                        adopt(source, destination)
    for paths in imputation.get('references', {}).values():
        for source in Path(paths[0]).parent.glob('reference.*.provenance.json'):
            adopt(source, output/'imputation/reference_provenance'/source.name)
    for paths in imputation.get('ligated', {}).values():
        for source in map(Path, paths):
            adopt(source, output/'imputation'/source.name)
    kept={entry['destination'] for entry in adopted}
    for entry in previous:
        old=Path(entry['destination'])
        if str(old) not in kept and old.parent.resolve().is_relative_to(output.resolve()) and old.is_symlink():
            old.unlink()
    manifest.write_text(json.dumps(adopted, indent=2)+'\n')
