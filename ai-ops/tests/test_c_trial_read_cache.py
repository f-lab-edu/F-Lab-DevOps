"""캐시가 리뷰/상태를 동결하지 않고 ZIP을 새 digest로 재검증하는지 확인한다."""

from copy import deepcopy
import json
import unittest
from unittest.mock import Mock

from ai_ops.c_trial_read_cache import ArchiveBytesReader, ImmutableGitReader
from ai_ops.normal_ci_history import verify_normal_ci_history
from ai_ops.ci_history import PREFIX
from test_normal_ci import fixture
from test_normal_ci_handoff import Reader


class ReadCacheTest(unittest.TestCase):
    def test_only_full_sha_git_objects_are_cached_and_copies_are_independent(self):
        source=Mock();source.get.return_value={'sha':'a'*40,'tree':{'sha':'b'*40}}
        reader=ImmutableGitReader(source);path='repos/joungGo/c03-execution-lab/git/commits/'+'a'*40
        reader.get(path)['tree']['sha']='changed'
        self.assertEqual(reader.get(path)['tree']['sha'],'b'*40)
        self.assertEqual(source.get.call_count,1)
        for dynamic in ['repos/joungGo/c03-execution-lab/pulls/8/reviews?per_page=100',
                        'repos/joungGo/c03-execution-lab/git/ref/heads/main']:
            reader.get(dynamic);reader.get(dynamic)
        self.assertEqual(source.get.call_count,5)

    def test_wrong_object_identity_is_never_cached(self):
        source=Mock();source.get.return_value={'sha':'b'*40};reader=ImmutableGitReader(source)
        with self.assertRaises(ValueError):reader.get('repos/joungGo/c03-execution-lab/git/blobs/'+'a'*40)
        self.assertEqual(reader.cache,{})

    def test_zip_reuse_still_reads_current_run_listing_expiration_and_digest(self):
        _,_,_,binding,_,receipt=fixture();initial=Reader(receipt)
        run=initial.get(PREFIX+'/runs/12345');listing=json.loads(initial.get(PREFIX+'/runs/12345/artifacts?per_page=100'))
        raw=initial.get(PREFIX+'/artifacts/88/zip')
        import hashlib
        listing['artifacts'][0]['digest']='sha256:'+hashlib.sha256(raw).hexdigest()
        calls=[]
        class Source:
            def get(self,path,**kwargs):
                calls.append(path)
                return raw if path.endswith('/zip') else json.dumps(listing).encode() if '/artifacts?'in path else run
        reader=ArchiveBytesReader(Source())
        for _ in range(2):verify_normal_ci_history(12345,'a'*40,binding,reader=reader)
        self.assertEqual(sum(path.endswith('/zip')for path in calls),1)
        self.assertEqual(sum('/artifacts?'in path for path in calls),2)
        listing['artifacts'][0]['digest']='sha256:'+'0'*64
        with self.assertRaises(ValueError):verify_normal_ci_history(12345,'a'*40,binding,reader=reader)
        listing['artifacts'][0]['expired']=True
        with self.assertRaises(ValueError):verify_normal_ci_history(12345,'a'*40,binding,reader=reader)


if __name__=='__main__':unittest.main()
