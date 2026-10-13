"""Trait/GWAS annotation with separate observed and imputed confidence tiers."""
import csv
import html
import json
import math
from collections import Counter,defaultdict
from pathlib import Path
import re
import subprocess
import pipeline as p

ASSIGNED={'called','exploratory','imputed'}

def imputed_call(rec,c):
    result={'genotype':'.','DP':0,'GQ':'.','GQ_source':'imputation_GP','AD':'.','alleles':[],
            'status':'low_imputation_confidence','genotype_source':'GLIMPSE2','confidence':'unresolved',
            'GP':'.','DS':'.','INFO':'.'}
    if not rec:return result
    fmt=rec['fmt'];codes=re.split(r'[/|]',fmt.get('GT','.'));alleles=[rec['ref']]+rec['alt'].split(',')
    try:
        if len(alleles)!=2 or len(codes)!=2 or any(x not in {'0','1'} for x in codes):return result
        if any(len(x)!=1 or x not in 'ACGT' for x in alleles):return result
        gp=[float(x) for x in fmt.get('GP','').split(',')]
        if len(gp)!=3 or any(not math.isfinite(x) or not 0<=x<=1 for x in gp) or abs(sum(gp)-1)>0.02:return result
        result['alleles']=[alleles[int(x)] for x in codes];result['genotype']='/'.join(result['alleles'])
        posterior=gp[sum(map(int,codes))];result['GP']=posterior
        dosage=float(fmt.get('DS',gp[1]+2*gp[2]))
        if not math.isfinite(dosage) or not 0<=dosage<=2:return result
        result['DS']=dosage
        info=next((rec['info'][k] for k in ['INFO','R2','DR2'] if k in rec['info']),None)
        if info is not None:
            info=float(info);result['INFO']=info
            if not math.isfinite(info) or not 0<=info<=1 or info<c.get('imputation_min_info',0.3):return result
        if rec['filter'] not in {'.','PASS'}:return result
        if posterior<c.get('imputation_min_gp',0.9):return result
        result['status']='imputed';result['confidence']='high' if posterior>=0.99 else 'supported'
    except (ValueError,IndexError,TypeError):pass
    return result

def assess(direct,imputed,c):
    if c.get('confidence_gwas'):
        from gwas_confidence import assess as preset_assess
        return preset_assess(direct,imputed,c)
    strict=p.genotype(direct,c['min_depth'],c['min_gq'])
    strict.update(genotype_source='reads',confidence='high' if strict['status']=='called' else 'unresolved',GP='.',DS='.',INFO='.')
    imp=imputed_call(imputed,c)
    if strict['status']=='called':
        if imp['status']=='imputed' and sorted(imp['alleles'])!=sorted(strict['alleles']):strict['discordance']='reads_vs_imputation'
        return strict
    if imp['status']=='imputed':
        imp['DP']=strict['DP'];imp['AD']=strict['AD'];return imp
    if c.get('trait_mode','exploratory')=='exploratory':
        relaxed=p.genotype(direct,c.get('trait_min_depth',2),c.get('trait_min_gq',5))
        if relaxed['status']=='called':
            relaxed.update(status='exploratory',genotype_source='reads',confidence='exploratory',GP='.',DS='.',INFO='.')
            return relaxed
    return strict

def add_imputed_record(sites, rec):
    """Keep allele identities: distinct ALT alleles can share a VCF position."""
    locus = (rec['chrom'], rec['pos'])
    variants = sites.setdefault(locus, {})
    allele_key = (rec['ref'], rec['alt'])
    entry = variants.setdefault(allele_key, {'record': rec, 'copies': 0, 'conflict': False})
    entry['copies'] += 1
    # Identical repeated records are harmless. Conflicting records for the same
    # allele must not be resolved by input order or by choosing the highest GP.
    if entry['record'] != rec:
        entry['conflict'] = True


