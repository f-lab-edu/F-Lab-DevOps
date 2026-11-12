"""실제 로컬 소켓과 가짜 호스트 도구 응답으로 전달·마감·원자적 게시를 검증한다."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import select
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from ai_ops.c_trial_notice import deliver_review_notice, require_bridge, serve_bridge
from ai_ops.c_trial_review_window import require_review_notice
from ai_ops.contracts import sha256_json
import test_c_trial_review_window as fixtures


class NoticeDeliveryTest(unittest.TestCase):
    def setUp(self):
        fixture = fixtures.ReviewWindowTest(); fixture.setUp()
        self.plan, self.head = fixture.plan, fixture.head
        self.now = self.plan.planned_at + timedelta(seconds=70)
        self.tmp = tempfile.TemporaryDirectory(prefix='c4n-', dir=Path('/tmp').resolve())
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); self.root.chmod(0o700)
        self.socket = self.root/'notice.sock'; self.output = self.root/'review-notified.json'
        self.ready = {'trial_id':self.plan.trial_id,'pull_number':8,'head_sha':self.head,
                      'url':'https://github.com/joungGo/c03-execution-lab/pull/8',
                      'review_deadline_utc':self.plan.review_cutoff().isoformat()}
        self.questions = []; self.errors = []

    def clock(self):
        return self.now

    def start(self, host=None, *, rehearsal=False, resolve_plan=None):
        def accepted(question):
            self.questions.append(question)
            return {'delivery_id':'synthetic-local-tool-response-1'}
        def run():
            try: serve_bridge(None if resolve_plan else self.plan, self.socket, host or accepted,
                              clock=self.clock, rehearsal=rehearsal, resolve_plan=resolve_plan,
                              standby_until=self.now+timedelta(minutes=15) if resolve_plan else None)
            except Exception as exc: self.errors.append(exc)
        thread = threading.Thread(target=run)
        thread.start()
        def stop():
            self.now = self.plan.planned_at+timedelta(hours=1)
            thread.join(3)
            self.assertFalse(thread.is_alive())
        self.addCleanup(stop)
        for _ in range(100):
            try:
                if resolve_plan:
                    if self.socket.exists(): break
                    raise OSError('local bridge not yet bound')
                elif rehearsal:
                    from ai_ops.c_trial_notice import _exchange
                    response = _exchange(self.socket, {'action':'ping',
                        'trial_id':self.plan.trial_id,'plan_hash':self.plan.digest()}, 2)
                    self.assertIs(response.get('rehearsal'), True)
                else:
                    require_bridge(self.plan,self.socket)
                break
            except (OSError,ValueError):
                pass
            time.sleep(.005)
        else: self.fail(f'bridge did not bind: {self.errors!r}')

    def test_ready_bridge_cannot_deliver_before_activation_then_uses_frozen_deadlines(self):
        active = [None]
        self.start(resolve_plan=lambda: active[0])
        with self.assertRaises(ValueError): require_bridge(self.plan, self.socket)
        self.assertEqual(self.questions, []); self.assertFalse(self.output.exists())
        active[0] = self.plan
        for _ in range(100):
            try: require_bridge(self.plan, self.socket); break
            except ValueError: time.sleep(.005)
        else: self.fail('ready bridge did not activate')
        receipt = self.deliver()
        self.assertEqual(receipt['review_deadline'], self.plan.review_cutoff().isoformat())
        self.assertEqual(receipt['plan_hash'], self.plan.digest())

    def deliver(self):
        return deliver_review_notice(self.plan,self.ready,self.socket,self.output,clock=self.clock)

    def test_rehearsal_delivers_a_real_host_prompt_without_requesting_pr_approval(self):
        from ai_ops.c_trial_notice import _exchange, _request
        self.start(rehearsal=True)
        with self.assertRaises(ValueError):
            require_bridge(self.plan,self.socket)
        request = _request(self.plan,8,self.head,'a'*32)
        request['action'] = 'rehearse'
        response = _exchange(self.socket,request,2)
        self.assertIs(response['rehearsal'],True)
        self.assertEqual(response['request_hash'],sha256_json(request))
        question = self.questions[0]['questions'][0]
        self.assertIn('리허설',question['title'])
        self.assertNotIn('Approve',question['title'])
        self.assertEqual(question['options'],['수신 확인'])
        self.assertFalse(self.output.exists())
        self.assertFalse(self.errors)

    def test_live_delivery_cannot_use_a_rehearsal_bridge_or_publish_a_receipt(self):
        self.start(rehearsal=True)
        with self.assertRaises(ValueError):
            self.deliver()
        self.assertFalse(self.output.exists())
        self.assertEqual(self.questions,[])

    def test_accepted_tool_response_is_preserved_without_inventing_a_delivery_id(self):
        self.start(lambda question: {'delivery_tool_response': {'accepted': True}})
        receipt = self.deliver()
        self.assertEqual(receipt['delivery_tool_response'],{'accepted':True})
        self.assertNotIn('delivery_id',receipt)
        require_review_notice(self.plan,receipt,pull_number=8,head=self.head,now=self.now)

    def test_failure_missing_or_ambiguous_tool_ack_cannot_publish_a_receipt(self):
        from ai_ops.c_trial_notice import delivery_evidence
        for value in ({}, {'accepted':True},
                      {'delivery_tool_response':{'accepted':False}},
                      {'delivery_tool_response':{'accepted':1}},
                      {'delivery_tool_response':{'accepted':True,'error':'failure'}},
                      {'delivery_tool_response':{'accepted':True},'delivery_id':'unbound'}):
            with self.subTest(value=value),self.assertRaises(ValueError):
                delivery_evidence(value)

    def test_socket_delivery_binds_exact_head_then_publishes_complete_private_receipt(self):
        self.start()
        require_bridge(self.plan,self.socket)
        self.assertEqual(self.questions,[])
        self.assertFalse(self.output.exists())
        receipt = self.deliver()
        self.assertEqual(json.loads(self.output.read_text()),receipt)
        self.assertEqual(self.output.stat().st_mode & 0o777,0o600)
        self.assertEqual(list(self.root.glob('.review-notice-*')),[])
        question = self.questions[0]['questions'][0]['title']
        self.assertIn(self.head,question)
        self.assertIn('/pull/8/files',question)
        self.assertIn('KST',question)
        require_review_notice(self.plan,receipt,pull_number=8,head=self.head,now=self.now)
        self.assertNotIn('approved_at',receipt)
        self.assertFalse(self.errors)

    def test_host_failure_or_missing_success_response_never_creates_receipt(self):
        def fail(question): raise OSError('synthetic delivery failure')
        self.start(fail)
        with self.assertRaises((ValueError,OSError)): self.deliver()
        self.assertFalse(self.output.exists())

    def test_host_response_after_notice_cutoff_is_not_backdated(self):
        def late(question):
            self.now = self.plan.planned_at + timedelta(seconds=91)
            return {'delivery_id':'synthetic-late-ack'}
        self.start(late)
        with self.assertRaisesRegex(ValueError,'insufficient_actual_review_notice_window'): self.deliver()
        self.assertFalse(self.output.exists())

    def test_missing_bridge_blocks_readiness(self):
        with self.assertRaises(OSError): require_bridge(self.plan,self.socket)
        self.assertFalse(self.output.exists())

    def test_other_trial_or_unsafe_socket_never_reaches_host(self):
        self.start()
        os.chmod(self.socket,0o666)
        with self.assertRaises(ValueError): self.deliver()
        self.assertEqual(self.questions,[])

    def test_stale_ready_binding_or_foreign_url_is_rejected_before_exchange(self):
        for change in ({'trial_id':0},{'url':'https://example.com/pull/8'},
                       {'head_sha':'x'*40},{'pull_number':True}):
            with self.subTest(change=change),patch('ai_ops.c_trial_notice._exchange') as exchange:
                with self.assertRaises(ValueError):
                    deliver_review_notice(self.plan,{**self.ready,**change},self.socket,self.output,clock=self.clock)
                exchange.assert_not_called()

    def response(self, path, request, timeout):
        return {'request_hash':sha256_json(request),'delivery_id':'synthetic-test-ack',
                'acknowledged_at':self.now.isoformat()}

    def test_wrong_response_or_future_timestamp_does_not_create_receipt(self):
        for change in ({'request_hash':'0'*64},{'delivery_id':''},
                       {'acknowledged_at':(self.now+timedelta(seconds=1)).isoformat()}):
            def altered(*args): return {**self.response(*args),**change}
            with self.subTest(change=change),patch('ai_ops.c_trial_notice._exchange',side_effect=altered):
                with self.assertRaises(ValueError): self.deliver()
                self.assertFalse(self.output.exists())

    def test_disk_delay_past_notice_cutoff_removes_temporary_receipt(self):
        moments = iter([self.now,self.now,self.plan.planned_at+timedelta(seconds=91)])
        with patch('ai_ops.c_trial_notice._exchange',side_effect=self.response):
            with self.assertRaisesRegex(ValueError,'insufficient_actual_review_notice_window'):
                deliver_review_notice(self.plan,self.ready,self.socket,self.output,clock=lambda:next(moments))
        self.assertFalse(self.output.exists())
        self.assertEqual(list(self.root.glob('.review-notice-*')),[])

    def test_concurrent_receipts_never_overwrite_one_another(self):
        barrier = threading.Barrier(2)
        def simultaneous(*args):
            barrier.wait(timeout=3)
            return self.response(*args)
        def attempt(_):
            try: return self.deliver()
            except FileExistsError: return None
        with patch('ai_ops.c_trial_notice._exchange',side_effect=simultaneous):
            with ThreadPoolExecutor(max_workers=2) as pool: results=list(pool.map(attempt,range(2)))
        winners=[value for value in results if value is not None]
        self.assertEqual(len(winners),1)
        self.assertEqual(json.loads(self.output.read_text()),winners[0])
        self.assertEqual(list(self.root.glob('.review-notice-*')),[])

    def test_existing_or_linked_receipt_cannot_be_replaced(self):
        self.output.write_text('previous-evidence')
        with self.assertRaises(ValueError): self.deliver()
        self.assertEqual(self.output.read_text(),'previous-evidence')
        self.output.unlink(); self.output.symlink_to(self.root/'absent')
        with self.assertRaises(ValueError): self.deliver()
        self.assertFalse((self.root/'absent').exists())

    def test_manual_timestamp_only_notice_is_not_a_delivery_ack(self):
        fixture=fixtures.ReviewWindowTest(); fixture.setUp()
        receipt={key:value for key,value in fixture.notice.items() if not key.startswith('delivery_')}
        with self.assertRaises(KeyError):
            require_review_notice(self.plan,receipt,pull_number=8,head=self.head,now=self.now)

    def test_cli_waits_for_host_ack_before_actual_client_receipt(self):
        start=datetime.now(timezone.utc)-timedelta(seconds=5)
        self.plan=self.plan.model_copy(update={'planned_at':start,
            'approval_cutoff':start-timedelta(minutes=5),
            'review_deadline':start+timedelta(seconds=210),
            'manual_recovery_at':start+timedelta(seconds=240),
            'rollback_deadline':start+timedelta(seconds=300)})
        self.ready['review_deadline_utc']=self.plan.review_cutoff().isoformat()
        plan_file=self.root/'plan.json';plan_file.write_text(self.plan.model_dump_json());plan_file.chmod(0o600)
        script=Path(__file__).resolve().parents[1]/'scripts/bridge_c4_review_notice.py'
        env={**os.environ,'AI_OPS_FILES_ROOT':str(self.root)}
        child=subprocess.Popen([sys.executable,str(script),'--plan',str(plan_file),'--socket',str(self.socket)],
                               env=env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        try:
            for _ in range(200):
                if self.socket.exists():break
                if child.poll() is not None:self.fail(child.stderr.read())
                time.sleep(.005)
            require_bridge(self.plan,self.socket)
            with ThreadPoolExecutor(max_workers=1) as pool:
                future=pool.submit(deliver_review_notice,self.plan,self.ready,self.socket,self.output)
                self.assertTrue(select.select([child.stdout],[],[],5)[0])
                question=json.loads(child.stdout.readline())
                self.assertEqual(question['tool'],'functions.request_user_input_async')
                self.assertFalse(self.output.exists())
                child.stdin.write('{"delivery_id":"synthetic-subprocess-tool-ack"}\n');child.stdin.flush()
                receipt=future.result(timeout=5)
            self.assertEqual(child.wait(timeout=5),0)
            self.assertEqual(receipt['delivery_id'],'synthetic-subprocess-tool-ack')
            require_review_notice(self.plan,receipt,pull_number=8,head=self.head,now=datetime.now(timezone.utc))
        finally:
            if child.poll() is None:child.kill();child.wait(timeout=5)
            for stream in (child.stdin,child.stdout,child.stderr):stream.close()

    def test_supervisor_missing_bridge_stops_before_credentials_or_children(self):
        script=Path(__file__).resolve().parents[1]/'scripts/supervise_c4_trial.py'
        spec=importlib.util.spec_from_file_location('c04_notice_preflight_test',script)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        args=SimpleNamespace(plan=self.root/'plan.json',authorization=self.root/'auth.json',
            runtime_root=self.root,guard_source_root=self.root,run_dir=self.root/'live',
            review_notifier_socket=str(self.socket))
        authorization={'trial_id':self.plan.trial_id,'plan_hash':self.plan.digest(),
            'actor_github_id':149856960,'approved_at':self.plan.approval_cutoff.isoformat(),
            'fault_injection_approved':True}
        with patch.object(module,'parse_args',return_value=args),\
             patch.object(module,'private_json',side_effect=[self.plan.model_dump(mode='json'),authorization]),\
             patch.object(module,'verify_runtime_bundle'),\
             patch('ai_ops.c_trial_notice.require_bridge',side_effect=FileNotFoundError),\
             patch.object(module.subprocess,'run') as run,patch.object(module.subprocess,'Popen') as popen:
            with self.assertRaises(FileNotFoundError):module.main()
            run.assert_not_called();popen.assert_not_called()
        self.assertFalse(args.run_dir.exists())


if __name__ == '__main__': unittest.main()
