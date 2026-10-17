#!/usr/bin/env python3
"""Process implementations; subprocess argument arrays, no shell interpolation."""
import argparse
import csv
import gzip
import html
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from collections import Counter


def dump(path,obj): Path(path).write_text(json.dumps(obj,indent=2,allow_nan=False)+'\n')
def read_json(path): return json.loads(Path(path).read_text())
def text_open(path):
    with open(path,'rb') as f: compressed=f.read(2)==b'\x1f\x8b'
    return gzip.open(path,'rt') if compressed else open(path)

def run(cmd, output=None):
    cmd=[str(x) for x in cmd]
    print('+ '+ ' '.join(cmd),file=sys.stderr,flush=True)
    if output:
        with open(output,'w') as f: subprocess.run(cmd,check=True,stdout=f)
    else: subprocess.run(cmd,check=True)

def capture(cmd): return subprocess.check_output([str(x) for x in cmd],text=True)

def vcf_records(path):
    with text_open(path) as f:
        for line in f:
            if line.startswith('#'): continue
            x=line.rstrip('\n').split('\t')
            info=dict(i.split('=',1) if '=' in i else (i,True) for i in x[7].split(';'))
            fmt=dict(zip(x[8].split(':'),x[9].split(':'))) if len(x)>9 else {}
            yield {'chrom':x[0],'pos':int(x[1]),'id':x[2],'ref':x[3],'alt':x[4],
                   'qual':x[5],'filter':x[6],'info':info,'fmt':fmt}

def num(value, default=0):
    try:
        x=float(value)
        return x if math.isfinite(x) else default
    except (ValueError,TypeError): return default

def genotype(rec,min_dp=10,min_gq=20):
    if not rec: return {'status':'not_covered','genotype':'.','DP':0,'GQ':0,'AD':'.','alleles':[],'GQ_source':'missing'}
    fmt=rec['fmt']; gt=fmt.get('GT','.')
    result={'status':'low_confidence','genotype':gt,'DP':int(num(fmt.get('DP'))),
            'GQ':num(fmt.get('GQ')),'AD':fmt.get('AD','.'),'alleles':[],
            'GQ_source':'FORMAT_GQ' if fmt.get('GQ','.')!='.' else 'missing'}
    if '.' in gt: result['status']='no_call'; return result
    codes=re.split(r'[/|]',gt); options=[rec['ref']]+rec['alt'].split(',')
    try: alleles=[options[int(i)] for i in codes]
    except (ValueError,IndexError): result['status']='invalid_genotype'; return result
    if result['GQ_source']=='missing' and fmt.get('PL','.')!='.':
        # bcftools omits FORMAT/GQ at some 0/0 sites. Retained <*> PL values
        # support a conservative equal-genotype-prior quality; never use site QUAL.
        try:
            pls=[float(x) for x in fmt['PL'].split(',')]
            ints=sorted(int(x) for x in codes)
            idx=ints[0] if len(ints)==1 else ints[1]*(ints[1]+1)//2+ints[0]
            expected=len(options) if len(ints)==1 else len(options)*(len(options)+1)//2
            if len(pls)==expected and len(pls)>1 and all(math.isfinite(x) and x>=0 for x in pls):
                smallest=min(pls)
                weights=[10**(-(x-smallest)/10) for x in pls]
                error=sum(w for i,w in enumerate(weights) if i!=idx)/sum(weights)
                result['GQ']=round(min(99,-10*math.log10(max(error,1e-99))),2)
                result['GQ_source']='PL_equal_genotype_prior'
        except (ValueError,IndexError): pass
    result['alleles']=alleles
    result['genotype']='/'.join(alleles)
    if any(a in ['.','*','<*>','<NON_REF>'] for a in alleles): result['status']='non_sequence_allele'; return result
    if rec['filter'] not in {'.','PASS'}: result['status']='filtered'; return result
    if result['DP']<min_dp or result['GQ']<min_gq: return result
    ad=[num(x) for x in fmt.get('AD','').split(',')]
    if len(ad)<len(options) or sum(ad)==0: result['status']='missing_allele_depth'; return result
    fractions=[ad[int(i)]/sum(ad) for i in set(codes)]
    if len(set(codes))==2 and (min(fractions)<0.25 or max(fractions)>0.75): result['status']='allele_balance'; return result
    if len(set(codes))==1 and fractions[0]<0.8: result['status']='allele_balance'; return result
    result['status']='called'
    return result

def pathogenic_class(sig):
    # Conflicting/uncertain submissions must never become positive classifications.
    s=sig.lower()
    if any(k in s for k in ['conflict','uncertain','not_provided']): return False
    tokens=set(re.split(r'[/|,]',s))
    return bool(tokens & {'pathogenic','likely_pathogenic'}) and tokens <= {'pathogenic','likely_pathogenic'}

def review_stars(status):
    if 'practice_guideline' in status: return 4
    if 'reviewed_by_expert_panel' in status: return 3
    if 'multiple_submitters' in status and 'no_conflicts' in status: return 2
    if 'criteria_provided' in status: return 1
    return 0

def write_tsv(path, fields, rows):
    with open(path,'w') as f:
        w=csv.DictWriter(f,fieldnames=fields,delimiter='\t',extrasaction='ignore')
        w.writeheader(); w.writerows(rows)

