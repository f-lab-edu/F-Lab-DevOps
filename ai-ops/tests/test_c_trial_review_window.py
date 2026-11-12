"""안내 후 2분 미만·다른 head·늦은 승인·임의 원복 연장을 차단한다."""

from datetime import timedelta
import unittest

from ai_ops.c_trial_execution import TrialPlan
from ai_ops.c_trial_review_window import require_review_notice,review_in_time,review_path_ended
import test_c_trial_execution as fixtures


class ReviewWindowTest(unittest.TestCase):
    def setUp(self):
        fixture=fixtures.TrialExecutionTest();fixture.setUp()
        self.old=fixture.plan
        self.plan=TrialPlan.model_validate({**self.old.model_dump(), 'schema_version':2,
            'review_deadline':self.old.planned_at+timedelta(seconds=210),
            'minimum_review_seconds':120,'manual_recovery_at':self.old.planned_at+timedelta(seconds=240)})
        self.head='e'*40
        self.notice={'trial_id':self.plan.trial_id,'plan_hash':self.plan.digest(),'pull_number':8,
            'head_sha':self.head,'notified_at':(self.plan.planned_at+timedelta(seconds=80)).isoformat(),
            'review_deadline':self.plan.review_cutoff().isoformat()}
        from ai_ops.c_trial_notice import _request
        from ai_ops.contracts import sha256_json
        request=_request(self.plan,8,self.head,'a'*32)
        self.notice.update(delivery_transport='codex-host-bridge',delivery_request=request,
            delivery_request_hash=sha256_json(request),delivery_id='synthetic-local-tool-ack')

    def check(self,notice=None,now=None):
        return require_review_notice(self.plan,notice or self.notice,pull_number=8,head=self.head,
            now=now or self.plan.planned_at+timedelta(seconds=85))

    def test_two_minute_notice_and_in_time_review_allow_preflight_after_review_cutoff(self):
        self.check()
        self.check(now=self.plan.planned_at+timedelta(seconds=220))
        self.assertTrue(review_in_time({'submitted_at':self.plan.review_cutoff().isoformat()},self.plan))

    def test_late_notice_future_notice_wrong_head_or_trial_are_rejected(self):
        for change in [{'notified_at':(self.plan.planned_at+timedelta(seconds=91)).isoformat()},
            {'notified_at':(self.plan.planned_at+timedelta(seconds=86)).isoformat()},
            {'head_sha':'f'*40},{'trial_id':123},{'plan_hash':'0'*64}]:
            with self.subTest(change=change),self.assertRaises(ValueError):self.check({**self.notice,**change})

    def test_review_submitted_after_deadline_is_not_accepted_even_if_poll_started_earlier(self):
        self.assertFalse(review_in_time({'submitted_at':(self.plan.review_cutoff()+timedelta(microseconds=1)).isoformat()},self.plan))
        with self.assertRaises(ValueError):self.check(now=self.plan.manual_recovery_at)

    def test_new_safety_schedule_is_fixed_and_old_digest_is_unchanged(self):
        from ai_ops.contracts import sha256_json
        old_data=self.old.model_dump(mode='json');old_data.pop('review_deadline');old_data.pop('minimum_review_seconds')
        self.assertEqual(self.old.digest(),sha256_json(old_data))
        for change in [{'manual_recovery_at':self.plan.planned_at+timedelta(seconds=270)},
                       {'rollback_deadline':self.plan.planned_at+timedelta(seconds=360)},
                       {'minimum_review_seconds':30}]:
            with self.subTest(change=change),self.assertRaises(ValueError):TrialPlan.model_validate({**self.plan.model_dump(),**change})

    def test_review_path_failure_requests_owned_fallback_but_active_review_does_not(self):
        self.assertTrue(review_path_ended({'stage':'minimum_notification_window_missed'}))
        self.assertTrue(review_path_ended({'stage':'stopped'}))
        self.assertFalse(review_path_ended({'stage':'waiting_exact_head_review_and_ci'}))
        self.assertFalse(review_path_ended({'stage':'review_and_ci_verified_context_published'}))


if __name__=='__main__':unittest.main()
