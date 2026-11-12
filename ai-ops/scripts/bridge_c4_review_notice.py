"""Codex 호스트의 실제 안내 도구와 한 실습의 알림 소켓을 연결한다.

stdout의 arguments를 실제 request_user_input_async로 전송한 다음에만
실제 성공 응답을 {"delivery_tool_response":{"accepted":true}} 한 줄로 stdin에 전달한다.
도구가 식별자를 제공한 경우만 {"delivery_id":"실제 식별자"}를 사용한다.
질문 출력 자체는 성공이 아니며 시간 입력·사용자 리뷰·승인을 받지 않는다.
"""

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import select
import sys

from ai_ops.c_trial_execution import TrialPlan
from ai_ops.c_trial_guards import private_json
from ai_ops.c_trial_notice import delivery_evidence, serve_bridge
from ai_ops.path_policy import permitted_path


def main():
    parser = argparse.ArgumentParser(description='현재 실습의 Codex 안내 호스트 연결')
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--socket', required=True)
    parser.add_argument('--rehearsal', action='store_true',
                        help='PR 승인을 요청하지 않는 별도 리허설 전송; 실제 실행 영수증으로 사용 불가')
    parser.add_argument('--when-ready', action='store_true')
    parser.add_argument('--activation', type=Path,
                        help='준비 완료 방식의 승인 후 생성할 불변 activation 파일')
    args = parser.parse_args()
    plan = None
    resolver = None
    standby_until = None
    if args.when_ready:
        from ai_ops.c_trial_ready import ReadyTrialPlan, ReadyActivation
        if args.rehearsal or args.activation is None:
            raise ValueError('ready_bridge_arguments_rejected')
        intent = ReadyTrialPlan.model_validate(private_json(permitted_path(args.plan, 'input')))
        activation_path = permitted_path(args.activation, 'store')
        standby_until = datetime.now(timezone.utc)+timedelta(minutes=15)
        bound = []
        def resolver():
            if not activation_path.exists():
                if bound:
                    raise ValueError('ready_bridge_activation_removed')
                return None
            activation = ReadyActivation.model_validate(private_json(activation_path))
            if activation.intent != intent or (bound and bound[0] != activation):
                raise ValueError('ready_bridge_activation_changed')
            if not bound:
                bound.append(activation)
            return activation.execution
    else:
        if args.activation is not None:
            raise ValueError('unexpected_ready_bridge_activation')
        plan = TrialPlan.model_validate(private_json(permitted_path(args.plan, 'input')))

    def notify(arguments):
        active = resolver() if resolver is not None else plan
        if active is None:
            raise ValueError('ready_bridge_not_activated')
        print(json.dumps({'status':'review_notice_requested',
                          'tool':'functions.request_user_input_async','arguments':arguments},ensure_ascii=False),flush=True)
        remaining = (active.review_cutoff()-timedelta(seconds=active.minimum_review_seconds)
                     -datetime.now(timezone.utc)).total_seconds()
        if remaining <= 0 or not select.select([sys.stdin], [], [], remaining)[0]:
            raise ValueError('review_notice_host_timeout')
        raw = sys.stdin.readline(1025)
        if len(raw) > 1024 or not raw.endswith('\n'):
            raise ValueError('review_notice_host_response_rejected')
        result = json.loads(raw)
        return delivery_evidence(result)

    serve_bridge(plan, args.socket, notify, rehearsal=args.rehearsal,
                 resolve_plan=resolver, standby_until=standby_until)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, KeyError, TypeError, OSError):
        raise SystemExit('C-04 안내 전달을 확인하지 못했습니다. 전달 성공·사람 승인으로 처리하지 않습니다.') from None