def tool(c,name): return c['tools'][name]

def index_vcf(c,path): run([tool(c,'bcftools'),'index','-f','-t',path])

def normalize(c,source,dest):
    bc=tool(c,'bcftools')
    run([bc,'norm','-m','-any','--multi-overlaps','.','--check-ref','e','-f',c['reference'],'-Oz','-o',dest,source])
    index_vcf(c,dest)

def contigs(c):
    if '_contigs' not in c:
        c['_contigs']={x.split('\t')[0]:int(x.split('\t')[1]) for x in Path(c['reference']+'.fai').read_text().splitlines()}
    return c['_contigs']

def chrom_name(c,chrom):
    lengths=contigs(c)
    if chrom in lengths: return chrom
    alt=chrom[3:] if chrom.startswith('chr') else 'chr'+chrom
    if alt in lengths: return alt
    if chrom in ['M','MT','chrM','chrMT']:
        return next((x for x in ['chrM','MT','M','chrMT'] if x in lengths),None)
    return None

def autosome_bed(c):
    # Unknown sex: restrict diploid genotype interpretation to autosomes.
    lengths=contigs(c)
    bed=Path('autosomes.bed')
    lines=[]
    if c.get('regions'):
        with open(c['regions']) as f:
            for line in f:
                if line.startswith(('#','track','browser')) or not line.strip(): continue
                chrom,start,end=line.split()[:3]
                if chrom not in lengths: raise ValueError(f'BED contig {chrom} absent from reference')
                if int(start)<0 or int(end)<=int(start) or int(end)>lengths[chrom]: raise ValueError('Invalid BED interval')
                if chrom.removeprefix('chr') in {str(i) for i in range(1,23)}: lines.append(f'{chrom}\t{start}\t{end}\n')
    else:
        lines=[f'{k}\t0\t{v}\n' for k,v in lengths.items() if k.removeprefix('chr') in {str(i) for i in range(1,23)}]
    if not lines: raise ValueError('No autosomal regions to call')
    bed.write_text(''.join(lines)); return bed

def fasta_base(c,chrom,pos):
    return ''.join(capture([tool(c,'samtools'),'faidx',c['reference'],f'{chrom}:{pos}-{pos}']).splitlines()[1:]).upper()

def align(a,c):
    reads=list(a.reads)
    # Check pairing before trimming, including names, truncation, and paired lengths in a subset test.
    handles=[text_open(p) for p in reads]
    outs=[gzip.open(f'subset_{i+1}.fq.gz','wt') for i in range(len(reads))] if c['test_reads'] else []
    limit=c['test_reads'] or 1000
    try:
        for i in range(limit):
            records=[[f.readline() for _ in range(4)] for f in handles]
            if all(not r[0] for r in records): break
            if any(not r[3] or not r[0].startswith('@') or not r[2].startswith('+') or len(r[1].strip())!=len(r[3].strip()) for r in records):
                raise ValueError(f'Truncated or malformed FASTQ record {i+1}')
            ids=[re.sub(r'/[12]$','',r[0].split()[0]) for r in records]
            if len(set(ids))!=1: raise ValueError(f'FASTQ mates disagree at record {i+1}')
            for out,r in zip(outs,records): out.writelines(r)
    finally:
        for f in handles+outs: f.close()
    if outs: reads=[f'subset_{i+1}.fq.gz' for i in range(len(reads))]
    trimmed=[f'{a.lane}.R{i+1}.fq.gz' for i in range(len(reads))]
    cmd=[tool(c,'fastp'),'--in1',reads[0],'--out1',trimmed[0],'--thread',min(a.threads,16),
         '--json',f'{a.lane}.fastp.json','--html',f'{a.lane}.fastp.html','--length_required','35']
    if len(reads)==2: cmd+=['--in2',reads[1],'--out2',trimmed[1],'--detect_adapter_for_pe']
    run(cmd)
    rg=f'@RG\\tID:{a.sample}.{a.lane}\\tSM:{a.sample}\\tLB:{a.library}\\tPL:ILLUMINA\\tPU:{a.lane}'
    run([tool(c,'bwa'),'mem','-M','-t',a.threads,'-R',rg,c['bwa_index'],*trimmed],output='aligned.sam')
    st=tool(c,'samtools'); extra=max(0,a.threads-1)
    # Sorting memory is per thread. Keep its total below half of the requested budget.
    sort_mem=f'{max(64,int(c["memory_gb"]*1024/(2*a.threads)))}M'
    run([st,'sort','-n','-@',extra,'-m',sort_mem,'-O','BAM','-o','name.bam','aligned.sam'])
    if len(reads)==2: run([st,'fixmate','-m','-@',extra,'name.bam','fixmate.bam'])
    else: shutil.copyfile('name.bam','fixmate.bam')
    run([st,'sort','-@',extra,'-m',sort_mem,'-o',f'{a.lane}.bam','fixmate.bam'])
    run([st,'quickcheck','-v',f'{a.lane}.bam'])
    for p in ['aligned.sam','name.bam','fixmate.bam',*trimmed,*([f'subset_{i+1}.fq.gz' for i in range(len(reads))] if outs else [])]: Path(p).unlink()

