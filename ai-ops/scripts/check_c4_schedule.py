"""새 자동 조회 경로와 마지막 읽기 지연만 검증한다. 자격 발급·외부 쓰기는 없다."""

import argparse
from datetime import datetime, timezone
import json
import time

from ai_ops.c_trial_execution import TrialPlan, verify_normal_trial
from ai_ops.c_trial_guards import private_json
from ai_ops.c_trial_schedule import RefreshingProbe
from ai_ops.c_trial_transport import C4TrialTransport
from ai_ops.path_policy import cli_paths, write_private_json


def main():
    parser=argparse.ArgumentParser(description='C-04 자동 조회의 읽기 전용 실측')
    parser.add_argument('--plan',required=True)
    parser.add_argument('--kubeconfig',required=True)
    parser.add_argument('--output',required=True)
    parser.add_argument('--live-read-check',action='store_true',required=True)
    args=cli_paths(parser,parser.parse_args(),{'plan':'input','output':'output'})
    plan=TrialPlan.model_validate(private_json(args.plan))
    from ai_ops.c_trial_aws import AWSImageReader
    samples=[]
    probe=RefreshingProbe(plan,AWSImageReader(),publish=lambda value:samples.append(value))
    transport=C4TrialTransport(args.kubeconfig,image_check=probe.require,
        guard_check=lambda plan:False,merge_session=None)
    delays=[]
    try:
        probe.start()
        end=time.monotonic()+40
        while len(samples)<3:
            if time.monotonic()>end or probe.error_type:
                raise ValueError('새 AWS 자동 조회를 확인하지 못했습니다')
            time.sleep(.1)
        for _ in range(3):
            tick=time.monotonic()
            snapshot=transport.snapshot()
            verify_normal_trial(plan,snapshot,now=datetime.now(timezone.utc))
            probe.require(plan)
            delays.append(time.monotonic()-tick)
    finally:probe.close()
    result={'record_type':'c04_automatic_schedule_readonly_measurement',
        'checked_at':datetime.now(timezone.utc).isoformat(),'plan_hash':plan.digest(),
        'image_samples':samples,'snapshot_and_cache_seconds':delays,
        'last_read_within_five_seconds':max(delays)<5,'normal_runtime_verified':True,
        'external_writes':0,'tokens_issued':0,'guards_started':0,'fault_injected':False,
        'ready_to_inject':False,'scope':'actual AWS refresh and normal target reads; active guard readiness needs fresh approved trial'}
    write_private_json(args.output,result)
    print(json.dumps({key:result[key] for key in ('external_writes','snapshot_and_cache_seconds','last_read_within_five_seconds','ready_to_inject')}))
    return 0 if result['last_read_within_five_seconds'] else 1


if __name__=='__main__':raise SystemExit(main())
