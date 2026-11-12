"""AWS 읽기에서 자격/부재 오류를 정상 근거로 바꾸지 않는지 검증한다."""

from types import SimpleNamespace
import importlib.util
import unittest
from unittest.mock import Mock

from ai_ops.c_trial_aws import AWSImageReader


class MissingImage(Exception):pass


@unittest.skipUnless(importlib.util.find_spec('boto3'), '로컬 trial SDK 선택 의존성 필요')
class AWSReaderTest(unittest.TestCase):
    def setUp(self):
        self.plan=SimpleNamespace(normal_image='repo@sha256:'+'1'*64,failure_image='repo@sha256:'+'2'*64)
        self.sts=Mock()
        self.sts.get_caller_identity.return_value={'Account':'764428911194',
            'Arn':'arn:aws:iam::764428911194:user/urlshortener-operator'}
        self.eks=Mock()
        self.eks.describe_cluster.return_value={'cluster':{'arn':
            'arn:aws:eks:ap-northeast-2:764428911194:cluster/urlshortener','status':'ACTIVE'}}
        self.ecr=Mock()
        self.ecr.exceptions=SimpleNamespace(ImageNotFoundException=MissingImage)
        self.status='ACTIVE'
        self.missing=True
        def describe(**kwargs):
            if kwargs['imageIds'][0]['imageDigest'].endswith('2'*64):
                if isinstance(self.missing,Exception):raise self.missing
                if self.missing:raise MissingImage()
            return {'imageDetails':[{'imageDigest':'sha256:'+'1'*64,'imageStatus':self.status}]}
        self.ecr.describe_images.side_effect=describe
        self.session=Mock()
        self.session.client.side_effect=[self.sts,self.eks,self.ecr]
        self.reader=AWSImageReader(session=self.session)

    def test_same_clients_are_reused_and_only_fixed_read_apis_are_called(self):
        for _ in range(2):self.assertTrue(self.reader(self.plan)['failure_absent'])
        self.assertEqual(self.session.client.call_count,3)
        self.assertEqual(self.sts.get_caller_identity.call_count,2)
        self.eks.describe_cluster.assert_called_with(name='urlshortener')
        for call in self.ecr.describe_images.call_args_list:
            self.assertEqual(call.kwargs['repositoryName'],'urlshortener')
            self.assertEqual(call.kwargs['registryId'],'764428911194')

    def test_wrong_caller_is_refused_before_resource_reads(self):
        self.sts.get_caller_identity.return_value['Account']='000000000000'
        with self.assertRaises(ValueError):self.reader(self.plan)
        self.ecr.describe_images.assert_not_called()

    def test_access_denied_is_not_interpreted_as_absent_image(self):
        self.missing=PermissionError('synthetic AccessDenied')
        with self.assertRaises(PermissionError):self.reader(self.plan)

    def test_present_bad_digest_and_inactive_normal_are_refused(self):
        self.missing=False
        with self.assertRaises(ValueError):self.reader(self.plan)
        self.missing=True;self.status='ARCHIVED'
        with self.assertRaises(ValueError):self.reader(self.plan)


if __name__=='__main__':unittest.main()