def merge(a,c):
    st=tool(c,'samtools'); extra=max(0,a.threads-1)
    if len(a.bams)>1: run([st,'merge','-@',extra,'-o','merged.bam',*a.bams])
    else: shutil.copyfile(a.bams[0],'merged.bam')
    bam=f'{a.sample}.bam'
    # PCR/optical duplicates remain flagged in BAM; callers exclude duplicate reads.
    run([st,'markdup','-@',extra,'-s','-f',f'{a.sample}.markdup.txt','merged.bam',bam])
    run([st,'index','-@',extra,bam]); run([st,'quickcheck','-v',bam])
    run([st,'flagstat','-@',extra,bam],output=f'{a.sample}.flagstat.txt')
    run([st,'stats','-@',extra,bam],output=f'{a.sample}.stats.txt')
    run([st,'coverage','--ff','0xF04','-q',c['min_mapping_quality'],'-Q',c['min_base_quality'],bam],output=f'{a.sample}.coverage.txt')
    rows=list(csv.DictReader(open(f'{a.sample}.coverage.txt'),delimiter='\t'))
    autos=[r for r in rows if r['#rname'].removeprefix('chr') in {str(i) for i in range(1,23)}]
    total=sum(int(r['endpos'])-int(r['startpos'])+1 for r in autos)
    depth=sum(num(r['meandepth'])*(int(r['endpos'])-int(r['startpos'])+1) for r in autos)/max(total,1)
    covered=sum(int(r['covbases']) for r in autos)/max(total,1)
    dump(f'{a.sample}.qc.json',{'mean_autosomal_depth_after_filters':depth,'autosomal_fraction_covered':covered,
        'mapping_quality_min':c['min_mapping_quality'],'base_quality_min':c['min_base_quality'],
        'test_mode':c['test_mode'],'warning':'Low coverage cannot exclude germline, somatic, or trait variants.' if depth<10 else ''})
    Path('merged.bam').unlink()

def germline(a,c):
    bc=tool(c,'bcftools'); bed=autosome_bed(c)
    run([bc,'mpileup','-f',c['reference'],'-q',c['min_mapping_quality'],'-Q',c['min_base_quality'],
         '-d','10000','-a','FORMAT/DP,FORMAT/AD','-R',bed,'-Ob','-o','pileup.bcf',a.bam])
    run([bc,'call','-m','-v','-a','GQ','--ploidy','2','-Oz','-o','raw.vcf.gz','pileup.bcf'])
    normalize(c,'raw.vcf.gz','norm.vcf.gz')
    expr=f'QUAL<20 || FMT/DP<{c["min_depth"]} || FMT/GQ<{c["min_gq"]}'
    dest=f'{a.sample}.germline.vcf.gz'
    run([bc,'filter','-s','LowEvidence','-e',expr,'-Oz','-o',dest,'norm.vcf.gz'])
    index_vcf(c,dest)
    run([bc,'stats',dest],output=f'{a.sample}.germline.stats.txt')
    Path('pileup.bcf').unlink()

def gatk(c,cmd):
    heap=max(1,int(c['memory_gb']*0.7))
    return [tool(c,'gatk'),'--java-options',f'-Xmx{heap}g -XX:ActiveProcessorCount={c["threads"]}',*cmd]

def normal_mutect(a,c):
    # Each independent normal is called as a tumor, retaining the raw calls.
    # No PoN, matched-normal argument or FilterMutectCalls is used here.
    dest=f'{a.sample}.pon.unfiltered.vcf.gz'
    run(gatk(c,['Mutect2','-R',c['reference'],'-I',a.bam,'-O',dest,'-L',autosome_bed(c),
                '--max-mnp-distance','0','--native-pair-hmm-threads',a.threads,
                '--minimum-mapping-quality',c['min_mapping_quality'],
                '--min-base-quality-score',c['min_base_quality']]))
    index_vcf(c,dest)


def create_pon(a,c):
    vcfs=sorted(a.normal_vcfs)
    expected={n['sample'] for n in c['pon']['normals']}
    samples=[]
    for vcf in vcfs:
        names=capture([tool(c,'bcftools'),'query','-l',vcf]).splitlines()
        if len(names)!=1: raise ValueError(f'PoN input must contain exactly one normal sample: {vcf}')
        samples.extend(names)
    if len(samples)<2 or len(samples)!=len(set(samples)) or set(samples)!=expected:
        raise ValueError('PoN callsets must contain all expected independent normal samples exactly once')
    bed=autosome_bed(c)
    cmd=['GenomicsDBImport','-R',c['reference'],'-L',bed,'--genomicsdb-workspace-path','pon_db',
         '--reader-threads',a.threads,'--batch-size','50']
    for vcf in vcfs: cmd+=['-V',vcf]
    run(gatk(c,cmd))
    dest='somatic_pon.hg38.vcf.gz'
    cmd=['CreateSomaticPanelOfNormals','-R',c['reference'],'-V','gendb://pon_db',
         '--min-sample-count','2','-O',dest]
    if c.get('germline_resource'): cmd+=['--germline-resource',c['germline_resource']]
    run(gatk(c,cmd))
    index_vcf(c,dest)
    sites=int(capture([tool(c,'bcftools'),'index','--nrecords',dest]).strip())
    if not sites: print('WARNING: Built PoN contains no recurrent sites; it adds no artifact filtering.',file=sys.stderr)
    dump('somatic_pon.provenance.json',{'normal_count':len(samples),'normal_samples':sorted(samples),
        'min_sample_count':2,'sites':sites,'reference':c['reference'],'regions':c.get('regions'),
        'germline_resource':c.get('germline_resource'),'test_mode':c['test_mode'],
        'test_reads_per_lane':c['test_reads'],'normals':c['pon']['normals'],
        'pipeline_source_sha256':c.get('pipeline_source_sha256'),'versions':c.get('versions',{})})