def assess_site(direct, variants, c):
    variants = variants or {}
    multiple = len(variants) > 1
    conflict = any(v['conflict'] for v in variants.values())
    selected = next(iter(variants.values()))['record'] if len(variants) == 1 and not conflict else None
    g = assess(direct, selected, c)
    g.update(imputation_note='.', imputation_ref='.', imputation_alt='.')
    if selected is not None:
        g.update(imputation_ref=selected['ref'], imputation_alt=selected['alt'])
    if multiple or conflict:
        issue = 'conflicting_duplicate_records' if conflict else 'multiple_alleles_at_position'
        # GLIMPSE probabilities are per biallelic record, not a joint posterior
        # over all alleles. In particular G>C:0/0 does not establish G/G when a
        # second record is G>A:0/1. Use independent read evidence or leave unknown.
        g['imputation_note'] = issue + '; imputation withheld; read thresholds unchanged'
        if g['status'] not in ASSIGNED:
            g.update(status='ambiguous_imputed_site', genotype='.', alleles=[], confidence='unresolved')
    elif selected is not None and next(iter(variants.values()))['copies'] > 1:
        g['imputation_note'] = 'identical_duplicate_records_collapsed'
    return g


def panel_call(g, row):
    """A position match alone cannot identify an imputed panel marker."""
    g = dict(g)
    alternate_alleles = set(row['alt'].split(','))
    if g['status'] in ASSIGNED:
        if g['genotype_source'] == 'GLIMPSE2' and (g['imputation_ref'] != row['ref'] or g['imputation_alt'] not in alternate_alleles):
            g['status'] = 'imputed_allele_mismatch'
        elif not set(g['alleles']) <= {row['ref']} | alternate_alleles:
            g['status'] = 'unmodeled_allele'
    return g


