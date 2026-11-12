"""현재 운영자의 명시적인 취소를 같은 실습 실행기에 전달한다."""

import argparse
from pathlib import Path
import subprocess
import json

from ai_ops.c_trial_execution import TrialPlan
from ai_ops.c_trial_guards import private_json
from ai_ops.path_policy import cli_paths, write_private_json


def request_cancel(plan, root, actor):
    if type(actor) is not int or actor != 149856960:
        raise ValueError('execution_cancel_actor_rejected')
    owner = private_json(root/'result-owner.json')
    if owner['trial_id'] != plan.trial_id or owner['plan_hash'] != plan.digest():
        raise ValueError('execution_cancel_owner_mismatch')
    value = {'trial_id': plan.trial_id, 'plan_hash': plan.digest(),
             'actor_github_id': actor, 'cancel_execution': True}
    path = root/'cancel-execution.json'
    if path.exists():
        if private_json(path) != value:
            raise ValueError('execution_cancel_changed')
    else:
        write_private_json(path, value)
    return {'cancel_requested': True, 'already_applied_changes_reverted': False}


def main():
    parser = argparse.ArgumentParser(description='미실행 복구 단계를 취소; 적용된 Git 변경은 자동 취소하지 않음')
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--run-dir', type=Path, required=True)
    args = cli_paths(parser, parser.parse_args(), {'plan': 'input', 'run_dir': 'store'})
    result = subprocess.run(['gh', 'api', 'user', '--jq', '.id'], check=True,
                            capture_output=True, text=True, timeout=10)
    actor = int(result.stdout.strip())
    print(json.dumps(request_cancel(TrialPlan.model_validate(private_json(args.plan)), args.run_dir, actor)))


if __name__ == '__main__':
    main()