def somatic(a,c):
    bed=autosome_bed(c)
    cmd=['Mutect2','-R',c['reference'],'-I',a.bam,'-O','unfiltered.vcf.gz','-L',bed,
         '--native-pair-hmm-threads',a.threads,'--f1r2-tar-gz','f1r2.tar.gz',
         '--minimum-mapping-quality',c['min_mapping_quality'],'--min-base-quality-score',c['min_base_quality']]
    if c.get('matched_normal_bam'):
        normal=Path('matched_normal.bam');normal.symlink_to(Path(c['matched_normal_bam']).resolve())
        run([tool(c,'samtools'),'index',str(normal)])
        cmd+=['-I',str(normal),'-normal',c['matched_normal_sample'],'-tumor',a.sample]
    pon=getattr(a,'pon',None)
    if not pon:
        if c.get('pon',{}).get('mode')=='build': raise ValueError('The generated panel of normals must be supplied to the somatic stage')
        pon=c.get('pon',{}).get('vcf') or c.get('panel_of_normals')
    if c.get('germline_resource'): cmd+=['--germline-resource',c['germline_resource']]
    if pon: cmd+=['--panel-of-normals',pon]
    run(gatk(c,cmd))
    run(gatk(c,['LearnReadOrientationModel','-I','f1r2.tar.gz','-O','orientation-priors.tar.gz']))
    cmd=['FilterMutectCalls','-R',c['reference'],'-V','unfiltered.vcf.gz','--stats','unfiltered.vcf.gz.stats',
         '--ob-priors','orientation-priors.tar.gz','-O','filtered.vcf.gz']
    if c.get('contamination_sites'):
        run(gatk(c,['GetPileupSummaries','-I',a.bam,'-V',c['contamination_sites'],'-L',c['contamination_sites'],
                    '-L',bed,'--interval-set-rule','INTERSECTION','-O','pileup.table']))
        run(gatk(c,['CalculateContamination','-I','pileup.table','-O','contamination.table','--tumor-segmentation','segments.table']))
        cmd+=['--contamination-table','contamination.table','--tumor-segmentation','segments.table']
    run(gatk(c,cmd))
    if c.get('matched_normal_bam'):
        run([tool(c,'bcftools'),'view','-s',a.sample,'-Oz','-o','patient.filtered.vcf.gz','filtered.vcf.gz'])
    normalize(c,'patient.filtered.vcf.gz' if c.get('matched_normal_bam') else 'filtered.vcf.gz',f'{a.sample}.somatic.vcf.gz')
    dump(f'{a.sample}.somatic.json',{'status':'matched_normal_candidates' if c.get('matched_normal_bam') else 'unpaired_candidates','matched_normal':bool(c.get('matched_normal_bam')),
        'population_resource':bool(c.get('germline_resource')),'panel_of_normals':bool(pon),'panel_of_normals_source':c.get('panel_of_normals'),
        'contamination_estimated':bool(c.get('contamination_sites')),
        'interpretation':'PASS does not establish somatic origin or clonal hematopoiesis. Germline variants and artifacts may remain.'})

def prepare_clinvar(a,c):
    bc=tool(c,'bcftools')
    import clinvar_resource
    source=clinvar_resource.prepare(c['clinvar'],c['reference'],bc,Path('clinvar_header_cache'),
                                    allow_test_reference=c.get('allow_test_reference',False))
    header=capture([bc,'view','-h',source])
    if not c.get('allow_test_reference') and not re.search(r'GRCh38|hg38',header,re.I): raise ValueError('ClinVar header does not identify GRCh38/hg38')
    names=re.findall(r'##contig=<ID=([^,>]+)',header)
    mapping={name:chrom_name(c,name) for name in names}
    usable={k:v for k,v in mapping.items() if v}
    if not usable: raise ValueError('ClinVar and FASTA have no matching contigs')
    Path('rename.tsv').write_text(''.join(f'{k}\t{v}\n' for k,v in usable.items()))
    # Keep only contigs present in the reference, rename, then normalize exact alleles.
    run([bc,'view','-t',','.join(usable),'-Ob','-o','cv.subset.bcf',source])
    run([bc,'annotate','--rename-chrs','rename.tsv','-Ob','-o','cv.renamed.bcf','cv.subset.bcf'])
    run([bc,'norm','-m','-any','--multi-overlaps','.','--check-ref','e','-f',c['reference'],'-Ob','-o','cv.norm.bcf','cv.renamed.bcf'])
    run([bc,'sort','-Oz','-o','clinvar.norm.vcf.gz','cv.norm.bcf'])
    index_vcf(c,'clinvar.norm.vcf.gz')
    dump('clinvar.provenance.json',{'source':c['clinvar'],'file_date':re.findall(r'##fileDate=(.*)',header),
        'reference':c['reference'],'match':'normalized exact CHROM/POS/REF/ALT','retained_contigs':list(usable.values())})
    for p in ['cv.subset.bcf','cv.renamed.bcf','cv.norm.bcf']: Path(p).unlink()

