#!/usr/bin/env python3
"""Visible, exact-allele annotations for mitochondrial, archaic and somatic evidence."""
import argparse,csv,gzip,json,subprocess,shutil,re
from pathlib import Path
from urllib.parse import unquote
import pysam
from extension_common import dump,table,Excel,flat,runtime_signature,sha256
from report_layout import model_page

NOTES={
 'mitochondrial':'Dominant mitochondrial SNP alleles used for maternal-lineage analysis (depth >=10, major allele fraction >=90%). Gene/protein consequences use rCRS RefSeq features and vertebrate mitochondrial code 2. This is not a sensitive heteroplasmy, indel, NUMT or mitochondrial disease assay. A missing ClinVar match is not evidence of benignity.',
 'neanderthal':'Carried derived alleles at published Neanderthal-associated markers (GP >=0.95, INFO >=0.80). Gene proximity and ClinVar labels do not establish an archaic cause of a trait or disease. Correlated markers do not measure the percentage of the genome inherited from Neanderthals.',
 'somatic':'Read-supported, unpaired somatic candidates. Gene/transcript and ClinVar annotations are retained from the original annotated caller VCF. Acquired somatic origin is not confirmed without appropriate normal-sample comparison. This table excludes millions of low-evidence caller records, retained in the full somatic annotation output.'}
from sample_variant_status import FIELDS as STATUS_FIELDS,for_marker,zygosity

COMMON=STATUS_FIELDS+['carrier_status','carrier_label','carrier_reason']+['chrom','pos','ref','alt','genotype','gene','consequence','protein_change','clinvar_classification','clinvar_review','condition','clinvar_allele_id','clinvar_url']

def rows(path):
    with (gzip.open(path,'rt') if str(path).endswith('.gz') else open(path)) as f:return list(csv.DictReader(f,delimiter='\t'))

def mt_features(path):
    result=[]
    for line in Path(path).read_text().splitlines():
        if line.startswith('#'):continue
        f=line.split('\t')
        if len(f)<9 or f[2] not in {'gene','CDS'}:continue
        attrs=dict(x.split('=',1) for x in f[8].split(';') if '=' in x)
        result.append({'start':int(f[3]),'end':int(f[4]),'type':f[2],'strand':f[6],'phase':f[7],**attrs})
    return result

def mt_symbol(name):
    return 'MT-'+name.replace('TRN','T').replace('COX','CO')

