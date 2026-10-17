"""Require the GRCh38 allele, not just an identifier, to match clinical evidence."""
import re


def minimal(pos, ref, alt):
    """Trim VCF padding only; repeat-shifted indels are withheld for review."""
    while len(ref)>1 and len(alt)>1 and ref[-1]==alt[-1]: ref,alt=ref[:-1],alt[:-1]
    while len(ref)>1 and len(alt)>1 and ref[0]==alt[0]: pos,ref,alt=pos+1,ref[1:],alt[1:]
    return pos,ref.upper(),alt.upper()


def compare_public_allele(variant, local, public):
    if not public:return {'status':'not_verified','message':'No public record available for exact-allele verification.'}
    ch,pos,ref,alt=variant.split(':');want=(ch.removeprefix('chr'),*minimal(int(pos),ref,alt))
    observed=[]
    for loc in public.get('locations',[]):
        if loc.get('Assembly')!='GRCh38':continue
        try:
            p=int(loc['positionVCF']);r=loc['referenceAlleleVCF'];a=loc['alternateAlleleVCF']
            observed.append((loc['Chr'].removeprefix('chr'),*minimal(p,r,a)))
        except (KeyError,TypeError,ValueError):continue
    if want in observed:return {'status':'matched','message':'Public and local GRCh38 chromosome, position, reference and alternate alleles agree.'}
    if not observed:return {'status':'not_verified','message':'Public record lacks an explicit comparable GRCh38 VCF allele.'}
    names=['chr'+str(c)+':'+str(p)+':'+r+':'+a for c,p,r,a in observed]
    return {'status':'mismatch','local_allele':variant,'public_alleles':names,
            'message':'Annotation identity mismatch: local '+variant+' is linked to '+public.get('accession','the public record')+
                      ', whose GRCh38 allele is '+', '.join(names)+'. Its classification is withheld for the local allele.'}
