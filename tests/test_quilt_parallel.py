"""New concurrency must produce reusable chunks for the unchanged QUILT2 worker."""
from pathlib import Path
import json,os,shutil,tempfile,time,unittest
from unittest.mock import patch
import pysam
from oncotracer_cli.quilt_parallel import load_worker,prefetch,SUPPORTED_WORKER,configure_environment,identity_cached_sha
from oncotracer_cli.resource_pool import DynamicResources
from oncotracer_cli.runtime import sha256_file

class WithinRunHashTests(unittest.TestCase):
    def test_content_change_with_preserved_size_and_mtime_and_replacement_are_rehashed(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'reference';path.write_bytes(b'original');before=path.stat()
            with patch('oncotracer_cli.quilt_parallel.sha256_file',wraps=sha256_file) as digest, patch('oncotracer_cli.quilt_parallel.time.time_ns',return_value=before.st_ctime_ns+2_000_000_000):
                cached=identity_cached_sha(digest,[path])
                self.assertEqual(cached(path),sha256_file(path));self.assertEqual(cached(path),sha256_file(path))
                self.assertEqual(digest.call_count,1)
                time.sleep(.02);path.write_bytes(b'modified');os.utime(path,ns=(before.st_atime_ns,before.st_mtime_ns))
                self.assertEqual(cached(path),sha256_file(path));self.assertEqual(digest.call_count,2)
                time.sleep(.02);replacement=Path(folder)/'replacement';replacement.write_bytes(b'replaced');os.utime(replacement,ns=(before.st_atime_ns,before.st_mtime_ns));replacement.replace(path)
                self.assertEqual(cached(path),sha256_file(path));self.assertEqual(digest.call_count,3)
                self.assertEqual(cached(path),sha256_file(path));self.assertEqual(digest.call_count,3)

    def test_change_during_hashing_is_rejected_and_never_cached(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'reference';path.write_bytes(b'original')
            def changing_digest(value):
                before=Path(value).stat();result=sha256_file(value);Path(value).write_bytes(b'modified');os.utime(value,ns=(before.st_atime_ns,before.st_mtime_ns+1));return result
            cached=identity_cached_sha(changing_digest,[path])
            with patch('oncotracer_cli.quilt_parallel.time.time_ns',return_value=path.stat().st_ctime_ns+2_000_000_000):
                with self.assertRaisesRegex(ValueError,'changed while hashing'):cached(path)

    def test_recent_files_and_patient_outputs_are_always_hashed(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);reference=root/'reference';patient=root/'patient.vcf.gz'
            reference.write_bytes(b'recent reference');patient.write_bytes(b'patient output')
            with patch('oncotracer_cli.quilt_parallel.sha256_file',wraps=sha256_file) as digest:
                cached=identity_cached_sha(digest,[reference])
                now=max(reference.stat().st_ctime_ns,patient.stat().st_ctime_ns)
                with patch('oncotracer_cli.quilt_parallel.time.time_ns',return_value=now):
                    for path in [reference,reference]:self.assertEqual(cached(path),sha256_file(path))
                with patch('oncotracer_cli.quilt_parallel.time.time_ns',return_value=now+2_000_000_000):
                    for path in [patient,patient]:self.assertEqual(cached(path),sha256_file(path))
                self.assertEqual(digest.call_count,4)

    def test_retargeted_symlink_gets_the_new_digest(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);first=root/'first';second=root/'second';link=root/'link'
            first.write_bytes(b'one');second.write_bytes(b'two');link.symlink_to(first)
            cached=identity_cached_sha(sha256_file,[first])
            with patch('oncotracer_cli.quilt_parallel.time.time_ns',return_value=first.stat().st_ctime_ns+2_000_000_000):
                before=cached(link)
                link.unlink();link.symlink_to(second)
                self.assertNotEqual(cached(link),before);self.assertEqual(cached(link),sha256_file(second))


@unittest.skipUnless(shutil.which('bcftools'),'bcftools required')
class ParallelQuiltTests(unittest.TestCase):
    def test_child_uses_explicit_verified_payload_when_argv_is_not_the_zipapp(self):
        root=Path(__file__).resolve().parents[1]
        with patch('oncotracer_cli.quilt_parallel.runtime_root',side_effect=AssertionError('Do not infer child argv[0]')):
            worker,path=load_worker(root)
        self.assertEqual(worker.sha(path),SUPPORTED_WORKER)

    def test_dynamic_entry_uses_the_original_isolated_r_environment(self):
        with patch.dict(os.environ, {'R_HOME':'/wrong', 'R_LIBS_USER':'/wrong',
                                    'PYTHONPATH':'/wrong', 'OMP_NUM_THREADS':'64'}):
            configure_environment({'tools': {'Rscript': '/pinned/variants/bin/Rscript'}})
            self.assertNotIn('R_HOME', os.environ)
            self.assertNotIn('R_LIBS_USER', os.environ)
            self.assertNotIn('PYTHONPATH', os.environ)
            self.assertEqual(os.environ['PATH'], '/pinned/variants/bin'+os.pathsep+os.defpath)
            self.assertEqual(os.environ['CONDA_PREFIX'], '/pinned/variants')
            self.assertEqual(os.environ['OMP_NUM_THREADS'], '1')

    def test_parallel_chunks_are_reused_by_original_worker_and_corruption_is_repaired(self):
        worker,path=load_worker();self.assertEqual(sha256_file(path),SUPPORTED_WORKER)
        with tempfile.TemporaryDirectory(prefix='quilt dynamic ') as folder:
            root=Path(folder);bam=root/'S1.bam'
            with pysam.AlignmentFile(str(bam),'wb',header={'HD':{'VN':'1.6','SO':'coordinate'},'SQ':[{'SN':'chr1','LN':1000}],'RG':[{'ID':'rg','SM':'S1'}]}) as handle:
                read=pysam.AlignedSegment();read.query_name='synthetic';read.query_sequence='A'*30;read.reference_id=0;read.reference_start=10;read.mapping_quality=60;read.cigarstring='30M';read.query_qualities=pysam.qualitystring_to_array('I'*30);read.set_tag('RG','rg');handle.write(read)
            pysam.index(str(bam))
            reference=root/'synthetic.RData';reference.write_bytes(b'fixture only; imputation is stubbed')
            chunks=[dict(chr='chr1',regionStart=start,regionEnd=start+399,buffer=20,nGen=100,prepared_reference=str(reference),sha256=worker.sha(reference)) for start in (1,501)]
            worker.sha=identity_cached_sha(worker.sha,[reference])
            manifest=root/'panel.json';manifest.write_text(json.dumps(dict(schema=worker.SCHEMA,version=worker.VERSION,chunks=chunks,contig_lengths={'chr1':1000})))
            config=dict(sample='S1',bam=str(bam),output=str(root/'output'),manifest=str(manifest),expected_chromosomes=['chr1'],threads=1,min_base_quality=20,tools={'bcftools':shutil.which('bcftools')},force=False)
            counts=[]
            def synthetic(tools,chunk,bamlist,names,temp,threads,bq):
                self.assertEqual(threads,1);counts.append(chunk['regionStart']);p=Path(temp)/'imputed.vcf'
                p.write_text('##fileformat=VCFv4.2\n##contig=<ID=chr1,length=1000>\n'
                    '##FORMAT=<ID=GT,Number=1,Type=String,Description="GT">\n'
                    '##FORMAT=<ID=GP,Number=G,Type=Float,Description="GP">\n'
                    '##FORMAT=<ID=DS,Number=1,Type=Float,Description="DS">\n'
                    '#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1\n'
                    f'chr1\t{chunk["regionStart"]+20}\t.\tA\tG\t.\tPASS\t.\tGT:GP:DS\t0|1:0.1,0.8,0.1:1\n')
                output=Path(temp)/'imputed.vcf.gz';pysam.tabix_compress(str(p),str(output));return output
            a=dict(total_threads=2,parallel_samples=1,total_memory_gb=32,threads_per_sample=1)
            pool=DynamicResources(root/'controls',['S1'],a,hardware=lambda:dict(cpu_workers_available=2,ram_available_bytes=64*1024**3)).start()
            try:
                pool.update('S1','running')
                with patch.object(worker,'run_imputation_chunk',side_effect=synthetic):prefetch(config,pool.sample_directory('S1'),2,worker)
                self.assertEqual(sorted(counts),[1,501])
                receipts=list((root/'output/chunks').glob('*/*.complete.json'));self.assertEqual(len(receipts),2)
                original={p:worker.sha(p) for p in (root/'output/chunks').glob('*/*.vcf.gz')}
                with patch.object(worker,'run_imputation_chunk',side_effect=AssertionError('Verified chunk recomputed')):
                    worker.impute(config)
                summary=json.loads((root/'output/S1.quilt2.summary.json').read_text());self.assertEqual(summary['reused_chunks'],2);self.assertEqual(summary['computed_chunks'],0)
                for p,digest in original.items():self.assertEqual(worker.sha(p),digest)
                index=next((root/'output/chunks').glob('*/*.tbi'));index.write_bytes(b'corrupt index')
                counts.clear()
                with patch.object(worker,'run_imputation_chunk',side_effect=synthetic):prefetch(config,pool.sample_directory('S1'),2,worker)
                self.assertEqual(len(counts),1)
                self.assertFalse(list((root/'output/chunks').glob('*/.impute-*')))
                self.assertEqual(reference.read_bytes(),b'fixture only; imputation is stubbed')
            finally:pool.close()

if __name__=='__main__':unittest.main()
