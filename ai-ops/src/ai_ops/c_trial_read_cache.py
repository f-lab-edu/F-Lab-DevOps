"""내용 주소 객체만 재사용하고 상태·리뷰·CI 메타데이터는 매번 새로 읽는다."""

from copy import deepcopy
import re


class ImmutableGitReader:
    def __init__(self, reader):
        self.reader, self.cache = reader, {}
        self.hits = 0

    def get(self, path):
        immutable = re.fullmatch(r"repos/joungGo/c03-execution-lab/git/(?:commits|trees|blobs)/([0-9a-f]{40})", path)
        if not immutable:
            return self.reader.get(path)
        if path in self.cache:
            self.hits += 1
            return deepcopy(self.cache[path])
        value = self.reader.get(path)
        if not isinstance(value, dict) or value.get('sha') != immutable[1]:
            raise ValueError('immutable_git_object_identity_mismatch')
        if len(self.cache) < 32:
            self.cache[path] = deepcopy(value)
        return value


class ArchiveBytesReader:
    """ZIP만 보관한다. 호출자는 매번 새 listing의 digest로 ZIP을 다시 검증한다."""

    def __init__(self, reader):
        self.reader, self.cache = reader, {}
        self.hits = 0

    def get(self, path, **kwargs):
        immutable = re.fullmatch(r"repos/f-lab-edu/F-Lab-DevOps/actions/artifacts/[1-9][0-9]*/zip", path)
        if not immutable:
            return self.reader.get(path, **kwargs)
        if path in self.cache:
            self.hits += 1
            value = self.cache[path]
        else:
            value = self.reader.get(path, **kwargs)
            if not isinstance(value, bytes) or len(value) > 1024*1024:
                raise ValueError('normal_archive_cache_size_rejected')
            if len(self.cache) < 4:
                self.cache[path] = value
        if len(value) > kwargs.get('max_bytes', 1024*1024):
            raise ValueError('normal_archive_cache_size_rejected')
        return value


def warm_trial_reads(plan, binding, normal_run_id, normal_head):
    """주입 전 불변 콘텐츠만 메모리에 준비한다. 활성 장애 근거는 만들지 않는다."""
    from ai_ops.c_approval import ApprovalGitHubReader
    from ai_ops.ci_history import GitHubReader
    from ai_ops.normal_ci_history import verify_normal_ci_history
    import importlib.util
    from pathlib import Path
    git=ImmutableGitReader(ApprovalGitHubReader(repository='joungGo/c03-execution-lab'))
    history=ArchiveBytesReader(GitHubReader())
    verify_normal_ci_history(normal_run_id,normal_head,binding,reader=history)
    path=Path(__file__).resolve().parents[2]/'scripts/audit_c2_live_preflight.py'
    spec=importlib.util.spec_from_file_location('c04_static_values',path)
    audit=importlib.util.module_from_spec(spec);spec.loader.exec_module(audit)
    for revision in (plan.normal_revision,plan.failure_revision):
        audit._github_values(revision,reader=git,repository='joungGo/c03-execution-lab')
    return {'git_reader':git,'history_reader':history}