def annotate(a,c):
    bc=tool(c,'bcftools'); prefix=f'{a.sample}.{a.kind}'
    fields=['CLNSIG','CLNREVSTAT','CLNDN','CLNDISDB','ALLELEID','GENEINFO']
    header=capture([bc,'view','-h',a.clinvar])
    cols=[f'INFO/{x}' for x in fields if f'##INFO=<ID={x},' in header]
    if not {'INFO/CLNSIG','INFO/CLNREVSTAT'}.issubset(cols): raise ValueError('ClinVar missing classification/review INFO fields')
    run([bc,'annotate','-a',a.clinvar,'-c',','.join(cols),'-Oz','-o','clinical.vcf.gz',a.vcf])
    engine=c['annotation']['engine']; ann=c['annotation']
    n=sum(1 for _ in vcf_records(a.vcf))
    dest=f'{prefix}.annotated.vcf.gz'
    used=engine
    if n==0 or engine=='none': shutil.copyfile('clinical.vcf.gz',dest)
    elif engine=='annovar':
        run([bc,'view','-Ov','-o','input.vcf','clinical.vcf.gz'])
        run([tool(c,'perl'),str(Path(ann['directory'])/'table_annovar.pl'),'input.vcf',ann['database'],
             '-buildver','hg38','-out',prefix,'-protocol',ann['protocol'],'-operation','g','-nastring','.',
             '-vcfinput','-polish'])
        annotated=prefix+'.hg38_multianno.vcf'
        run([bc,'view','-Oz','-o',dest,annotated])
    else:
        run([ann['executable'],'--offline','--cache','--dir_cache',ann['cache'],'--cache_version',ann['cache_version'],
             '--assembly','GRCh38','--fasta',c['reference'],'--format','vcf','--vcf','--symbol','--hgvs',
             '--canonical','--check_existing','--fork',a.threads,'--no_stats','--force_overwrite',
             '--input_file','clinical.vcf.gz','--output_file','vep.vcf'])
        run([bc,'view','-Oz','-o',dest,'vep.vcf'])
    index_vcf(c,dest)
    table_fields=['chrom','pos','ref','alt','genotype','DP','GQ','AD','filter','genotype_status','gene','consequence',
                  'clinvar_classification','clinvar_review','review_stars','condition','clinvar_allele_id','review_candidate','origin']
    counts=Counter(total=0,clinvar_matches=0); candidates=[]
    with open(f'{prefix}.all_variants.tsv','w') as f:
        writer=csv.DictWriter(f,fieldnames=table_fields,delimiter='\t'); writer.writeheader()
        for r in vcf_records(dest):
            gt=genotype(r,c['min_depth'],c['min_gq']); info=r['info']
            sig=str(info.get('CLNSIG','.')); review=str(info.get('CLNREVSTAT','.'))
            present=any(x!='0' and x!='.' for x in re.split(r'[/|]',r['fmt'].get('GT','.')))
            # Mutect2 omits germline GQ; use caller PASS plus independent DP/AD criteria.
            if a.kind=='somatic':
                ad=[num(x) for x in r['fmt'].get('AD','').split(',')]
                supported=r['filter']=='PASS' and num(r['fmt'].get('DP'))>=c['min_depth'] and len(ad)>1 and ad[1]>=3
                gt['status']='somatic_candidate' if supported else 'low_confidence_or_filtered'
            else: supported=gt['status']=='called'
            candidate=present and supported and pathogenic_class(sig) and review_stars(review)>=1
            gene=info.get('Gene.'+ann.get('protocol','refGene'),info.get('GENEINFO','.'))
            consequence=info.get('ExonicFunc.'+ann.get('protocol','refGene'),info.get('Func.'+ann.get('protocol','refGene'),info.get('CSQ','.')))
            row={k:r[k] for k in ['chrom','pos','ref','alt','filter']}
            row.update({k:gt[k] for k in ['genotype','DP','GQ','AD']})
            row.update(genotype_status=gt['status'],gene=gene,consequence=consequence,clinvar_classification=sig,
                clinvar_review=review,review_stars=review_stars(review),condition=info.get('CLNDN','.'),
                clinvar_allele_id=info.get('ALLELEID','.'),review_candidate='yes' if candidate else 'no',
                origin='germline_call' if a.kind=='germline' else 'unpaired_somatic_candidate_unconfirmed_origin')
            writer.writerow(row); counts['total']+=1
            if sig!='.': counts['clinvar_matches']+=1
            if candidate: candidates.append(row)
    write_tsv(f'{prefix}.pathogenic_candidates.tsv',table_fields,candidates)
    dump(f'{prefix}.annotation.json',{'kind':a.kind,'engine':used,'annotation_status':'no_variants' if n==0 else 'complete',
        **counts,'pathogenic_review_candidates':len(candidates),'test_mode':c['test_mode'],
        'interpretation':'ClinVar assertions with evidence for review; no ACMG classification, diagnosis, penetrance estimate, or treatment recommendation.'})

