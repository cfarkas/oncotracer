"""Explicit research evidence presets for trait/GWAS reporting only."""
PRESETS={
 'strict':{'gp':.99,'info':.8,'read_dp':10,'read_gq':20},
 'medium':{'gp':.8,'info':.3,'read_dp':4,'read_gq':10},
 'exploratory':{'gp':.7,'info':.2,'read_dp':2,'read_gq':5},
}


def assess(direct,imputed,c):
    import pipeline as p
    from traits_lpwgs import imputed_call
    policy=PRESETS[c['confidence_gwas']]
    strict=p.genotype(direct,c['min_depth'],c['min_gq'])
    strict.update(genotype_source='reads',confidence='high' if strict['status']=='called' else 'unresolved',GP='.',DS='.',INFO='.')
    settings={**c,'imputation_min_gp':policy['gp'],'imputation_min_info':policy['info']}
    imp=imputed_call(imputed,settings)
    # A missing site-quality metric cannot satisfy an explicit confidence preset.
    if imp['INFO']=='.':imp['status']='low_imputation_confidence'
    if imp['status']=='imputed':
        imp['confidence']='high' if imp['GP']>=.99 else 'supported' if imp['GP']>=.9 else 'medium' if imp['GP']>=.8 else 'exploratory'
    if strict['status']=='called':
        if imp['status']=='imputed' and sorted(imp['alleles'])!=sorted(strict['alleles']):strict['discordance']='reads_vs_imputation'
        return strict
    if imp['status']=='imputed':
        imp['DP']=strict['DP'];imp['AD']=strict['AD'];return imp
    relaxed=p.genotype(direct,policy['read_dp'],policy['read_gq'])
    if relaxed['status']=='called':
        relaxed.update(status='exploratory',genotype_source='reads',confidence='medium' if c['confidence_gwas']=='medium' else 'exploratory',GP='.',DS='.',INFO='.')
        return relaxed
    return strict