def mt_effect(pos,ref,alt,sequence,features,codons):
    if sequence[pos-1]!=ref:raise ValueError('Mitochondrial REF differs from rCRS')
    genes=[];effects=[];proteins=[];comp=str.maketrans('ACGT','TGCA')
    for f in features:
        if not f['start']<=pos<=f['end']:continue
        gene=mt_symbol(f['gene'])
        if f['type']=='gene':
            genes.append(gene)
            if f.get('gene_biotype')!='protein_coding':effects.append(f.get('gene_biotype','noncoding')+'_variant')
        else:
            if f.get('transl_table')!='2' or f['phase']!='0':raise ValueError('Unsupported mitochondrial translation feature')
            seq=sequence[f['start']-1:f['end']];i=pos-f['start'];a=alt
            if f['strand']=='-':seq=seq.translate(comp)[::-1];i=f['end']-pos;a=alt.translate(comp)
            start=i//3*3;old=seq[start:start+3]
            if len(old)!=3:
                effects.append('incomplete_terminal_codon_variant');continue
            new=old[:i%3]+a+old[i%3+1:];aa=codons[old];bb=codons[new]
            if i<3:
                if old in {'ATT','ATC','ATA','ATG','GTG'}:aa='M'
                if new in {'ATT','ATC','ATA','ATG','GTG'}:bb='M'
                else:effects.append('start_lost')
            effects.append('synonymous_variant' if aa==bb else 'stop_gained' if bb=='*' else 'stop_lost' if aa=='*' else 'missense_variant')
            proteins.append(gene+':p.'+aa+str(i//3+1)+bb)
    if not genes:effects.append('control_region_variant' if pos<=576 or pos>=16024 else 'intergenic_variant')
    return {'gene':';'.join(sorted(set(genes))) or '.', 'consequence':';'.join(sorted(set(effects))), 'protein_change':';'.join(proteins) or '.'}

def info_annotation(rec,protocol):
    info=dict(rec.info);allele=flat(info.get('ALLELEID'))
    func=flat(info.get('ExonicFunc.'+protocol));func=flat(info.get('Func.'+protocol)) if func=='.' else func
    return {'gene':flat(info.get('Gene.'+protocol,info.get('GENEINFO'))),'consequence':func,
        'protein_change':unquote(flat(info.get('AAChange.'+protocol))),
        'clinvar_classification':flat(info.get('CLNSIG')),'clinvar_review':flat(info.get('CLNREVSTAT')),
        'condition':unquote(flat(info.get('CLNDN'))),'clinvar_allele_id':allele,
        'clinvar_url':'https://www.ncbi.nlm.nih.gov/clinvar/?term='+allele+'[AlleleID]' if allele!='.' else '.'}

def add_status_formats(header):
    for tag,description in [('ZYG','Reported source genotype zygosity; mitochondrial labels do not establish homoplasmy'),('AFFSTAT','Affected status is not established by this genetic analysis'),('CARRIER','Carrier context: unknown or not_applicable; no clinical carrier confirmation')]:
        header.formats.add(tag,1,'String',description)

def write_input(path,records,sample,fasta,mito=False):
    header=pysam.VariantHeader();header.add_sample(sample);add_status_formats(header)
    for chrom in (dict.fromkeys(r['chrom'] for r in records) or dict.fromkeys(fasta.references)):header.contigs.add(chrom,length=fasta.get_reference_length(chrom))
    header.info.add('EVIDENCE_SOURCE',1,'String','Evidence source for this annotated variant set')
    for id,num,typ,desc in [('GT',1,'String','Genotype'),('DP',1,'Integer','Filtered read depth'),('AF',1,'Float','Observed dominant allele fraction; not a validated heteroplasmy estimate'),('IGP',1,'Float','Imputed called-genotype posterior probability'),('IINFO',1,'Float','Imputation site information')]:
        header.formats.add(id,num,typ,desc)
    with pysam.VariantFile(str(path),'wz' if str(path).endswith('.gz') else 'w',header=header) as out:
        for row in sorted(records,key=lambda r:(list(header.contigs).index(r['chrom']),int(r['pos']))):
            pos=int(row['pos']);ref=row['ref'];alt=row['alt']
            if fasta.fetch(row['chrom'],pos-1,pos-1+len(ref)).upper()!=ref:raise ValueError('Annotation input REF mismatch')
            rec=out.new_record(contig=row['chrom'],start=pos-1,alleles=(ref,alt));rec.filter.add('PASS')
            fmt=rec.samples[sample];rec.info['EVIDENCE_SOURCE']='mitochondrial_dominant_allele' if mito else 'archaic_marker_imputed'
            if mito:fmt['GT']=(1,);fmt['DP']=int(row['depth']);fmt['AF']=float(row['fraction'])
            else:
                fmt['GT']=tuple((ref,alt).index(a.upper()) for a in re.split(r'[|/]',row['GT']));fmt.phased='|' in row['GT'];fmt['IGP']=float(row['GP']);fmt['IINFO']=float(row['INFO'])
            fmt['ZYG']=zygosity(fmt['GT'],ref,alt,rec.contig);fmt['AFFSTAT']='not_established';fmt['CARRIER']='not_applicable' if mito else 'unknown'
            out.write(rec)

def run(a,c):
    out=Path(a.sample+'.special_annotations');out.mkdir(exist_ok=True);bc=c['tools']['bcftools'];protocol=c['annotation'].get('protocol','refGene')
    def call(args):subprocess.run([str(x) for x in args],check=True)
    with pysam.VariantFile(str(a.clinvar)) as cv:
        columns=','.join('INFO/'+k for k in ['CLNSIG','CLNREVSTAT','CLNDN','ALLELEID','GENEINFO'] if k in cv.header.info)
    if not columns:raise ValueError('ClinVar has no recognized annotation fields')
    ancestry=next((Path(p) for p in a.ancestry if Path(p).is_dir()),None)
    with pysam.FastaFile(c['reference']) as fa:
        mitochondrial=[];archaic=[];effects={}
        if ancestry:
            rawmt=ancestry/(a.sample+'.mitochondrial_variants.tsv')
            mt=next((x for x in fa.references if x in {'chrM','MT','M'}),None)
            if rawmt.is_file() and mt:
                mitochondrial=[{**r,'chrom':mt,'pos':int(r['position']),'alt':r['allele'],'genotype':r['allele']} for r in rows(rawmt)]
                tree=Path(c['ancestry_explorer_cache'])/'haplogrep3-linux/trees/phylotree-rcrs/17.3'
                features=mt_features(tree/'mtdna.gff');sequence=fa.fetch(mt).upper()
                # The installed code table explicitly encodes vertebrate mtDNA.
                codons={r['Codon']:r['Letter'] for r in rows(tree/'mtdna.aac.txt')}
                codons.update(ATA='M',TGA='W',AGA='*',AGG='*',TAA='*',TAG='*')
                for r in mitochondrial:effects[(r['chrom'],r['pos'],r['ref'],r['alt'])]=mt_effect(r['pos'],r['ref'],r['alt'],sequence,features,codons)
            rawneo=ancestry/(a.sample+'.neanderthal_markers.tsv.gz')
            if rawneo.is_file():archaic=[{**r,'pos':int(r['position']),'genotype':r['GT']} for r in rows(rawneo) if int(r['derived_copies'])>0]
        categories={};details={}
        for kind,records in [('mitochondrial',mitochondrial),('neanderthal',archaic)]:
            source=Path(kind+'.input.vcf.gz');write_input(source,records,a.sample,fa,kind=='mitochondrial');call([bc,'index','-f','-t',source])
            clinical=Path(kind+'.clinical.vcf.gz');dest=out/(a.sample+'.'+kind+'.annotated.vcf.gz')
            call([bc,'annotate','-a',a.clinvar,'-c',columns,'-Oz','-o',clinical,source]);call([bc,'index','-f','-t',clinical])
            if kind=='neanderthal' and records and c['annotation']['engine']=='annovar':
                source=Path(kind+'.clinical.vcf');call([bc,'view','-Ov','-o',source,clinical]);ann=c['annotation'];prefix=str(Path(kind+'.genes'))
                call([c['tools']['perl'],str(Path(ann['directory'])/'table_annovar.pl'),source,ann['database'],'-buildver','hg38','-out',prefix,'-protocol',protocol,'-operation','g','-nastring','.','-vcfinput','-polish'])
                call([bc,'view','-Oz','-o',dest,prefix+'.hg38_multianno.vcf'])
            elif kind=='mitochondrial':
                with pysam.VariantFile(str(clinical)) as sourcevcf:
                    for name in ['MGENE','MCONSEQ','MPROTEIN']:sourcevcf.header.info.add(name,1,'String','rCRS mitochondrial annotation; genetic code 2')
                    with pysam.VariantFile(str(dest),'wz',header=sourcevcf.header) as v:
                        for rec in sourcevcf:
                            eff=effects[(rec.contig,rec.pos,rec.ref,rec.alts[0])]
                            for field,key in [('MGENE','gene'),('MCONSEQ','consequence'),('MPROTEIN','protein_change')]:rec.info[field]=eff[key].replace(';','|')
                            v.write(rec)
            else:shutil.copyfile(clinical,dest)
            call([bc,'index','-f','-t',dest]);annot={}
            with pysam.VariantFile(str(dest)) as v:
                for rec in v:
                    key=(rec.contig,rec.pos,rec.ref,rec.alts[0]);annot[key]=info_annotation(rec,protocol)
                    if kind=='mitochondrial':annot[key].update(effects[key])
            for r in records:r.update(annot[(r['chrom'],r['pos'],r['ref'],r['alt'])])
            categories[kind]=records
            details[kind]={'records':len(records),'clinvar_matches':sum(r['clinvar_allele_id']!='.' for r in records),'scope':NOTES[kind],'vcf':dest.name}
    somaticpath=next(Path(p) for p in a.evidence_tables if str(p).endswith('.somatic_candidate.tsv.gz'))
    somatic=rows(somaticpath);targets={(r['chrom'],int(r['pos']),r['ref'].upper(),r['alt'].upper()):r for r in somatic}
    if len(targets)!=len(somatic):raise ValueError('Duplicate somatic candidate')
    bed=Path('somatic_targets.bed');bed.write_text(''.join(f'{ch}\t{pos-1}\t{pos}\n' for ch,pos,ref,alt in targets))
    subset=Path('somatic.subset.bcf');call([bc,'view',*(['-R',bed] if targets else ['--header-only']),'-Ob','-o',subset,a.somatic]);call([bc,'index','-f',subset])
    dest=out/(a.sample+'.somatic_candidates.annotated.vcf.gz');seen=set()
    with pysam.VariantFile(str(a.somatic)) as original:export_header=original.header.copy()
    add_status_formats(export_header)
    with pysam.VariantFile(str(subset)) as v, pysam.VariantFile(str(dest),'wz',header=export_header) as export:
        for rec in v:
            if len(rec.alts or ())!=1:continue
            key=(rec.contig,rec.pos,rec.ref.upper(),rec.alts[0].upper())
            if key not in targets or key in seen:continue
            r=targets[key];r.update(info_annotation(rec,protocol),genotype=r['somatic_GT'],TLOD=flat(dict(rec.info).get('TLOD')));rec.ref=key[2];rec.alts=(key[3],);rec.translate(export_header);rec.samples[a.sample]['ZYG']=zygosity(rec.samples[a.sample].get('GT'),key[2],key[3],key[0]);rec.samples[a.sample]['AFFSTAT']='not_established';rec.samples[a.sample]['CARRIER']='not_applicable';export.write(rec);seen.add(key)
    if seen!=set(targets):raise ValueError('Somatic candidates missing from original annotated VCF')
    call([bc,'index','-f','-t',dest]);categories['somatic']=somatic;details['somatic']={'records':len(somatic),'clinvar_matches':sum(r['clinvar_allele_id']!='.' for r in somatic),'scope':NOTES['somatic'],'vcf':dest.name}
    book=Excel(out/(a.sample+'.special_annotations.xlsx'),[[a.sample+' · additional variant annotations']]+[[k,v] for k,v in NOTES.items()])
    extras={'mitochondrial':['depth','fraction','notation'],'neanderthal':['GP','INFO','derived_copies','alt_dosage','foundin'],'somatic':['category','reason','somatic_DP','somatic_AD','somatic_VAF','somatic_filter','TLOD']}
    links=[('Excel',a.sample+'.special_annotations.xlsx')]
    for kind,records in categories.items():
        for r in records:r.update(for_marker(r,kind))
        fields=COMMON+extras[kind];name=a.sample+'.'+kind+'.annotated.tsv.gz';table(out/name,fields,records);book.create(kind.capitalize(),fields)
        for r in records:book.add(kind.capitalize(),r)
        links += [(kind.capitalize()+' TSV',name),(kind.capitalize()+' VCF',details[kind]['vcf'])]
    book.close()
    from genome_report_ui import write,section,table as html_table,cards,esc
    body=cards([(sum(len(v) for v in categories.values()),'annotated variants'),(sum(v['clinvar_matches'] for v in details.values()),'exact ClinVar matches')])
    columns=[('Variant','variant'),('Gene','gene'),('Genotype','genotype'),('Reported zygosity','zygosity_label'),('Carrier status','carrier_label'),('Affected status','affected_label'),('ClinVar classification','clinvar_classification'),('Molecular effect','consequence'),('Protein / transcript change','protein_change'),('Condition','condition'),('ClinVar review','clinvar_review'),('ClinVar record','clinvar_url')]
    for kind,records in categories.items():
        display=[]
        for original in records:
            r=dict(original);r['variant']=':'.join(str(r[k]) for k in ['chrom','pos','ref','alt'])
            for k in ['gene','consequence','protein_change']:
                if r.get(k) in {None,'','.'}:r[k]='Not available'
            if r.get('clinvar_allele_id') in {None,'','.'}:r['clinvar_classification']='No exact ClinVar match'
            display.append(r)
        display.sort(key=lambda r:(r.get('clinvar_allele_id','.')=='.',r['chrom'],int(r['pos'])))
        extra=[('Read depth','somatic_DP'),('Alternate fraction','somatic_VAF'),('Original filter','somatic_filter')] if kind=='somatic' else [('Read depth','depth'),('Major allele fraction','fraction')] if kind=='mitochondrial' else [('Imputed posterior','GP'),('Imputation INFO','INFO'),('Derived copies','derived_copies')]
        body+=section(kind.capitalize()+' variants', '<p>'+esc(NOTES[kind])+'</p><p>'+str(details[kind]['clinvar_matches'])+' of '+str(len(records))+' records match ClinVar. Other rows show the available molecular effect; no match does not imply benignity.</p>'+html_table(display,columns+extra,True),kind)
    write(out/(a.sample+'.special_annotations.html'),a.sample+' · additional annotated variants','Search every mitochondrial SNP, carried Neanderthal-associated marker and somatic candidate below. Click a heading to sort.',body,[('All results','../../START_HERE.html')]+links)
    dump(out/(a.sample+'.special_annotations.json'),{'sample':a.sample,'runtime_signature':runtime_signature(c),'categories':details,'clinvar_source':c['clinvar'],'reference':c['reference'],'ancestry_source':str(ancestry),'sources':['https://pmc.ncbi.nlm.nih.gov/articles/PMC7717623/','https://www.ncbi.nlm.nih.gov/nuccore/NC_012920.1','https://www.nature.com/articles/s41586-020-2225-9']})

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--config',required=True);p.add_argument('--sample',required=True);p.add_argument('--clinvar',required=True);p.add_argument('--somatic',required=True);p.add_argument('--ancestry',nargs='*',default=[]);p.add_argument('--evidence-tables',nargs='+',required=True)
    a=p.parse_args();run(a,json.loads(Path(a.config).read_text()))
