"""Versioned public literature/ClinVar retrieval. Never send sample data to a service."""
import datetime
import fcntl
import hashlib
import json
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

EUTILS = 'https://eutils.ncbi.nlm.nih.gov/entrez/eutils/'
_NETWORK_FAILURES = 0


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def get_public(cache, endpoint, params, offline=False, release='2026-10'):
    """Pin responses per release. An unavailable source is explicit, not fabricated."""
    global _NETWORK_FAILURES
    url = EUTILS + endpoint + '?' + urllib.parse.urlencode(params)
    key = hashlib.sha256((release+'\n'+url).encode()).hexdigest()
    folder = Path(cache); folder.mkdir(parents=True, exist_ok=True)
    path = folder/(key+'.json')
    with open(folder/(key+'.lock'), 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if path.exists():
            value = json.loads(path.read_text())
            if value['sha256'] != hashlib.sha256(value['body'].encode()).hexdigest():
                raise ValueError('Literature cache checksum mismatch: '+str(path))
            return value
        if _NETWORK_FAILURES >= 3 and not offline:
            return {'status':'unavailable_network_circuit_breaker','url':url,'body':'','retrieved_utc':None}
        if offline:
            return {'status':'unavailable_offline','url':url,'body':'','retrieved_utc':None}
        error = ''
        for attempt in range(3):
            try:
                request = urllib.request.Request(url, headers={'User-Agent':'LPWGS-evidence-report/1.0'})
                with urllib.request.urlopen(request, timeout=20) as response:
                    body = response.read().decode('utf-8')
                if params.get('retmode') == 'json':
                    data = json.loads(body)
                    if 'error' in data: raise ValueError(str(data['error']))
                else:
                    xml = ET.fromstring(body)
                    if xml.find('.//ERROR') is not None: raise ValueError(xml.findtext('.//ERROR'))
                    if params.get('db') == 'clinvar' and not xml.findall('.//VariationArchive'):
                        raise ValueError('ClinVar returned no variation archives')
                value = {'status':'retrieved','url':url,'retrieved_utc':now(), 'release':release,
                         'sha256':hashlib.sha256(body.encode()).hexdigest(),'body':body}
                temporary = path.with_suffix('.tmp'); temporary.write_text(json.dumps(value)); temporary.replace(path)
                _NETWORK_FAILURES = 0
                time.sleep(.36)
                return value
            except (OSError, ValueError, ET.ParseError) as exc:
                error = type(exc).__name__+': '+str(exc)
                if attempt < 2: time.sleep(attempt+1)
        _NETWORK_FAILURES += 1
        return {'status':'unavailable_network','url':url,'body':'','retrieved_utc':None,'error':error}


def text(node, path, default=''):
    found = node.find(path)
    return ''.join(found.itertext()).strip() if found is not None else default


def parse_clinvar(body):
    results = {}
    for archive in ET.fromstring(body).findall('.//VariationArchive'):
        record = archive.find('ClassifiedRecord')
        if record is None: continue
        allele = record.find('SimpleAllele')
        if allele is None: continue
        summary = record.find('Classifications/GermlineClassification')
        assertions = []
        for assertion in record.findall('.//ClinicalAssertion'):
            accession = assertion.find('ClinVarAccession')
            review = text(assertion,'Classification/ReviewStatus')
            conditions = sorted(set(e.text for e in assertion.findall('.//TraitSet/Trait/Name/ElementValue')
                                    if e.get('Type') == 'Preferred' and e.text))
            assertions.append({'accession':accession.get('Accession','') if accession is not None else '',
                'submitter':accession.get('SubmitterName','') if accession is not None else '',
                'classification':text(assertion,'Classification/GermlineClassification') or text(assertion,'Classification/OncogenicityClassification') or text(assertion,'Classification/SomaticClinicalImpact/ClinicalImpactDescription'),
                'classification_domain':'germline' if assertion.find('Classification/GermlineClassification') is not None else 'oncogenicity' if assertion.find('Classification/OncogenicityClassification') is not None else 'somatic clinical impact',
                'review':review,'flagged':'flagged' in review.lower(),'conditions':conditions,
                'origin':sorted(set(e.text for e in assertion.findall('.//Sample/Origin') if e.text)),
                'method':sorted(set(e.text for e in assertion.findall('.//MethodType') if e.text)),
                'pmids':sorted(set(e.text for e in assertion.findall('.//Citation/ID') if e.get('Source')=='PubMed' and e.text)),
                'inheritance':sorted(set(e.text for e in assertion.findall('.//Attribute') if e.get('Type')=='ModeOfInheritance' and e.text))})
        locations = [dict(e.attrib) for e in allele.findall('Location/SequenceLocation') if e.get('Assembly')=='GRCh38']
        vid = archive.get('VariationID')
        results[vid] = {'variation_id':vid,'allele_id':allele.get('AlleleID'),
            'accession':archive.get('Accession')+'.'+archive.get('Version',''),
            'name':archive.get('VariationName'),'updated':archive.get('DateLastUpdated'),
            'classification':text(summary,'Description') if summary is not None else '',
            'review':text(summary,'ReviewStatus') if summary is not None else '',
            'locations':locations,'assertions':assertions,
            'pmids':sorted(set(e.text for e in record.findall('.//Citation/ID') if e.get('Source')=='PubMed' and e.text)),
            'url':'https://www.ncbi.nlm.nih.gov/clinvar/variation/'+vid+'/'}
    return results


def clinvar_records(ids, config):
    results = {}; requests = []
    ids = sorted({str(x) for x in ids if re.fullmatch(r'\d+', str(x))}, key=int)
    for start in range(0,len(ids),20):
        response = get_public(config['literature_cache'],'efetch.fcgi',
            {'db':'clinvar','id':','.join(ids[start:start+20]),'rettype':'vcv','is_variationid':'true','retmode':'xml'},
            config.get('literature_offline',False),config['literature_release'])
        requests.append({k:v for k,v in response.items() if k!='body'})
        if response['body']:
            for key,value in parse_clinvar(response['body']).items():
                value['retrieved_utc'] = response['retrieved_utc']; value['source_sha256'] = response['sha256']
                results[key] = value
    return results,requests


def publications(ids,config):
    results = {}; requests = []
    ids = sorted({str(x) for x in ids if re.fullmatch(r'\d+', str(x))},key=int)
    for start in range(0,len(ids),150):
        batch = ids[start:start+150]
        response = get_public(config['literature_cache'],'esummary.fcgi',
            {'db':'pubmed','id':','.join(batch),'retmode':'json'},
            config.get('literature_offline',False),config['literature_release'])
        requests.append({k:v for k,v in response.items() if k!='body'})
        data = json.loads(response['body']).get('result',{}) if response['body'] else {}
        for pmid in batch:
            item = data.get(pmid,{})
            results[pmid] = {'pmid':pmid,'title':item.get('title','Bibliographic metadata unavailable'),
                'year':item.get('pubdate',''),'journal':item.get('fulljournalname',''),
                'authors':'; '.join(x['name'] for x in item.get('authors',[])[:3]),
                'url':'https://pubmed.ncbi.nlm.nih.gov/'+pmid+'/',
                'retrieved_utc':response['retrieved_utc'],'status':'verified_metadata' if item.get('title') else 'unavailable',
                'scope':'Citation metadata only; this is not a review of the article or proof of variant causality.'}
    return results,requests
