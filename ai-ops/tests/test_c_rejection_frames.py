"""거절 위치 진단이 예외 원문·외부 경로·locals를 내보내지 않는지 검증한다."""
from pathlib import Path
import unittest

from ai_ops.c_trial_read_budget import rejection_source_frames


class RejectionFramesTest(unittest.TestCase):
    def test_real_bound_failure_retains_trusted_source_without_exception_or_locals(self):
        from test_c_observation_stability import ObservationStabilityTest
        fixture=ObservationStabilityTest();self.addCleanup(fixture.doCleanups)
        secret='sensitive-response-not-for-results'
        def mutate(snapshot):snapshot['application']['metadata']['uid']=secret
        try:
            fixture.bound_preflight(mutate)
        except Exception as error:
            frames=rejection_source_frames(error)
        else:self.fail('changed UID must reject')
        self.assertTrue(frames)
        self.assertLessEqual(len(frames),8)
        self.assertTrue(any(frame['source']=='src/ai_ops/c_failure_handoff.py' for frame in frames))
        self.assertNotIn(secret,str(frames))
        for frame in frames:
            self.assertEqual(set(frame),{'source','function','line'})
            self.assertFalse(Path(frame['source']).is_absolute())
            self.assertGreater(frame['line'],0)

    def test_external_frames_messages_and_cyclic_causes_are_not_emitted(self):
        try:raise ValueError('sensitive-message')
        except ValueError as error:
            error.__cause__=error
            self.assertEqual(rejection_source_frames(error),[])


if __name__=='__main__':unittest.main()
