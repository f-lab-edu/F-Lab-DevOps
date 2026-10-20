"""경보 식별·원자 접수·중복·해제 순서와 기존 큐 연결을 검증한다."""

import copy
import json
import sqlite3
import threading
import unittest
from unittest.mock import patch

from ai_ops.alerts import AlertInbox, alert_time, show_alert
from ai_ops.job_queue import JobQueue, QueueFull, RETENTION_SECONDS
from ai_ops.retention import QueueRetention
import test_job_queue as queue_tests


class AlertInboxTest(unittest.TestCase):
    def setUp(self):
        self.fixture = queue_tests.JobQueueTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.queue = self.fixture.queue
        self.settings = self.fixture.settings
        self.inbox = AlertInbox(self.queue, self.settings, cluster_id=self.fixture.target.target.cluster_id)

    def alert(self, **changes):
        labels = {**self.fixture.target.target.model_dump(), 'ai_ops':'enabled','alertname':'CacheHitRateLow','severity':'warning'}
        value = {'status':'firing','labels':labels,'annotations':{'summary':'token=untrusted-secret'},
                 'startsAt':'1970-01-01T00:10:00.123456789Z','endsAt':'0001-01-01T00:00:00Z',
                 'fingerprint':'0123456789abcdef','generatorURL':'https://untrusted.invalid/secret'}
        value.update(changes)
        return value

    def body(self, *alerts, **changes):
        value = {'version':'4','receiver':'ai-investigation','status':'firing','truncatedAlerts':0,
                 'alerts':list(alerts or [self.alert()]),'commonLabels':{'namespace':'untrusted'},
                 'externalURL':'http://127.0.0.1/private'}
        value.update(changes)
        return json.dumps(value).encode()

    def rows(self, query):
        with self.queue.transaction() as db:
            return [dict(row) for row in db.execute(query)]

    def test_nanosecond_offsets_and_invalid_times(self):
        self.assertEqual(alert_time('1970-01-01T09:10:00.123456789+09:00')[0],alert_time('1970-01-01T00:10:00.123456789Z')[0])
        self.assertNotEqual(alert_time('1970-01-01T00:10:00.123456788Z')[0],alert_time('1970-01-01T00:10:00.123456789Z')[0])
        for value in ('2026-01-01T00:00:00', '2026-01-01T00:00:00+09:99', '1970-01-01T00:00:60Z', 'not-time'):
            with self.assertRaises(ValueError):alert_time(value)

    def test_accept_commit_duplicate_and_rules_worker_without_original_body(self):
        body = self.body()
        self.assertEqual(self.inbox.accept(body),{'accepted':1,'duplicate':0,'resolved':0,'suppressed':0,'rejected':0})
        restarted = AlertInbox(JobQueue(self.queue.root,clock=self.queue.clock),self.settings,cluster_id=self.inbox.cluster_id)
        self.assertEqual(restarted.accept(body)['duplicate'],1)
        result = self.fixture.worker().run_once()
        self.assertEqual(result['state'],'partial')
        self.assertEqual(result['attempts'],1)
        stored = str(self.rows('SELECT * FROM jobs')+self.rows('SELECT * FROM alert_episodes'))
        for value in ('untrusted-secret','untrusted.invalid','externalURL','annotations','generatorURL'):
            self.assertNotIn(value,stored)
        self.assertEqual(self.queue.engine.show(result['incident_id'],result['run_id']).report.execution.mode,'rules')

    def test_recurrence_with_new_start_creates_new_job(self):
        self.inbox.accept(self.body())
        self.inbox.accept(self.body(self.alert(status='resolved',endsAt='1970-01-01T00:15:00Z')))
        self.assertEqual(self.inbox.accept(self.body())['suppressed'],1)
        result=self.inbox.accept(self.body(self.alert(startsAt='1970-01-01T00:11:00Z')))
        self.assertEqual(result['accepted'],1)
        self.assertEqual(len(self.rows('SELECT * FROM jobs')),2)

    def test_resolved_first_and_mixed_group_do_not_create_job(self):
        resolved=self.alert(status='resolved',endsAt='1970-01-01T00:15:00Z')
        result=self.inbox.accept(self.body(self.alert(),resolved))
        self.assertEqual((result['resolved'],result['suppressed']),(1,1))
        self.assertEqual(self.rows('SELECT * FROM jobs'),[])
        self.assertEqual(self.inbox.accept(self.body())['suppressed'],1)

    def test_resolved_changes_episode_only_not_job_report_or_attempt(self):
        self.inbox.accept(self.body())
        result=self.fixture.worker().run_once()
        before=self.queue.show(result['job_id'])
        record=self.queue.engine.show(result['incident_id'],result['run_id'])
        self.inbox.accept(self.body(self.alert(status='resolved',endsAt='1970-01-01T00:15:00Z')))
        self.assertEqual(self.queue.show(result['job_id']),before)
        self.assertEqual(self.queue.engine.show(result['incident_id'],result['run_id']).markdown,record.markdown)
        self.assertIsNone(self.fixture.worker().run_once())

    def test_mixed_group_uses_each_alert_status_not_group_status(self):
        other=self.alert(status='resolved',endsAt='1970-01-01T00:15:00Z',fingerprint='f'*16)
        result=self.inbox.accept(self.body(other,self.alert(),status='resolved'))
        self.assertEqual((result['resolved'],result['accepted']),(1,1))

    def test_all_targets_required_and_watchdog_unknown_dimensions_rejected(self):
        changes=[{'alertname':'Watchdog'},{'workload':'foreign-app'},{'namespace':'other'},
                 {'cluster_id':'other'},{'environment':'production'},{'service':'other'},
                 {'ai_ops':'disabled'},{'route':'/arbitrary'},{'operation':'drop_table'}]
        for change in changes:
            value=self.alert();value['labels'].update(change)
            self.assertEqual(self.inbox.accept(self.body(value))['rejected'],1)
        value=self.alert();del value['labels']['namespace']
        self.assertEqual(self.inbox.accept(self.body(value))['rejected'],1)
        self.assertEqual(self.rows('SELECT * FROM jobs'),[])

    def test_truncated_oversized_duplicate_json_bad_time_and_fingerprint_rejected(self):
        invalid=[self.body(truncatedAlerts=1),self.body(receiver='slack'),self.body(alerts=[]),
                 self.body(self.alert(startsAt='1970-01-01T01:00:00Z')),
                 self.body(self.alert(status='resolved',endsAt='1970-01-01T00:09:59Z')),
                 self.body(self.alert(status='resolved',endsAt='1970-01-01T00:10:00.123456788Z')),
                 self.body(self.alert(fingerprint='wrong')),b'{}'*65536,
                 self.body().replace(b'"version": "4"',b'"version":"4","version":"4"')]
        for raw in invalid:
            with self.assertRaises(ValueError):self.inbox.accept(raw)
        self.assertEqual(self.rows('SELECT * FROM jobs'),[])

    def test_invalid_group_rolls_back_and_capacity_failure_retries_atomically(self):
        invalid=self.alert(fingerprint='f'*16);invalid['labels']['service']='foreign'
        selected=self.inbox.accept(self.body(self.alert(),invalid))
        self.assertEqual((selected['accepted'],selected['rejected']),(1,1))
        self.queue.cancel(self.rows('SELECT * FROM jobs')[0]['job_id'])
        for index in range(99):self.fixture.submit('capacity-'+str(index))
        raw=self.body(self.alert(fingerprint='a'*16),self.alert(fingerprint='f'*16))
        with self.assertRaises(QueueFull):self.inbox.accept(raw)
        self.assertEqual(len(self.rows('SELECT * FROM alert_episodes')),1)
        self.assertEqual(len(self.rows('SELECT * FROM jobs')),100)
        self.queue.cancel(next(row['job_id'] for row in self.rows('SELECT * FROM jobs') if row['state']=='queued'))
        self.assertEqual(self.inbox.accept(raw)['accepted'],2)

    def test_identity_conflict_and_storage_commit_failure_not_acknowledged(self):
        self.inbox.accept(self.body())
        value=self.alert();value['labels']['severity']='critical'
        with self.assertRaises(ValueError):self.inbox.accept(self.body(value))
        with patch.object(self.queue,'transaction',side_effect=sqlite3.OperationalError('private-error')):
            with self.assertRaises(sqlite3.Error):self.inbox.accept(self.body())

    def test_deleted_job_tombstone_still_suppresses_firing(self):
        self.inbox.accept(self.body())
        result=self.fixture.worker().run_once()
        self.fixture.time+=RETENTION_SECONDS
        QueueRetention(self.queue).run(apply=True)
        self.assertEqual(self.queue.show(result['job_id'])['state'],'purged')
        self.assertEqual(self.inbox.accept(self.body())['duplicate'],1)
        self.assertEqual(self.rows('SELECT * FROM jobs'),[])

    def test_concurrent_delivery_creates_one_job(self):
        values=[];errors=[]
        def run():
            try:values.append(self.inbox.accept(self.body()))
            except Exception as error:errors.append(error)
        threads=[threading.Thread(target=run) for _ in range(4)]
        for thread in threads:thread.start()
        for thread in threads:thread.join(5)
        self.assertFalse(errors)
        self.assertEqual(sum(v['accepted'] for v in values),1)
        self.assertEqual(sum(v['duplicate'] for v in values),3)
        self.assertEqual(len(self.rows('SELECT * FROM jobs')),1)

    def test_operator_timeline_and_tombstone_capacity(self):
        self.inbox.accept(self.body())
        job=self.rows('SELECT * FROM jobs')[0]['job_id']
        self.fixture.worker().run_once()
        timeline=show_alert(self.queue,job)
        self.assertEqual(timeline['alert']['starts_at'],'1970-01-01T00:10:00.123456789Z')
        self.assertEqual(timeline['alert']['received_at'],'1970-01-01T00:16:40Z')
        self.assertEqual(timeline['attempts'][0]['started_at'],'1970-01-01T00:16:40Z')
        self.assertNotIn('untrusted-secret',json.dumps(timeline))
        with patch('ai_ops.alerts.MAX_ALERT_EPISODES',1):
            self.assertEqual(self.inbox.accept(self.body())['duplicate'],1)
            with self.assertRaises(QueueFull):
                self.inbox.accept(self.body(self.alert(fingerprint='f'*16)))


if __name__=='__main__':unittest.main()
