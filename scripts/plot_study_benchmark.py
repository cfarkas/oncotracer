#!/usr/bin/env python3
"""Plot the public aggregate resource series exported by export_study_benchmark.py."""
import argparse
import csv
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--directory',type=Path,required=True);a=p.parse_args()
    with (a.directory/'resource_timeseries.csv').open() as h:rows=list(csv.DictReader(h))
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':11,'axes.titlesize':13,'axes.titleweight':'bold','axes.labelcolor':'black','text.color':'black','svg.fonttype':'none','pdf.fonttype':42})
    fig,axes=plt.subplots(2,2,figsize=(12,7.4),sharex=True)
    cohorts=[('Illumina_FFPE','Illumina FFPE · 26 samples','#2878A6'),('ONT_Fresh','ONT fresh · 21 samples','#D75D35'),('Illumina_Fresh','Illumina fresh · 11 samples','#8266A2')]
    metrics=[('cpu_cores','A  CPU use','CPU cores'),('anonymous_ram_gib','B  Anonymous RAM','GiB'),('cgroup_memory_including_cache_gib','C  Memory including page cache','GiB'),('allocated_project_disk_gib','D  Project disk allocation','GiB')]
    for ax,(key,title,ylabel) in zip(axes.flat,metrics):
        for name,label,color in cohorts:
            points=[r for r in rows if r['cohort']==name and r[key]!='']
            ax.plot([float(r['elapsed_hours']) for r in points],[float(r[key]) for r in points],color=color,label=label,lw=1.6)
        ax.set_title(title,loc='left',pad=10);ax.set_ylabel(ylabel);ax.set_ylim(bottom=0);ax.grid(axis='y',alpha=.18)
        ax.spines[['top','right']].set_visible(False)
    for ax in axes[1]:ax.set_xlabel('Hours since the concurrent benchmark began')
    handles,labels=axes[0,0].get_legend_handles_labels();fig.legend(handles,labels,loc='lower center',ncol=3,frameon=False,bbox_to_anchor=(.5,.01))
    fig.subplots_adjust(left=.08,right=.98,top=.93,bottom=.15,wspace=.23,hspace=.35)
    for suffix in ['svg','pdf','png']:fig.savefig(a.directory/('resource_usage.'+suffix),dpi=180,metadata={'Creator':'OncoTracer benchmark exporter'})
    svg=a.directory/"resource_usage.svg"
    svg.write_text("\n".join(line.rstrip() for line in svg.read_text().splitlines())+"\n")
    plt.close(fig)


if __name__=='__main__':main()