def read_gwas(path):
    with text_open(path) as f:
        reader=csv.DictReader(f,delimiter='\t')
        required={'chrom','pos','rsid','trait','allele_orientation','pvalue'}
        if not required.issubset(reader.fieldnames or []): raise ValueError('Use the prepared GRCh38 GWAS catalog from bin/prepare_resources.py')
        for row in reader: yield row

def traits(a,c):
    if 'trait_mode' in c or getattr(a,'imputed_vcfs',None):
        from traits_lpwgs import run as run_lpwgs_traits
        return run_lpwgs_traits(a,c)
    panel=list(csv.DictReader(open(c['traits_panel']),delimiter='\t'))
    bc=tool(c,'bcftools')
    positions=set()
    for row in panel:
        chrom=chrom_name(c,row['chrom'])
        if not chrom: raise ValueError('Trait contig absent from reference')
        if fasta_base(c,chrom,int(row['pos']))!=row['ref']: raise ValueError(f'Trait panel REF mismatch for {row["rsid"]}; require GRCh38 forward alleles')
        positions.add((chrom,int(row['pos'])))
    if c['gwas']:
        for row in read_gwas(c['gwas_catalog']):
            chrom=chrom_name(c,row['chrom'])
            if chrom: positions.add((chrom,int(row['pos'])))
    # Apply BED restrictions to targeted queries, preserving no-call entries outside them.
    regions={}
    for line in autosome_bed(c).read_text().splitlines():
        chrom,start,end=line.split('\t'); regions.setdefault(chrom,[]).append((int(start),int(end)))
    def within(chrom,pos): return any(start<=pos-1<end for start,end in regions.get(chrom,[]))
    positions={p for p in positions if within(*p)}
    order={k:i for i,k in enumerate(contigs(c))}
    Path('trait_gwas.bed').write_text(''.join(f'{chrom}\t{pos-1}\t{pos}\n' for chrom,pos in sorted(positions,key=lambda x:(order[x[0]],x[1]))))
    genotype_map={}
    if positions:
        run([bc,'mpileup','-f',c['reference'],'-q',c['min_mapping_quality'],'-Q',c['min_base_quality'],
             '-d','10000','-R','trait_gwas.bed','-a','FORMAT/DP,FORMAT/AD','-Ob','-o','target.pileup.bcf',a.bam])
        # No -v: confidently homozygous-reference sites must be retained.
        dest=f'{a.sample}.target_genotypes.vcf.gz'
        run([bc,'call','-m','-A','--keep-unseen-allele','-a','GQ','--ploidy','2','-Oz','-o',dest,'target.pileup.bcf'])
        index_vcf(c,dest)
        for rec in vcf_records(dest): genotype_map[(rec['chrom'],rec['pos'])]=rec
        Path('target.pileup.bcf').unlink()
    trait_rows=[]
    for row in panel:
        chrom=chrom_name(c,row['chrom']); pos=int(row['pos'])
        g=genotype(genotype_map.get((chrom,pos)),c['min_depth'],c['min_gq'])
        interpretation='Not determined: '+g['status'].replace('_',' ')
        dosage='.'
        if g['status']=='called':
            if not set(g['alleles']) <= {row['ref'],row['alt']}:
                g['status']='unmodeled_allele'; interpretation='Not determined: allele outside this published two-allele model'
            else:
                dosage=g['alleles'].count(row['effect_allele'])
                criterion=dosage>=1 if row['model']=='dominant' else dosage==2
                interpretation=row['association'] if criterion else 'This genotype does not meet the listed association model; phenotype remains uncertain.'
        if c['test_mode']: interpretation='SMOKE TEST ONLY; '+interpretation
        trait_rows.append({**row,**{k:g[k] for k in ['genotype','DP','GQ','GQ_source','AD','status']},'effect_allele_copies':dosage,'interpretation':interpretation})
    trait_fields=['rsid','trait','chrom','pos','ref','alt','genotype','DP','GQ','GQ_source','AD','status','effect_allele','effect_allele_copies','model','interpretation','limitation','pubmed']
    write_tsv(f'{a.sample}.traits.tsv',trait_fields,trait_rows)
    counts=Counter(); preview=[]
    gfields=['chrom','pos','rsid','trait','genotype','DP','GQ','GQ_source','AD','status','reported_risk_allele','pvalue',
             'reported_or_beta','confidence_interval','pubmed','study','study_population','mapped_trait','allele_orientation','interpretation']
    with open(f'{a.sample}.gwas.tsv','w') as f:
        writer=csv.DictWriter(f,fieldnames=gfields,delimiter='\t',extrasaction='ignore'); writer.writeheader()
        if c['gwas']:
            for row in read_gwas(c['gwas_catalog']):
                key=(chrom_name(c,row['chrom']),int(row['pos']))
                g=genotype(genotype_map.get(key),c['min_depth'],c['min_gq'])
                counts[g['status']]+=1
                row.update({k:g[k] for k in ['genotype','DP','GQ','GQ_source','AD','status']})
                row['interpretation']='Association at this locus; published risk allele has not been strand-harmonized. No individual risk/effect dosage inferred.' if g['status']=='called' else 'Not determined: '+g['status'].replace('_',' ')
                if c['test_mode']: row['interpretation']='SMOKE TEST ONLY; '+row['interpretation']
                writer.writerow(row)
                if g['status']=='called' and len(preview)<30: preview.append(row)
    dump(f'{a.sample}.traits_gwas.json',{'traits':trait_rows,'gwas_enabled':c['gwas'],'gwas_associations_by_status':dict(counts),
        'gwas_preview':preview,'target_sites':len(positions),'observed_sites':len(genotype_map),'test_mode':c['test_mode'],
        'interpretation':'Trait associations are a small transparent starter panel, not a commercial prediction model. GWAS hits are associations, not diagnoses or polygenic risk scores.'})

