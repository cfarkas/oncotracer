#!/usr/bin/env python3
"""Generate deterministic, non-patient FASTQs for a Docker integration check.

Uses a local indexed hg38 FASTA, never downloads references or executes callers.
Both libraries have a low-coverage genomic background and two known heterozygous
SNVs at a locally well-covered locus. This tests software wiring, not sensitivity.
"""
import argparse
import gzip
import hashlib
import json
import random
from pathlib import Path


class IndexedFasta:
    def __init__(self, path):
        self.handle = path.open('rb')
        self.index = {}
        for line in Path(str(path) + '.fai').read_text().splitlines():
            name, length, offset, bases, width = line.split('\t')[:5]
            self.index[name] = tuple(map(int, (length, offset, bases, width)))

    def fetch(self, chrom, start, length):
        size, offset, bases, width = self.index[chrom]
        assert 0 <= start < start + length <= size
        byte_start = offset + (start // bases) * width + start % bases
        end = start + length
        byte_end = offset + (end // bases) * width + end % bases
        self.handle.seek(byte_start)
        return self.handle.read(byte_end - byte_start).replace(b'\n', b'').replace(b'\r', b'')[:length].decode().upper()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reference', type=Path, required=True)
    parser.add_argument('--outdir', type=Path, required=True)
    parser.add_argument('--pairs', type=int, default=300000)
    parser.add_argument('--ont-reads', type=int, default=25000)
    args = parser.parse_args()
    args.outdir.mkdir(parents=True, exist_ok=False)
    ref = IndexedFasta(args.reference)
    rng = random.Random(20260921)
    chroms = ['chr' + str(i) for i in range(1, 23)]
    lengths = [ref.index[c][0] for c in chroms]
    reverse = str.maketrans('ACGT', 'TGCA')
    def rc(seq):
        return seq.translate(reverse)[::-1]
    def fragment(size):
        while True:
            chrom = rng.choices(chroms, weights=lengths)[0]
            start = rng.randrange(10000, ref.index[chrom][0] - size)
            seq = ref.fetch(chrom, start, size)
            if set(seq) <= set('ACGT'):
                return seq
    variants = []
    for pos in (20050000, 20051000):
        base = ref.fetch('chr22', pos, 1)
        assert base in 'ACGT'
        variants.append({'chrom': 'chr22', 'pos': pos + 1, 'ref': base,
                         'alt': next(b for b in 'ACGT' if b != base)})
    illumina = args.outdir / 'illumina'
    ont = args.outdir / 'ont' / 'barcode01'
    illumina.mkdir()
    ont.mkdir(parents=True)
    def record(handle, name, seq):
        handle.write(f'@{name}\n{seq}\n+\n'+ 'I' * len(seq) + '\n')
    with gzip.open(illumina/'SYNTH_ILLUMINA_R1.fastq.gz', 'wt', compresslevel=1) as r1, gzip.open(illumina/'SYNTH_ILLUMINA_R2.fastq.gz', 'wt', compresslevel=1) as r2:
        def pair(name, seq):
            if rng.random() < .5:
                seq = rc(seq)
            record(r1, name + '/1', seq[:150])
            record(r2, name + '/2', rc(seq[-150:]))
        for i in range(args.pairs):
            pair(f'background_{i}', fragment(350))
        for vi, variant in enumerate(variants):
            for i in range(200):
                pos = variant['pos'] - 1
                start = pos - rng.randrange(30, 120)
                seq = ref.fetch('chr22', start, 350)
                if i % 2:
                    offset = pos - start
                    seq = seq[:offset] + variant['alt'] + seq[offset+1:]
                pair(f'spike_{vi}_{i}', seq)
    with gzip.open(ont/'SYNTH_ONT.fastq.gz', 'wt', compresslevel=1) as out:
        for i in range(args.ont_reads):
            seq = fragment(3000)
            record(out, f'background_{i}', seq if i % 2 else rc(seq))
        for i in range(120):
            start = 20046000 + rng.randrange(2000)
            seq = ref.fetch('chr22', start, 7000)
            if i % 2:
                for variant in variants:
                    offset = variant['pos'] - 1 - start
                    seq = seq[:offset] + variant['alt'] + seq[offset+1:]
            record(out, f'spike_{i}', seq if (i // 2) % 2 else rc(seq))
    (args.outdir/'targets.bed').write_text('chr22\t20045000\t20056000\n')
    hashes = {str(p.relative_to(args.outdir)): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in args.outdir.rglob('*.gz')}
    (args.outdir/'fixture.json').write_text(json.dumps({
        'description': 'Synthetic software integration data, not patient data or a clinical benchmark',
        'seed': 20260921, 'reference': str(args.reference.resolve()),
        'illumina_background_pairs': args.pairs, 'ont_background_reads': args.ont_reads,
        'expected_snvs': variants, 'sha256': hashes,
    }, indent=2) + '\n')
    print(args.outdir/'fixture.json')


if __name__ == '__main__':
    main()
