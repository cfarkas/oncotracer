import csv
import gzip
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from oncotracer_cli.cna_options import resolve_policy, validate_profile, NEW_PROJECT_PROFILE
from oncotracer_cli.cna_evidence_guard import ReadSupport, publish_guard, requires_read_support
from oncotracer_cli.cna_quality import publish_assessment
from oncotracer_cli.runtime import OncoTracerError
from tests.test_broad_cna import synthetic


def event(chrom='2', start=1, end=50_000_000, state='gain', sample='control'):
    return dict(sample=sample, chrom=chrom, start=start, end=end, state=state)


class EvidenceTests(unittest.TestCase):
    def test_sparse_neutral_controls_reject_both_apparent_gains_and_losses(self):
        for seed in (12, 27, 49):
            calls=[event(state='gain'), event(chrom='7', state='loss')]
            results=ReadSupport(synthetic(seed=seed, mean=1)).evaluate(calls)
            self.assertTrue(all(r['decision']=='rejected' for r in results))
            self.assertTrue(all(r['reason']=='uncertain_or_discordant_read_depth' for r in results))

    def test_supported_control_gain_and_loss_are_retained(self):
        support=ReadSupport(synthetic(changes={'2':2.2,'7':.35}, mean=1))
        rows=support.evaluate([event(), event(chrom='7',state='loss')])
        self.assertTrue(all(r['decision']=='retained' for r in rows), rows)
        self.assertGreater(rows[0]['lower_99'], .15)
        self.assertLess(rows[1]['upper_99'], -.15)
        self.assertTrue(all(r['approximate_q']<=.01 for r in rows))

    def test_insufficient_or_absent_reads_never_become_a_deletion(self):
        for mean in (.001,.01):
            r=ReadSupport(synthetic(mean=mean)).evaluate([event(state='loss')])[0]
            self.assertEqual(r['reason'],'insufficient_genome_wide_read_support')
        r=ReadSupport(synthetic(changes={'2':0})).evaluate([event(state='loss')])[0]
        self.assertEqual(r['reason'],'insufficient_region_read_support')
        self.assertIsNone(r['log2_ratio'])

    def test_focal_control_changes_need_local_read_support(self):
        rows=synthetic(mean=40)
        for b in rows:
            if b['chromosome']=='2' and 10_000_001<=b['start']<=12_000_000:
                b['reads'] *= 2
        r=ReadSupport(rows).evaluate([event(start=10_000_001,end=12_000_000)])[0]
        self.assertEqual(r['decision'],'retained',r)
        low=ReadSupport(synthetic(mean=1)).evaluate([event(start=10_000_001,end=10_500_000)])[0]
        self.assertEqual(low['reason'],'insufficient_region_read_support')

    def test_gc_bias_and_unsupported_gaps_do_not_create_control_calls(self):
        r=ReadSupport(synthetic(biased=True,mean=3)).evaluate([event()])[0]
        self.assertEqual(r['decision'],'rejected')
        rows=synthetic(changes={'2':2.2})
        rows=[b for b in rows if b['chromosome']!='2' or b['start']<10_000_000]
        r=ReadSupport(rows).evaluate([event()])[0]
        self.assertEqual(r['reason'],'poor_reference_or_genomic_coverage')

    def test_relabeling_a_supported_event_does_not_change_read_support(self):
        support=ReadSupport(synthetic(changes={'2':2.2}))
        a,b=support.evaluate([event(sample='control'),event(sample='tumor')])
        self.assertEqual(a,b)
        self.assertEqual(a['decision'],'retained')

    def test_isolated_alternating_spikes_fail_split_bin_consistency(self):
        rows=synthetic(mean=20)
        for b in rows:
            if b['chromosome']=='2':
                b['reads']=100 if ((b['start']-1)//50000)%2==0 else 18
        r=ReadSupport(rows).evaluate([event()])[0]
        self.assertEqual(r['decision'],'rejected')
        self.assertEqual(r['reason'],'inconsistent_split_bin_direction')

    def test_two_extreme_bins_cannot_create_a_control_gain(self):
        rows=synthetic(mean=1)
        targets=[b for b in rows if b['chromosome']=='2']
        targets[100]['reads']=1000;targets[101]['reads']=1000
        r=ReadSupport(rows).evaluate([event()])[0]
        self.assertEqual(r['decision'],'rejected')
        self.assertEqual(r['reason'],'localized_or_inconsistent_read_support')

    def test_diploid_to_three_copies_and_single_copy_loss_are_retained(self):
        for seed in (12,27,49):
            calls=[event(),event(chrom='7',state='loss')]
            rows=ReadSupport(synthetic(seed=seed,changes={'2':1.5,'7':.5},mean=1)).evaluate(calls)
            self.assertTrue(all(r['decision']=='retained' for r in rows),rows)

    def test_newly_admitted_focal_and_weak_calls_need_support_even_at_standard_coverage(self):
        sample = dict(sample_role='tumor', median_reads_per_bin=30, zero_read_bin_fraction=.001)
        established = dict(event(), mean_log2=.5)
        self.assertFalse(requires_read_support(established, sample))
        for call in (dict(established, end=500000), dict(established, mean_log2=.21),
                     dict(established, state='loss', mean_log2=-.21), dict(event())):
            self.assertTrue(requires_read_support(call, sample), call)
        self.assertTrue(requires_read_support(established, dict(sample, sample_role='normal')))
        self.assertTrue(requires_read_support(established, dict(sample, median_reads_per_bin=5)))

    def test_publication_preserves_raw_calls_and_excludes_rejected_calls_from_final_tables(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); raw=root/'diagnostics/unfiltered'; raw.mkdir(parents=True)
            rows=[dict(event(), molecular_piece='2(1_50000000)x3', cna_shorthand='gain(2)', caller='qdnaseq'),
                  dict(event(chrom='7',state='loss'), molecular_piece='7(1_50000000)x1', cna_shorthand='loss(7)', caller='qdnaseq')]
            with (raw/'cna_events.tsv').open('w') as f:
                w=csv.DictWriter(f,fieldnames=list(rows[0]),delimiter='\t');w.writeheader();w.writerows(rows)
            (raw/'cna_cytogenomic_notation.tsv').write_text('sample\tn_cna_events\tmolecular_cytogenomic_notation\tcna_shorthand\tcaller\ncontrol\t2\tcna[GRCh38] original\toriginal\tqdnaseq\n')
            original=(raw/'cna_events.tsv').read_bytes()
            counts=root/'counts';counts.mkdir(); source=synthetic(changes={'2':2.2})
            with gzip.open(counts/'broad_cna_counts.tsv.gz','wt') as f:
                fields=['chromosome','start','end','gc','mappability','bases','sample:control']
                w=csv.DictWriter(f,fieldnames=fields,delimiter='\t');w.writeheader()
                for row in source:
                    r=dict(row);r['sample:control']=r.pop('reads');w.writerow(r)
            assessment={'samples':[{'sample':'control','sample_role':'normal','median_reads_per_bin':1,'zero_read_bin_fraction':.3}]}
            receipt=publish_guard(raw,root,counts,'qdnaseq',assessment,resolve_policy({'cna_profile':'sensitive'}))
            self.assertEqual(receipt['samples']['control'],{'retained':1,'rejected':1})
            self.assertEqual((raw/'cna_events.tsv').read_bytes(),original)
            with (root/'cna_events.tsv').open() as handle:
                accepted=list(csv.DictReader(handle,delimiter='\t'))
            self.assertEqual([r['chrom'] for r in accepted],['2'])
            self.assertNotIn('loss(7)',(root/'cna_cytogenomic_notation.tsv').read_text())
            with (root/'cna_evidence_decisions.tsv').open() as handle:
                decisions=list(csv.DictReader(handle,delimiter='\t'))
            self.assertEqual(len(decisions),2)
            self.assertTrue(decisions[1]['reason'])
            # A resumed evaluation reads the preserved unfiltered inputs.
            again=publish_guard(raw,root,counts,'qdnaseq',assessment,resolve_policy({'cna_profile':'sensitive'}))
            self.assertEqual(again,receipt)


class ProfileTests(unittest.TestCase):
    def test_new_projects_explicitly_sensitive_old_yaml_standard(self):
        self.assertEqual(NEW_PROJECT_PROFILE,'sensitive')
        self.assertEqual(resolve_policy()['qc_policy'],'coverage-v2')
        self.assertEqual(resolve_policy({'cna_profile':'sensitive'})['thresholds']['minimum_median_reads_per_bin'],5)
        for v in ('',True,None,{},'unsupported'):
            with self.assertRaises(OncoTracerError): validate_profile(v)

    def test_cli_and_both_container_engines_preserve_profile(self):
        from oncotracer_cli.cli import parse_arguments,_run_docker,_run_podman
        for entry in (['run','--config','run.yml'],['internal-run','--config','run.yml'],['setup'],['--interactive']):
            self.assertIsNone(parse_arguments(entry).cna_profile)
            self.assertEqual(parse_arguments([*entry,'--cna-profile','sensitive']).cna_profile,'sensitive')
        for engine,run in [('docker',_run_docker),('podman',_run_podman)]:
            args=parse_arguments(['run','--config','/data/config/run.yml','--backend',engine,'--dry-run','--cna-profile','sensitive'])
            with patch('oncotracer_cli.cli._load_install_config',return_value={}),patch('oncotracer_cli.cli._project_mounts',return_value=[Path('/data')]),patch('oncotracer_cli.cli.load_flat_yaml',return_value={}),patch('oncotracer_cli.cli._run') as execute:
                run(Path('/data/config/run.yml'),args)
            command=execute.call_args.args[0]
            self.assertEqual(command[command.index('--cna-profile')+1],'sensitive')

    def test_nextflow_full_run_forwards_profile_without_changing_yaml(self):
        from oncotracer_cli.nextflow_gateway import parser, command
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'run.yml';path.write_text('mode: ont\n')
            args=parser().parse_args(['--analysis','full','--config',str(path),'--cna-profile','sensitive'])
            argv,_,_=command(args)
            self.assertEqual(argv[argv.index('--cna-profile')+1],'sensitive')
            self.assertEqual(path.read_text(),'mode: ont\n')

    def test_coverage_assessment_rejects_stale_profile_status(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); status=root/'status.json'
            status.write_text(json.dumps({'samples':[{'sample':'case','status':'complete','assessment':'assessable','qc_policy':'coverage-v2','median_reads_per_bin':30,'zero_read_bin_fraction':0}]}))
            data=publish_assessment(root,status,50,policy=resolve_policy({'cna_profile':'sensitive'}))
            self.assertEqual(data['samples'][0]['assessment'],'not_assessable')
            self.assertIn('Resume',data['samples'][0]['reason'])