def ancestry(a,c):
    dest=f'{a.sample}.ancestry.json'
    base={'method':'Somalier PCA plus supervised reference classification','reference':'1000 Genomes labeled sketches',
          'test_mode':c['test_mode'],'interpretation':'Reference similarity only. Classifier scores are not admixture percentages, ethnicity, nationality, or Indigenous ancestry fractions.'}
    if c['no_ancestry']:
        dump(dest,{**base,'status':'disabled_by_user'}); return
    anc=Path(c['ancestry_dir']); exe=anc/'somalier'; sites=anc/'sites.hg38.vcf.gz'
    if c['chrom_prefix']!='chr':
        # Identical marker order, alternate contig naming; sketch order must be preserved.
        header=capture([tool(c,'bcftools'),'view','-h',sites])
        names=re.findall(r'##contig=<ID=([^,>]+)',header)
        Path('ancestry.rename.tsv').write_text(''.join(f'{n}\t{chrom_name(c,n) or n}\n' for n in names))
        run([tool(c,'bcftools'),'annotate','--rename-chrs','ancestry.rename.tsv','-Oz','-o','sites.vcf.gz',sites]); sites=Path('sites.vcf.gz')
    run([exe,'extract','--sites',sites,'-f',c['reference'],'-d','sketch',a.bam])
    sketches=sorted(Path('sketch').glob('*.somalier'))
    if len(sketches)!=1: raise ValueError('Expected exactly one patient sketch')
    run([exe,'relate','--sites',sites,'--min-depth','5','-o',f'{a.sample}.qc',*sketches])
    qcfile=next(Path('.').glob(f'{a.sample}.qc*.samples.tsv'))
    rows=list(csv.DictReader(open(qcfile),delimiter='\t'))
    row=next((r for r in rows if r.get('sample_id')==a.sample),rows[0] if len(rows)==1 else None)
    if not row: raise ValueError('Patient absent from Somalier QC')
    def metric(name):
        for key in [name,'n_'+name]:
            if key in row: return int(num(row[key]))
        raise ValueError(f'Cannot find Somalier QC metric {name}: {list(row)}')
    known=sum(metric(k) for k in ['hom_ref','hom_alt','het'])
    base['confident_sites_depth_ge_5']=known; base['minimum_sites']=c['ancestry_min_sites']
    if known<c['ancestry_min_sites'] or c['test_mode']:
        dump(dest,{**base,'status':'withheld_test_mode' if c['test_mode'] else 'insufficient_coverage',
                   'reason':'Too few confident markers or a subset test; ancestry classification withheld.'}); return
    refs=sorted((anc/'reference').glob('*.somalier'))
    run([exe,'ancestry','--labels',anc/'labels.tsv','-o',a.sample,*refs,'++',*sketches])
    predfile=Path(f'{a.sample}.somalier-ancestry.tsv')
    predictions=list(csv.DictReader(open(predfile),delimiter='\t'))
    row=next((r for r in predictions if r.get('#sample_id')==a.sample),None)
    if not row: raise ValueError('Patient absent from ancestry classifier output')
    scores={k.removesuffix('_prob'):num(v) for k,v in row.items() if k.endswith('_prob')}
    max_score=max(scores.values(),default=0)
    dump(dest,{**base,'status':'exploratory' if max_score>=0.9 else 'uncertain_reference_similarity',
        'nearest_reference_label':row['predicted_ancestry'],'classifier_scores_not_ancestry_fractions':scores,
        'principal_components':{k:num(v) for k,v in row.items() if k.startswith('PC')},
        'notes':'Somalier ancestry is experimental and stochastic. Admixed populations and underrepresented reference groups require additional validated analysis.'})

def html_table(rows,fields):
    if not rows: return '<p>No entries.</p>'
    return '<div class="scroll"><table><thead><tr>'+''.join('<th>'+html.escape(k.replace('_',' '))+'</th>' for k in fields)+'</tr></thead><tbody>'+''.join('<tr>'+''.join('<td>'+html.escape(str(r.get(k,'')))+'</td>' for k in fields)+'</tr>' for r in rows)+'</tbody></table></div>'

