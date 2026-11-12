"""보존 실행 묶음과 명시한 작업 소스를 결합하고 실행 전 파일 해시를 대조한다."""

import hashlib
import importlib.metadata
import json
from pathlib import Path
import re
import shutil
import io
import subprocess
import tarfile


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_runtime_bundle(root):
    root=Path(root).resolve(strict=True)
    manifest=json.loads((root/'runtime-manifest.json').read_bytes())
    if manifest.get('schema_version')!=1 or manifest.get('credentials_included') is not False:
        raise ValueError('검증된 실행 묶음 manifest가 필요합니다')
    actual={str(p.relative_to(root)) for p in root.rglob('*') if p.is_file()
            and '__pycache__' not in p.parts and p!=root/'runtime-manifest.json'}
    if actual!=set(manifest['files']):
        raise ValueError('실행 묶음에 검증하지 않은 파일이 추가되거나 제거됐습니다')
    for name,expected in manifest['files'].items():
        path=root/name
        if ('..' in Path(name).parts or Path(name).is_absolute() or path.is_symlink()
                or not path.is_file() or not path.resolve().is_relative_to(root)
                or file_hash(path)!=expected):
            raise ValueError('실행 묶음 파일이 검증 후 변경됐습니다')
    for name,version in manifest['sdk_versions'].items():
        if importlib.metadata.version(name)!=version:
            raise ValueError('실행 묶음의 SDK 의존성 버전이 다릅니다')
    return manifest


def verify_ready_runtime(root, intent):
    """파일과 manifest를 함께 바꿔도 승인한 준비 코드의 hash를 바꿀 수 없다."""
    manifest = verify_runtime_bundle(root)
    if file_hash(Path(root)/'runtime-manifest.json') != intent.runtime_manifest_sha256:
        raise ValueError('ready_runtime_changed')
    return manifest


