#!/usr/bin/env python3
"""Rebuild the source-grounded GRCh38 SNP panel (public identifiers only)."""
import concurrent.futures,csv,json,re,urllib.request,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def main():
    source=ROOT/'data/hirisplex.source.html'
    if not source.exists():
        source.write_bytes(urllib.request.urlopen('https://hirisplex.erasmusmc.nl/',timeout=30).read())
    html=source.read_text()
    markers={}
    for raw in re.findall(r'<tr\b.*?</tr>',html,re.S|re.I):
        text=re.sub('<[^>]+>',' ',raw);m=re.search(r'\b(rs\d+)\s+([ACGT])\b',text)
        if m:markers[m[1]]={'trait':'Pigmentation (HIrisPlex-S marker)','source_url':'https://hirisplex.erasmusmc.nl/','pubmed':''}
    extra={'rs1815739':'ACTN3 muscle function','rs713598':'Bitter taste (TAS2R38 haplotype marker)',
      'rs1726866':'Bitter taste (TAS2R38 haplotype marker)','rs10246939':'Bitter taste (TAS2R38 haplotype marker)',
      'rs762551':'Caffeine metabolism','rs1229984':'Alcohol metabolism (ADH1B)',
      'rs671':'Alcohol metabolism (ALDH2)','rs9939609':'Body mass association (FTO)'}
    for rs,trait in extra.items():markers[rs]={'trait':trait,'source_url':f'https://bots.snpedia.com/index.php/{rs[0].upper()+rs[1:]}','pubmed':''}
    old=list(csv.DictReader((ROOT/'data/traits.GRCh38.tsv').open(),delimiter='\t'))
    oldids={r['rsid'] for r in old};sources={};skipped=[]
    def lookup(rs):
        url=f'https://rest.ensembl.org/variation/human/{rs}?content-type=application/json'
        cache=ROOT/'data/ensembl_evidence';cache.mkdir(exist_ok=True)
        dest=cache/(rs+'.json')
        if dest.exists():
            data=json.loads(dest.read_text());return rs,data.get('_source_url',url),data
        for attempt in range(4):
            try:
                data=json.load(urllib.request.urlopen(url,timeout=12));dest.write_text(json.dumps(data));return rs,url,data
            except Exception as exc:
                if attempt==3:
                    alt_url='https://api.ncbi.nlm.nih.gov/variation/v0/refsnp/'+rs[2:]
                    raw=json.load(urllib.request.urlopen(alt_url,timeout=30))
                    mappings=[]
                    for placement in raw['primary_snapshot_data']['placements_with_allele']:
                        builds=placement['placement_annot'].get('seq_id_traits_by_assembly',[])
                        if not any(b['assembly_name'].startswith('GRCh38') for b in builds) or not placement['seq_id'].startswith('NC_'):continue
                        chrom=str(int(placement['seq_id'].split('_')[1].split('.')[0]))
                        alleles=[x['allele']['spdi'] for x in placement['alleles']]
                        a=alleles[0];ref=a['deleted_sequence']
                        seqs=list(dict.fromkeys([ref]+[x['inserted_sequence'] for x in alleles]))
                        mappings.append({'assembly_name':'GRCh38','seq_region_name':chrom,'strand':1,'start':a['position']+1,'end':a['position']+len(ref),'allele_string':'/'.join(seqs)})
                    data={'mappings':mappings,'_source_url':alt_url};dest.write_text(json.dumps(data));return rs,alt_url,data
                time.sleep(1+attempt)
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
        for rs,url,data in ex.map(lookup,markers):
            maps=[m for m in data['mappings'] if m['assembly_name']=='GRCh38' and m['seq_region_name'] in map(str,range(1,23)) and m['strand']==1]
            if len(maps)!=1:skipped.append({'rsid':rs,'reason':'ambiguous genomic mapping'});continue
            m=maps[0];alleles=m['allele_string'].split('/')
            sources[rs]={'url':url,'mapping':m,**markers[rs]}
            if rs in oldids:continue
            if m['start']!=m['end'] or any(len(x)!=1 or x not in 'ACGT' for x in alleles):
                skipped.append({'rsid':rs,'reason':'indel or non-SNP; not supported by this SNP panel'});continue
            old.append({'chrom':m['seq_region_name'],'pos':m['start'],'rsid':rs,'ref':alleles[0],
              'alt':','.join(alleles[1:]),'effect_allele':'.','trait':markers[rs]['trait'],'model':'association_only',
              'association':'Published trait-associated marker; this genotype alone does not determine the phenotype.',
              'limitation':'Interpret jointly with other markers and study population; no standalone directional effect assigned.',
              'pubmed':markers[rs]['pubmed'],'source_url':markers[rs]['source_url']})
    alternatives={'rs12913832':'This marker favors darker eye pigmentation over the G/G-associated lighter pigmentation pattern; other genes can change the phenotype.',
        'rs4988235':'No lactase-persistence allele is present at this marker; other persistence variants and non-genetic factors are not assessed.',
        'rs17822931':'A C allele at this marker is associated with wet earwax.'}
    for row in old:
        row['alternative_association']=alternatives.get(row['rsid'],'')
        if row['rsid']=='rs1815739':
            row.update(alt='T',effect_allele='T',model='recessive',association='Two T alleles predict alpha-actinin-3 deficiency; this does not reliably predict athletic ability.',alternative_association='This marker does not predict alpha-actinin-3 deficiency.',limitation='Athletic performance is influenced by many genetic and non-genetic factors; no athlete classification is assigned.')
        if row['rsid']=='rs671':
            row.update(alt='A',effect_allele='A',model='dominant',association='An A allele is associated with reduced ALDH2 activity and increased alcohol-flushing tendency.',alternative_association='This marker does not indicate the ALDH2 A-associated reduced-activity form.',limitation='A single marker does not determine symptoms or alcohol-related health outcomes.')
    fields=list(dict.fromkeys(list(old[0])+['source_url','alternative_association']))
    with (ROOT/'data/traits.expanded.GRCh38.tsv').open('w') as f:
        w=csv.DictWriter(f,fieldnames=fields,delimiter='\t');w.writeheader();w.writerows(old)
    (ROOT/'data/traits.expanded.sources.json').write_text(json.dumps({'assembly':'GRCh38','sources':sources,'excluded':skipped},indent=2)+'\n')
    print('Expanded panel:',len(old),'markers. Excluded:',skipped)
if __name__=='__main__':main()