def report(a,c):
    qc=read_json(a.qc); tr=read_json(a.traits); an=read_json(a.ancestry); annotations=[read_json(x) for x in a.annotations]
    warnings=list(c['warnings'])
    if qc.get('warning'): warnings.append(qc['warning'])
    result={'sample':a.sample,'test_mode':c['test_mode'],'qc':qc,'annotation':annotations,'ancestry':an,'traits_gwas':tr,
            'warnings':warnings,'analysis_scope':'Autosomal SNVs/short indels; optional autosomal SNP imputation for traits/GWAS. No sex chromosome/mitochondrial interpretation, CNVs/SVs, repeats, or validated PRS.',
            'resources':c['resources'],'tools':c['tools'],'versions':c.get('versions',{})}
    dump(f'{a.sample}.report.json',result)
    banner='SMOKE TEST — NOT A PATIENT RESULT' if c['test_mode'] else 'Research analysis — findings require independent validation'
    candidates=[]
    for path in sorted(Path('.').glob('*.pathogenic_candidates.tsv')):
        with path.open() as f:
            for row in csv.DictReader(f,delimiter='\t'):
                if len(candidates)<100: candidates.append(row)
    text=f'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
+<title>{html.escape(a.sample)} genome report</title><style>
+body{{font:16px/1.5 system-ui,sans-serif;max-width:1180px;margin:40px auto;padding:0 24px;color:#172b3a;background:#f7f9fb}}
+h1,h2{{color:#123a52}} .banner{{background:#fff0cd;padding:16px;border-left:5px solid #bb7e14}}
+table{{border-collapse:collapse;background:white;width:100%;font-size:14px}}td,th{{padding:9px;border-bottom:1px solid #dbe4eb;text-align:left;vertical-align:top}}
+.scroll{{overflow:auto}}pre{{white-space:pre-wrap;background:white;padding:16px}}small{{color:#526673}}</style>
+<h1>{html.escape(a.sample)} · genome research report</h1><div class="banner">{banner}</div>
+<p>{html.escape(result['analysis_scope'])}</p>
+<h2>Read coverage</h2><p>Mean autosomal depth after read filters: <b>{qc['mean_autosomal_depth_after_filters']:.3f}×</b>.
+Genome fraction with any retained coverage: {qc['autosomal_fraction_covered']:.2%}. Uncovered positions are unknown.</p>
+<h2>Run limitations</h2><ul>{''.join('<li>'+html.escape(w)+'</li>' for w in warnings)}</ul>
+<h2>Variant annotation</h2>{html_table(annotations,['kind','engine','annotation_status','total','clinvar_matches','pathogenic_review_candidates'])}
+<p>ClinVar labels are database assertions, not a new ACMG classification. Candidates require genotype review, inheritance/phenotype assessment, and orthogonal confirmation. A negative result does not exclude disease.</p>
+<h2>Pathogenic variant review candidates</h2>{html_table(candidates,['chrom','pos','ref','alt','genotype','DP','GQ','clinvar_classification','review_stars','condition','origin'])}
+<p>Somatic results from a single PBMC specimen are unpaired candidates. They do not establish somatic origin, cancer, or clonal hematopoiesis.</p>
+<h2>Ancestry reference comparison</h2><pre>{html.escape(json.dumps(an,indent=2))}</pre>
+<h2>Trait associations</h2>{html_table(tr['traits'],['trait','rsid','genotype','genotype_source','confidence','DP','GQ','GP','status','interpretation','limitation','pubmed'])}
+<h2>GWAS Catalog</h2><p>Enabled: {tr['gwas_enabled']}. Genotype status counts per association: {html.escape(json.dumps(tr['gwas_associations_by_status']))}.</p>
+<p>Published locus associations are not individual risk predictions. Catalog risk alleles are retained verbatim without dosage inference because strand harmonization has not been established. Full results, including no calls, are in traits_gwas/*.gwas.tsv.</p>
+{html_table(tr['gwas_preview'],['rsid','trait','genotype','pvalue','reported_or_beta','study_population','pubmed'])}
+<h2>Provenance</h2><p>See pipeline_info/preflight.json, clinvar.provenance.json, trace.tsv, resource download manifests, and the report JSON. All sample analysis was local.</p></html>'''.replace('\n+','\n')
    Path(f'{a.sample}.report.html').write_text(text)

def main():
    p=argparse.ArgumentParser()
    p.add_argument('stage',choices=['align','merge','germline','normal_mutect','create_pon','somatic','prepare_clinvar','annotate','traits','ancestry','report'])
    p.add_argument('--config',required=True); p.add_argument('--sample'); p.add_argument('--threads',type=int,default=1)
    p.add_argument('--lane'); p.add_argument('--library'); p.add_argument('--reads',nargs='+'); p.add_argument('--bams',nargs='+')
    p.add_argument('--pon'); p.add_argument('--normal-vcfs',nargs='+')
    p.add_argument('--bam'); p.add_argument('--kind'); p.add_argument('--vcf'); p.add_argument('--clinvar')
    p.add_argument('--qc'); p.add_argument('--traits'); p.add_argument('--ancestry'); p.add_argument('--annotations',nargs='+')
    p.add_argument('--imputed-vcfs',nargs='*')
    p.add_argument('--cached-trait-data')
    a=p.parse_args(); c=read_json(a.config)
    globals()[a.stage](a,c)
    from continuation import record
    record(a,c)

if __name__=='__main__': main()