def run(a,c):
    panel=list(csv.DictReader(open(c['traits_panel']),delimiter='\t'))
    positions=set();bc=p.tool(c,'bcftools')
    for row in panel:
        chrom=p.chrom_name(c,row['chrom']);pos=int(row['pos'])
        if not chrom or p.fasta_base(c,chrom,pos)!=row['ref']:raise ValueError(f'Trait REF mismatch: {row["rsid"]}')
        positions.add((chrom,pos))
    if c['gwas']:
        for row in p.read_gwas(c['gwas_catalog']):
            chrom=p.chrom_name(c,row['chrom'])
            if chrom:positions.add((chrom,int(row['pos'])))
    regions=defaultdict(list)
    for line in p.autosome_bed(c).read_text().splitlines():
        chrom,start,end=line.split('\t');regions[chrom].append((int(start),int(end)))
    positions={x for x in positions if any(start<=x[1]-1<end for start,end in regions[x[0]])}
    order={k:i for i,k in enumerate(p.contigs(c))}
    bed=Path('trait_gwas.bed');bed.write_text(''.join(f'{chrom}\t{pos-1}\t{pos}\n' for chrom,pos in sorted(positions,key=lambda x:(order[x[0]],x[1]))))
    direct={};imputed={}
    cached=getattr(a,'cached_trait_data',None)
    if cached and positions:
        cached=Path(cached)
        if (cached/'trait_gwas.bed').read_bytes()!=bed.read_bytes():raise ValueError('Cached trait target regions changed')
        cached_vcf=cached/f'{a.sample}.target_genotypes.vcf.gz'
        def check_sample(path):
            with p.text_open(path) as handle:
                header=next((line.rstrip().split('\t')[9:] for line in handle if line.startswith('#CHROM\t')),None)
            if header!=[a.sample]:raise ValueError('Cached genotype input belongs to another sample')
        check_sample(cached_vcf)
        direct={(r['chrom'],r['pos']):r for r in p.vcf_records(cached_vcf)}
        files=sorted(cached.glob('imputed_targets.*.vcf.gz'))
        if len(files)!=len(getattr(a,'imputed_vcfs',[]) or []):raise ValueError('Cached imputed target count changed')
        for target in files:
            check_sample(target)
            for r in p.vcf_records(target):add_imputed_record(imputed,r)
        print('Reused completed read genotypes and imputed target extractions; updating confidence and annotations.',flush=True)
    elif positions:
        p.run([bc,'mpileup','-f',c['reference'],'-q',c['min_mapping_quality'],'-Q',c['min_base_quality'],
               '-d','10000','-R',str(bed),'-a','FORMAT/DP,FORMAT/AD','-Ob','-o','target.pileup.bcf',a.bam])
        dest=f'{a.sample}.target_genotypes.vcf.gz'
        p.run([bc,'call','-m','-A','--keep-unseen-allele','-a','GQ','--ploidy','2','-Oz','-o',dest,'target.pileup.bcf'])
        p.index_vcf(c,dest);direct={(r['chrom'],r['pos']):r for r in p.vcf_records(dest)};Path('target.pileup.bcf').unlink()
        for i,vcf in enumerate(getattr(a,'imputed_vcfs',[]) or []):
            target=f'imputed_targets.{i}.vcf.gz'
            p.run([bc,'view','-s',a.sample,'-R',str(bed),'-Oz','-o',target,vcf])
            for r in p.vcf_records(target):
                add_imputed_record(imputed,r)
    calls={key:assess_site(direct.get(key),imputed.get(key),c) for key in positions}
    empty=assess_site(None,None,c)
    def get(row):return calls.get((p.chrom_name(c,row['chrom']),int(row['pos'])),empty)
    evidence=['genotype','DP','GQ','GQ_source','AD','status','genotype_source','confidence','GP','DS','INFO','imputation_note']
    site_stats=Counter()
    review=[]
    for key,variants in sorted(imputed.items(),key=lambda item:(order[item[0][0]],item[0][1])):
        site_stats['imputed_variant_records']+=sum(v['copies'] for v in variants.values())
        site_stats['distinct_imputed_variants']+=len(variants)
        site_stats['multiple_allele_sites']+=len(variants)>1
        site_stats['conflicting_duplicate_sites']+=any(v['conflict'] for v in variants.values())
        site_stats['identical_duplicate_records']+=sum(v['copies']-1 for v in variants.values() if not v['conflict'])
        if len(variants)>1 or any(v['copies']>1 for v in variants.values()):
            g=calls.get(key,empty)
            for (ref,alt),v in sorted(variants.items()):
                r=v['record']
                review.append({'chrom':key[0],'pos':key[1],'ref':ref,'alt':alt,'record_copies':v['copies'],
                    'conflicting_records':v['conflict'],'record_GT':r['fmt'].get('GT','.'),
                    'record_GP':r['fmt'].get('GP','.'),'record_DS':r['fmt'].get('DS','.'),
                    'selected_genotype':g['genotype'],'selected_status':g['status'],
                    'selected_source':g['genotype_source'],'imputation_note':g['imputation_note']})
    p.write_tsv(f'{a.sample}.imputation_site_review.tsv',
        ['chrom','pos','ref','alt','record_copies','conflicting_records','record_GT','record_GP','record_DS',
         'selected_genotype','selected_status','selected_source','imputation_note'],review)
    traits=[]
    for row in panel:
        g=panel_call(get(row),row);interpretation='Not determined: '+g['status'].replace('_',' ');dosage='.'
        if g['status'] in ASSIGNED:
            if row['model']=='association_only':
                interpretation=row['association']
            else:
                dosage=g['alleles'].count(row['effect_allele'])
                matches=dosage>=1 if row['model']=='dominant' else dosage==2
                interpretation=row['association'] if matches else (row.get('alternative_association') or 'Does not meet the listed association model; phenotype remains uncertain.')
            if g['status']=='exploratory':interpretation='Exploratory (limited read evidence): '+interpretation
            if g['status']=='imputed':interpretation='Based on an imputed genotype ('+g['confidence']+' confidence): '+interpretation
            if g.get('discordance'):interpretation+=' Direct and imputed genotypes disagree; review this locus.'
        if c['test_mode']:interpretation='SMOKE TEST ONLY; '+interpretation
        traits.append({**row,**{k:g[k] for k in evidence},'effect_allele_copies':dosage,'interpretation':interpretation})
    fields=['rsid','trait','chrom','pos','ref','alt',*evidence,'effect_allele','effect_allele_copies','model','interpretation','limitation','pubmed','source_url']
    p.write_tsv(f'{a.sample}.traits.tsv',fields,traits)
    stats=Counter();confidence_counts=Counter();preview=[];groups=defaultdict(lambda:defaultdict(set))
    gf=['chrom','pos','rsid','trait',*evidence,'reported_risk_allele','pvalue','reported_or_beta','confidence_interval','pubmed','study','study_population','mapped_trait','allele_orientation','interpretation']
    with open(f'{a.sample}.gwas.tsv','w') as f:
        writer=csv.DictWriter(f,fieldnames=gf,delimiter='\t',extrasaction='ignore');writer.writeheader()
        if c['gwas']:
            for row in p.read_gwas(c['gwas_catalog']):
                g=get(row);status=g['status'];stats[status]+=1;confidence_counts[g['confidence']]+=1
                row.update({k:g[k] for k in evidence})
                row['interpretation']=('Published association at this locus; catalog effect allele is not strand-harmonized; no personal effect/risk inferred.' if status in ASSIGNED else 'Not determined: '+status.replace('_',' '))
                if status=='exploratory':row['interpretation']='Exploratory genotype: '+row['interpretation']
                if c['test_mode']:row['interpretation']='SMOKE TEST ONLY; '+row['interpretation']
                writer.writerow(row);groups[row['trait']][status].add((row['chrom'],row['pos']))
                if status in ASSIGNED and len(preview)<100:preview.append(row)
    summary=[]
    for trait,group in sorted(groups.items()):
        summary.append({'trait':trait,'confident_read_loci':len(group['called']),'imputed_loci':len(group['imputed']),
          'exploratory_read_loci':len(group['exploratory']),'unresolved_loci':sum(len(v) for k,v in group.items() if k not in ASSIGNED)})
    p.write_tsv(f'{a.sample}.gwas_trait_summary.tsv',['trait','confident_read_loci','imputed_loci','exploratory_read_loci','unresolved_loci'],summary)
    p.dump(f'{a.sample}.traits_gwas.json',{'traits':traits,'gwas_enabled':c['gwas'],'gwas_associations_by_status':dict(stats),
      'confidence_gwas':c.get('confidence_gwas') or 'legacy','gwas_associations_by_confidence':dict(confidence_counts),
      'confidence_policy':__import__('gwas_confidence').PRESETS.get(c.get('confidence_gwas'),{}),
      'gwas_preview':preview,'target_sites':len(positions),'observed_sites':len(direct),'imputed_target_sites':len(imputed),
      'imputation_site_handling':dict(site_stats),'imputation_site_review':f'{a.sample}.imputation_site_review.tsv',
      'test_mode':c['test_mode'],'trait_mode':c.get('trait_mode'),'thresholds':{k:c.get(k) for k in ['min_depth','min_gq','trait_min_depth','trait_min_gq','imputation_min_gp','imputation_min_info']},
      'interpretation':'Exploratory observed genotypes and posterior-filtered imputed genotypes are distinguished. Associations are not phenotype predictions or polygenic risk scores.'})
    table=p.html_table(traits,['trait','rsid','genotype','genotype_source','confidence','DP','GQ','GP','interpretation'])
    broader=('<p><a href="../polygenic/'+html.escape(a.sample)+'.polygenic.html">Additional published trait models and polygenic scores</a> (available when the scoring stage completes).</p>' if c.get('polygenic') else '')
    Path(f'{a.sample}.traits_report.html').write_text('<!doctype html><html><meta charset="utf-8"><title>Traits</title><style>body{font-family:sans-serif;margin:2em}table{border-collapse:collapse}td,th{padding:.5em;border:1px solid #ddd}</style><h1>'+html.escape(a.sample)+' traits</h1><p>Exploratory calls use limited read evidence. GLIMPSE2 calls use genotype posterior probabilities. Marker annotations do not by themselves predict a phenotype. Sites with multiple imputed alleles or conflicting duplicate records use read evidence only, or remain unresolved; see the imputation site review TSV.</p>'+broader+table+'</html>')
