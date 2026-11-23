#!/usr/bin/env python3
"""Plot aggregate CPU, RAM, GPU and disk measurements with allocation labels."""
import argparse
import csv
import json
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--directory',type=Path,required=True)
    a=p.parse_args()
    with (a.directory/'resource_timeseries.csv').open() as h:
        rows=list(csv.DictReader(h))
    summary=json.loads((a.directory/'benchmark_summary.json').read_text())
    allocations={row['cohort']:row for row in summary['cohorts']}
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':10.5,
        'axes.titlesize':13,'axes.titleweight':'bold','axes.labelcolor':'black',
        'text.color':'black','svg.fonttype':'none','pdf.fonttype':42})
    fig,axes=plt.subplots(2,3,figsize=(15,8),sharex=True)
    cohorts=[('Illumina_FFPE','Illumina FFPE','#2878A6'),
        ('ONT_Fresh','ONT fresh','#D75D35'),('Illumina_Fresh','Illumina fresh','#8266A2')]
    metrics=[('cpu_cores','A  Logical CPU use','Active thread equivalents',1),
        ('anonymous_ram_gib','B  Application RAM','Anonymous + shared memory (GiB)',1),
        ('cgroup_memory_including_cache_gib','C  RAM including page cache','GiB',1),
        ('allocated_project_disk_gib','D  Project disk allocation','GiB',1),
        ('gpu_memory_mib','E  GPU memory','Tracked analysis VRAM (GiB)',1024),
        ('gpu_sm_percent','F  GPU process activity','Tracked process SM activity (%)',1)]
    for ax,(key,title,ylabel,scale) in zip(axes.flat,metrics):
        for name,label,color in cohorts:
            allocation=allocations[name]
            points=[r for r in rows if r['cohort']==name and r[key]!='']
            legend=(f"{label} · {allocation['samples']} samples\n"
                f"{allocation['allocated_physical_cores']} cores / {allocation['allocated_logical_cpus']} threads"
                f" · {allocation['memory_budget_gib']} GiB RAM limit")
            ax.plot([float(r['elapsed_hours']) for r in points],
                [float(r[key])/scale for r in points],color=color,label=legend,lw=1.5)
        ax.set_title(title,loc='left',pad=10)
        ax.set_ylabel(ylabel);ax.set_ylim(bottom=0);ax.grid(axis='y',alpha=.18)
        ax.spines[['top','right']].set_visible(False)
    for ax in axes[1]:ax.set_xlabel('Hours since concurrent benchmark start')
    axes[1,1].text(.98,.96,'ONT methylation only\nRTX A5000 · 24 GiB',
        transform=axes[1,1].transAxes,ha='right',va='top',fontsize=9.5)
    handles,labels=axes[0,0].get_legend_handles_labels()
    fig.legend(handles,labels,loc='lower center',ncol=3,frameon=False,
        bbox_to_anchor=(.5,.01),fontsize=10)
    fig.subplots_adjust(left=.06,right=.99,top=.94,bottom=.19,wspace=.29,hspace=.35)
    for suffix in ['svg','pdf','png']:
        fig.savefig(a.directory/('resource_usage.'+suffix),dpi=180,
            metadata={'Creator':'OncoTracer benchmark exporter'})
    svg=a.directory/'resource_usage.svg'
    svg.write_text('\n'.join(line.rstrip() for line in svg.read_text().splitlines())+'\n')
    plt.close(fig)


if __name__=='__main__':main()
