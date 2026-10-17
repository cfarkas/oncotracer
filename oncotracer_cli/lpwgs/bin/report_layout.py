"""Readable wide reports; full precision remains in TSV/Excel exports."""
import html
from pathlib import Path

CALIBRATION_NOTE = ('Sex is used for model applicability and age is recorded as context. '
    'Reference percentiles are not stratified by age or sex. No age- or sex-specific '
    'absolute disease probability has been calibrated; that would require a validated '
    'clinical model and suitable outcome, incidence and covariate data.')
WIDE_CSS = """.scroll{overflow-x:auto;max-width:100%;overscroll-behavior-x:contain}
table.readable{table-layout:auto;width:max-content;min-width:100%;border-collapse:collapse}
table.readable th,table.readable td{min-width:125px;max-width:420px;white-space:normal;word-break:normal;overflow-wrap:normal;vertical-align:top}
table.readable .number{min-width:130px;width:130px}table.readable td.number{white-space:nowrap;font-variant-numeric:tabular-nums}
table.readable .label{min-width:260px;width:300px}table.readable .description{min-width:340px;width:380px}
table.readable .identifier{min-width:130px;white-space:nowrap}table.readable th{line-height:1.4}
.table-hint{font-size:.85rem;color:#536762}@media print{table.readable{width:100%;min-width:0}table.readable th,table.readable td{min-width:0}}"""

def kind(key):
    if key in {'pgs_id','model','rsid'}:return 'identifier'
    if key in {'trait','name','condition','development_ancestry','evaluation_ancestry'}:return 'label'
    if any(x in key for x in ('reason','interpretation','explanation','status','limitation')):return 'description'
    if any(x in key for x in ('score','percent','coverage','rank','reference_n','reference_z')):return 'number'
    return 'label'

def display(value):
    if value is None:return 'Not available'
    if isinstance(value,float):return f'{value:.6g}'
    return str(value)

def model_page(path,title,introduction,rows,fields,links=()):
    e=lambda x:html.escape(str(x),quote=True)
    headers=''.join('<th class="'+kind(k)+'">'+e(k.replace('_',' ').capitalize())+'</th>' for k in fields)
    cells=''.join('<tr>'+''.join('<td class="'+kind(k)+'">'+e(display(row.get(k)))+'</td>' for k in fields)+'</tr>' for row in rows)
    nav=' · '.join('<a href="'+e(url)+'">'+e(label)+'</a>' for label,url in links)
    Path(path).write_text('<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+e(title)+'</title><style>body{font:16px/1.6 system-ui;margin:2rem;color:#173044}th,td{border:1px solid #dce5ea;padding:12px;text-align:left}th{background:#eef4f7}'+WIDE_CSS+'</style><h1>'+e(title)+'</h1><p>'+e(introduction)+'</p><p>'+e(CALIBRATION_NOTE)+'</p><p>'+nav+'</p><p class="table-hint">Scroll horizontally to see all fields. Displayed sums are rounded; exports retain full precision.</p><div class="scroll" tabindex="0" aria-label="Polygenic model results"><table class="readable"><thead><tr>'+headers+'</tr></thead><tbody>'+cells+'</tbody></table></div></html>')
