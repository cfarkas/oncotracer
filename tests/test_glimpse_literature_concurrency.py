"""Concurrent reports share a request limit and intact literature cache entries."""
from concurrent.futures import ThreadPoolExecutor
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

BIN = Path(__file__).parents[1]/'oncotracer_cli/lpwgs/bin'
if str(BIN) not in sys.path:
    sys.path.insert(0, str(BIN))
import finding_sources


class LiteratureConcurrencyTests(unittest.TestCase):
    def test_distinct_sample_queries_do_not_overlap_network_requests(self):
        active=maximum=count=0
        guard=threading.Lock()
        class Response(io.BytesIO):
            def read(self, *args):
                threading.Event().wait(.015)
                return super().read(*args)
            def __exit__(self,*args):
                nonlocal active
                with guard:active-=1
                return super().__exit__(*args)
        def retrieve(request, timeout):
            nonlocal active,maximum,count
            with guard:
                active+=1;count+=1;maximum=max(maximum,active)
            return Response(b'{"result":{}}')
        with tempfile.TemporaryDirectory() as d, patch.object(finding_sources.urllib.request,'urlopen',side_effect=retrieve), patch.object(finding_sources.time,'sleep') as cooldown:
            with ThreadPoolExecutor(max_workers=4) as pool:
                rows=list(pool.map(lambda n:finding_sources.get_public(d,'esummary.fcgi',{'db':'pubmed','id':str(n),'retmode':'json'}),range(8)))
            self.assertEqual(count,8); self.assertEqual(maximum,1)
            self.assertTrue(all(row['status']=='retrieved' for row in rows))
            self.assertEqual(cooldown.call_count,8)
            self.assertTrue(all(call.args==(.36,) for call in cooldown.call_args_list))

    def test_same_query_downloads_once_then_reuses_atomic_cache(self):
        with tempfile.TemporaryDirectory() as d, patch.object(finding_sources.urllib.request,'urlopen',return_value=io.BytesIO(b'{"result":{}}')) as retrieve, patch.object(finding_sources.time,'sleep') as cooldown:
            def get(_):return finding_sources.get_public(d,'esummary.fcgi',{'db':'pubmed','id':'100','retmode':'json'})
            with ThreadPoolExecutor(max_workers=4) as pool:rows=list(pool.map(get,range(8)))
            self.assertEqual(retrieve.call_count,1);self.assertEqual(cooldown.call_count,1)
            self.assertTrue(all(row==rows[0] for row in rows))
            files=list(Path(d).glob('*.json'));self.assertEqual(len(files),1)
            self.assertEqual(json.loads(files[0].read_text()),rows[0])
            self.assertFalse(list(Path(d).glob('*.tmp')))

    def test_failed_fetch_still_observes_cooldown(self):
        with tempfile.TemporaryDirectory() as d, patch.object(finding_sources.urllib.request,'urlopen',side_effect=OSError('offline fixture')), patch.object(finding_sources.time,'sleep') as cooldown:
            with self.assertRaises(OSError):finding_sources.fetch_public(d,object())
            cooldown.assert_called_once_with(.36)


if __name__=='__main__':unittest.main()