def build_repository_runtime(source, revision, destination):
    """검토한 Git commit의 공개 프로젝트 파일만 묶는다. 개인 보존 묶음은 필요 없다."""
    if not isinstance(revision, str) or not re.fullmatch('[a-f0-9]{40}', revision):
        raise ValueError('고정 Git commit SHA가 필요합니다')
    source, destination = Path(source).resolve(strict=True), Path(destination)
    sdk_versions = {name: importlib.metadata.version(name) for name in
                    ('boto3', 'botocore', 'awscrt', 's3transfer', 'jmespath', 'urllib3', 'python-dateutil', 'six')}
    result = subprocess.run(['git', '-C', str(source), 'archive', '--format=tar', revision, '--',
        'ai-ops', '.github'], capture_output=True, timeout=30)
    if result.returncode or len(result.stdout) > 64*1024*1024:
        raise ValueError('고정 commit의 실행 소스를 읽지 못했습니다')
    members = []
    with tarfile.open(fileobj=io.BytesIO(result.stdout), mode='r:') as archive:
        for item in archive.getmembers():
            if item.isdir(): continue
            name = Path(item.name)
            if (not item.isfile() or name.is_absolute() or '..' in name.parts
                    or name.parts[0] not in {'ai-ops', '.github'}
                    or item.size > 4*1024*1024 or str(name) == 'runtime-manifest.json'):
                raise ValueError('실행 소스의 경로·형식·크기가 유효하지 않습니다')
            members.append((name, archive.extractfile(item).read(), item.mode))
    required = {'ai-ops/src/ai_ops/c_approval.py', 'ai-ops/src/ai_ops/c_execution_preflight.py',
                'ai-ops/src/ai_ops/c_trial_ready.py', '.github/workflows/ai-ops-c4-ready-rollback.yml'}
    if not required.issubset({str(name) for name, _, _ in members}):
        raise ValueError('해당 commit에 C-03/C-04 실행 의존성이 없습니다')
    destination.mkdir(mode=0o700, parents=False, exist_ok=False)
    for name, raw, mode in members:
        target = destination/name; target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        target.write_bytes(raw); target.chmod(0o700 if mode & 0o111 else 0o600)
    manifest = {'schema_version': 1, 'source_base_sha': revision, 'credentials_included': False,
        'files': {str(name): hashlib.sha256(raw).hexdigest() for name, raw, _ in members},
        'sdk_versions': sdk_versions}
    (destination/'runtime-manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
    (destination/'runtime-manifest.json').chmod(0o600)
    return verify_runtime_bundle(destination)


def build_runtime(base, base_manifest, overlay, destination):
    """과거 묶음은 수정하지 않고 새 디렉터리에 검증된 소스만 복사한다."""
    base,overlay,destination=map(Path,(base,overlay,destination))
    manifest=json.loads(Path(base_manifest).read_bytes())
    if manifest.get('credentials_included') is not False:
        raise ValueError('비밀값 없는 원본 manifest가 필요합니다')
    base_sha=manifest.get('source_base_sha',manifest.get('base_sha'))
    if not isinstance(base_sha,str) or not re.fullmatch('[a-f0-9]{40}',base_sha):
        raise ValueError('원본 실행 묶음의 Git SHA가 필요합니다')
    for name,expected in manifest['files'].items():
        path=base/name
        if ('..' in Path(name).parts or Path(name).is_absolute() or path.is_symlink()
                or not path.is_file() or not path.resolve().is_relative_to(base.resolve())
                or file_hash(path)!=expected):
            raise ValueError('원본 보존 묶음 해시가 다릅니다')
    destination.mkdir(mode=0o700,parents=False,exist_ok=False)
    for name in manifest['files']:
        target=destination/name;target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(base/name,target)
    # main의 오래된 C-03 파일로 보존된 C-03 계약을 덮지 않는다.
    script_names={'run_c4_trial.py','prepare_c4_trial.py','restore_c4_trial.py',
                  'supervise_c4_trial.py','check_c4_schedule.py','prepare_c4_recovery_pr.py',
                  'bridge_c4_review_notice.py','run_c4_ready_trial.py',
                  'run_c4_ready_guard.py','run_c2_rollback_guard.py'}
    for folder in ('src','scripts','tests'):
        for source in (overlay/'ai-ops'/folder).rglob('*'):
            if '__pycache__' in source.parts or not source.is_file():continue
            if not (folder=='src' and (source.name.startswith('c_trial_') or source.name=='path_policy.py')
                    or folder=='tests' and (source.name.startswith('test_c_trial_') or source.name=='test_path_policy.py')
                    or folder=='scripts' and source.name in script_names):continue
            if source.is_symlink():raise ValueError('overlay 링크는 허용하지 않습니다')
            relative=source.relative_to(overlay)
            target=destination/relative;target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(source,target)
    for name in ('pyproject.toml','requirements.lock','requirements-trial.lock'):
        shutil.copy2(overlay/'ai-ops'/name,destination/'ai-ops'/name)
    # 새 원격 감시 선언과 공개 계약도 실행 묶음의 파일 목록에 포함한다.
    for name in ('.github/workflows/ai-ops-c4-ready-rollback.yml',
                 *('ai-ops/schemas/c4-ready-'+kind+'.schema.json'
                   for kind in ('intent', 'receipt', 'approval', 'activation'))):
        source = overlay/name
        if source.exists():
            if source.is_symlink(): raise ValueError('overlay 링크는 허용하지 않습니다')
            target = destination/name; target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    result={'schema_version':1,'source_base_sha':base_sha,
        'credentials_included':False,'files':{str(p.relative_to(destination)):file_hash(p)
            for p in destination.rglob('*') if p.is_file() and '__pycache__' not in p.parts},
        'sdk_versions':{name:importlib.metadata.version(name) for name in
            ('boto3','botocore','awscrt','s3transfer','jmespath','urllib3','python-dateutil','six')}}
    (destination/'runtime-manifest.json').write_text(json.dumps(result,indent=2)+'\n')
    return verify_runtime_bundle(destination)
