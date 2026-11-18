from contextlib import redirect_stderr
from io import StringIO
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
import unittest

from ai_ops.c_trial_lease_client import _StageCollector, TrialLeaseClient, LeaseRequestError
from test_c_trial_lease_diagnostics import peer


def event(request='a'*32, seq=1, phase='dns', state='start', **extra):
    return dict(version=1, request_id=request, sequence=seq, phase=phase, state=state,
                elapsed_us=100, dropped=0, **extra)


def feed(collector, value):
    collector.accept(collector.PREFIX+json.dumps(value).encode())


class StageCollectorTests(unittest.TestCase):
    def test_only_strict_allowlisted_fields_survive(self):
        c=_StageCollector();c.begin('a'*32)
        bad=[event(secret='DO_NOT_RECORD'),event(phase='DO_NOT_RECORD'),event(state='DO_NOT_RECORD'),
             event(request='DO_NOT_RECORD'),{**event(),'elapsed_us':float('nan')},
             {**event(),'sequence':True},event(reused='DO_NOT_RECORD'),[],None]
        for v in bad:feed(c,v)
        c.accept(b'raw-token-DO_NOT_RECORD');c.accept(c.PREFIX+b'{"token":"DO_NOT_RECORD"}')
        self.assertEqual(c.snapshot()['events'],[])
        feed(c,event());self.assertEqual(len(c.snapshot()['events']),1)
        self.assertNotIn('DO_NOT_RECORD',json.dumps(c.snapshot()))

    def test_late_and_cross_request_events_are_not_attributed(self):
        c=_StageCollector();c.begin('a'*32);feed(c,event());c.end();c.begin('b'*32)
        feed(c,event());self.assertIsNone(c.snapshot()['last_observed'])
        feed(c,event(request='b'*32,phase='tls'));self.assertEqual(c.snapshot()['last_observed']['phase'],'tls')

    def test_memory_is_bounded_and_missing_events_are_explicit(self):
        c=_StageCollector();c.begin('a'*32)
        for n in range(1,81):feed(c,event(seq=n))
        s=c.snapshot();self.assertEqual(len(s['events']),32);self.assertTrue(s['loss_observed']);self.assertFalse(s['delivery_complete'])
        c.begin('a'*32);feed(c,event(seq=2));self.assertTrue(c.snapshot()['loss_observed'])
        c.begin('a'*32);feed(c,{**event(),'dropped':1});self.assertTrue(c.snapshot()['loss_observed'])

    def test_pending_stage_does_not_require_completion(self):
        for phase in ('authentication','dns','tcp','tls','response_wait'):
            c=_StageCollector();c.begin('a'*32);feed(c,event(phase=phase))
            self.assertEqual(c.snapshot()['last_observed'],{'phase':phase,'state':'start'})

    def test_stream_drains_overlong_raw_lines_without_recording_them(self):
        read,write=os.pipe();stream=os.fdopen(read,'rb',buffering=0);c=_StageCollector(stream);c.begin('a'*32)
        try:
            os.write(write,b'RAW-SECRET'*200+b'\n')
            os.write(write,c.PREFIX+json.dumps(event()).encode()+b'\n')
            end=time.monotonic()+1
            while not c.snapshot()['events'] and time.monotonic()<end:time.sleep(.005)
            self.assertEqual(len(c.snapshot()['events']),1)
            self.assertNotIn('RAW-SECRET',json.dumps(c.snapshot()))
        finally:
            c.close();os.close(write);c.thread.join(timeout=1)
        self.assertFalse(c.thread.is_alive());self.assertTrue(stream.closed)

    def test_real_timeout_contains_pending_stage_and_has_no_retry(self):
        collector=_StageCollector()
        def reply(sock):
            # Model the native callback arriving before the response stalls.
            feed(collector,event(request=collector.active,phase='tls'))
            time.sleep(3.15)
        with peer(reply) as (client,requests):
            client.stage_collector=collector
            output=StringIO()
            with redirect_stderr(output),self.assertRaises(LeaseRequestError) as caught:client.get()
            value=caught.exception.diagnostic
            self.assertEqual(value['timeout_seconds'],3)
            self.assertEqual(value['upstream']['last_observed'],{'phase':'tls','state':'start'})
            self.assertEqual(len(value['request_id']),32);self.assertEqual(len(requests),1)
            self.assertLess(value['elapsed_seconds'],4)
            self.assertIn(b'X-C04-Lease-Trace: 1',requests[0])

    def test_binary_pin_is_required_and_symlink_or_writable_binary_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            binary=Path(d)/'kubectl';binary.write_bytes(b'fixture');binary.chmod(0o700)
            pin=hashlib.sha256(binary.read_bytes()).hexdigest()
            c=TrialLeaseClient('unused','uid',trace_binary=binary,trace_sha256=pin)
            self.assertEqual(c._proxy_command(),str(binary))
            binary.chmod(0o722)
            with self.assertRaisesRegex(ValueError,'rejected'):c._proxy_command()
            binary.chmod(0o700);c.trace_sha256='0'*64
            with self.assertRaisesRegex(ValueError,'rejected'):c._proxy_command()
            c.trace_sha256=None
            with self.assertRaisesRegex(ValueError,'pin_required'):c._proxy_command()
            link=Path(d)/'link';link.symlink_to(binary);c.trace_binary=link;c.trace_sha256=pin
            with self.assertRaisesRegex(ValueError,'rejected'):c._proxy_command()

    def test_writable_parent_and_hardlinked_binary_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            parent = Path(folder) / 'shared'
            parent.mkdir(mode=0o700)
            binary = parent / 'kubectl'
            binary.write_bytes(b'fixture')
            binary.chmod(0o700)
            pin = hashlib.sha256(binary.read_bytes()).hexdigest()
            client = TrialLeaseClient('unused', 'uid', trace_binary=binary, trace_sha256=pin)
            parent.chmod(0o777)
            with self.assertRaisesRegex(ValueError, 'lease_trace_binary_rejected'):
                client._proxy_command()
            parent.chmod(0o700)
            os.link(binary, parent/'alias')
            with self.assertRaisesRegex(ValueError, 'lease_trace_binary_rejected'):
                client._proxy_command()

    def test_missing_binary_reports_only_fixed_error_code(self):
        client = TrialLeaseClient('unused', 'uid', trace_binary='/nonexistent/PRIVATE-CANARY', trace_sha256='a'*64)
        with self.assertRaisesRegex(ValueError, '^lease_trace_binary_rejected$'):
            client._proxy_command()


if __name__=='__main__':unittest.main()
