"""공개 저장소의 검토된 commit으로 재현 가능한 C-03/C-04 실행 묶음을 만든다."""

import argparse
from pathlib import Path
import json

from ai_ops.c_trial_bundle import build_repository_runtime
from ai_ops.path_policy import cli_paths


def main():
    parser = argparse.ArgumentParser(description='검토된 Git commit에서 C-04 실행 묶음 생성')
    parser.add_argument('--revision', required=True)
    parser.add_argument('--destination', type=Path, required=True)
    args = cli_paths(parser, parser.parse_args(), {'destination': 'store'})
    root = Path(__file__).resolve().parents[2]
    manifest = build_repository_runtime(root, args.revision, args.destination)
    print(json.dumps({'source_revision': manifest['source_base_sha'],
                      'files': len(manifest['files']), 'credentials_included': False}))


if __name__ == '__main__': main()
