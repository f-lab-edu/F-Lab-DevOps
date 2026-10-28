# 설치·정리 시 로컬 자격 원본 보존

이 문서는 프로젝트 인프라의 설치·전체 정리 양쪽에 적용한다. **AWS 리소스 전체 삭제, Kubernetes namespace/Secret 삭제, 임시 설치 폴더 정리는 로컬 자격 원본 삭제 요청이 아니다.** 값은 Git·문서·채팅·명령 출력에 기록하지 않는다. 자격을 폐기하거나 교체할 때는 별도 요청과 발급처의 회전·폐기 절차를 따른다.

## 보존 대상과 재설치 사용처

로컬 원본 디렉터리: `/Users/jounghyeon/.local/share/url-shortener-install/credentials/` (권한 `0700`). 각 파일은 일반 파일이며 권한 `0600`이어야 한다.

| 파일명 | 용도 | 전체 인프라 정리 | 재설치 시 확인 |
| --- | --- | --- | --- |
| `slack-webhook-url` | Alertmanager Slack 알림 URL | 로컬 원본 보존. `monitoring` Secret 복사본은 클러스터와 함께 제거 | 파일 존재·권한·URL 유효성 확인 후 `alertmanager-slack-secret` 복원. 실제 전달은 승인된 범위에서 검증 |
| `watchdog-heartbeat-url` | Watchdog 하트비트 URL | 로컬 원본 보존. `monitoring` Secret 복사본은 제거 | 파일 존재·권한·URL 유효성 확인 후 `alertmanager-heartbeat-secret` 복원. 실제 수신은 별도 확인 |
| `github-actions-read-token` | AI CI 전달기의 GitHub Actions 읽기 전용 토큰 | 로컬 원본 보존. `ai-ci/ci-relay-github` Secret 복사본은 제거 | 만료·권한·대상 저장소 확인 후 새 클러스터 Secret 생성. 만료·폐기됐으면 최소 권한으로 재발급 |
| `db-password` | Terraform RDS와 앱 DB 접속 | 로컬 원본 보존 | 현재 Terraform 입력·회전 버전·새 RDS 실제 인증과 대조. 저장된 값이 현재 DB에 맞는다고 가정하지 않음 |
| `redis-password` | Redis와 앱 접속 | 로컬 원본 보존 | Redis 설치 값과 앱 Secret 값을 동일하게 적용하고 실제 PING 확인 |
| `grafana-admin-user`, `grafana-admin-password` | Grafana 관리자 자격 | 로컬 원본 보존 | 현재 Grafana Secret에 적용할 값인지 확인 |

AI 모델 API 키 원본 `/Users/jounghyeon/.local/share/url-shortener-ai-ops/.openai-api-key`도 로컬에 보존한다. 클러스터의 모델 Secret은 정리 대상이며, 키 파일의 존재만으로 다음 설치에서 모델 호출을 켜지 않는다. 새 클러스터·이미지·입력·비용 범위에 묶인 캠페인 검토와 별도 승인이 필요하다. 개인 파일·로컬 보고서·로컬 사이트 역시 AWS 정리 대상이 아니다.

C-02 실습 사건 조회를 활성화했다면 `/Users/jounghyeon/.local/share/url-shortener-ai-ops/c2-projection-identity-20261028/`의 `token`, `tls.crt`, `tls.key`도 **로컬 원본으로 보존**한다. 디렉터리는 `0700`, 세 파일은 `0600`이어야 한다. AWS 전체 정리 때 `ai-ci/ci-projection-identity` Secret 복사본은 제거하지만 로컬 원본은 지우지 않는다. 재설치 시 [C-02 배포 기록](../../ai-ops/docs/C2-local-request-and-proposal.md)에 따라 인증서 SAN·만료일을 확인하고 새 클러스터의 Secret을 만든 뒤 읽기 전용 sidecar와 로컬 CA를 연결한다. 현재 인증서는 **2026-11-27 18:22 KST**에 만료되므로 그 이후에는 기존 원본을 재사용하지 말고 새 토큰·인증서를 발급한다. 이 자격은 GitHub Actions 읽기 토큰·모델 API 키와 분리한다.

Argo CD의 `ARGOCD_AUTH_TOKEN`과 실습 CI용 `S10_ARGOCD_TOKEN`은 **클러스터에 종속되고 만료되는 자격**이다. 삭제된 클러스터의 토큰을 재설치에 사용하지 않는다. 현재 클러스터의 최소 권한 역할에서 새 토큰을 발급해 해당 GitHub Secret을 갱신하고 실제 CI 주입을 검증한다. GitHub Actions 읽기 토큰과 Argo 토큰을 혼동하지 않는다.

## 정리 직전과 재설치 직전 확인

다음 검사는 **파일명·유형·권한·비어 있지 않은지만** 출력한다. 값이나 해시는 출력하지 않는다. 정리 직전 결과를 기록하고, 정리 후 같은 검사로 로컬 원본이 유지됐는지 확인한다.

```bash
python3 - <<'PY'
from pathlib import Path
import stat

root = Path('/Users/jounghyeon/.local/share/url-shortener-install/credentials')
names = (
    'slack-webhook-url', 'watchdog-heartbeat-url', 'github-actions-read-token',
    'db-password', 'redis-password', 'grafana-admin-user', 'grafana-admin-password',
)
if not root.exists() or not root.is_dir() or root.is_symlink() or stat.S_IMODE(root.stat().st_mode) != 0o700:
    raise SystemExit('로컬 자격 디렉터리 부재 또는 권한 오류')
for name in names:
    path = root / name
    if not path.exists() or path.is_symlink() or not path.is_file():
        raise SystemExit(f'{name}: 원본 파일 부재 또는 유형 오류')
    if stat.S_IMODE(path.stat().st_mode) != 0o600 or path.stat().st_size == 0:
        raise SystemExit(f'{name}: 권한 또는 크기 오류')
    print(f'{name}: 로컬 원본 확인')
api_key = Path('/Users/jounghyeon/.local/share/url-shortener-ai-ops/.openai-api-key')
if api_key.exists():
    if api_key.is_symlink() or not api_key.is_file() or stat.S_IMODE(api_key.stat().st_mode) != 0o600 or api_key.stat().st_size == 0:
        raise SystemExit('AI API 키 원본 유형 또는 권한 오류')
    print('AI API 키: 로컬 원본 확인')
else:
    print('AI API 키: 원본 없음, 모델 기능 재설치 불가')
projection = Path('/Users/jounghyeon/.local/share/url-shortener-ai-ops/c2-projection-identity-20261028')
if projection.exists():
    if projection.is_symlink() or not projection.is_dir() or stat.S_IMODE(projection.stat().st_mode) != 0o700:
        raise SystemExit('C-02 조회 자격 디렉터리 유형 또는 권한 오류')
    for name in ('token', 'tls.crt', 'tls.key'):
        path = projection / name
        if path.is_symlink() or not path.is_file() or stat.S_IMODE(path.stat().st_mode) != 0o600 or path.stat().st_size == 0:
            raise SystemExit(f'C-02 {name}: 원본 파일 유형·권한·크기 오류')
    print('C-02 조회 자격: 로컬 원본 확인, 인증서 만료·SAN은 별도 검사 필요')
else:
    print('C-02 조회 자격: 원본 없음, 선택적 조회 경로 재설치 불가')
PY
```

파일이 없으면 이전 임시 폴더나 삭제된 클러스터에서 복구된다고 가정하지 않는다. 정리 전에 **유일한 복사본인지 확인**하고 보호된 원본을 확보하거나 재발급 계획을 확정한다. 원본이 없거나 유효하지 않은 상태로 정리한다면 해당 연동은 재설치 미완료로 기록한다. `/private/tmp`의 설치 작업 폴더, Kubernetes Secret, GitHub Secret의 *이름*만으로 원본이 보존됐다고 판정하지 않는다.

정리 명령의 경로가 위 로컬 원본 디렉터리 또는 AI API 키 파일을 포함하면 실행을 중단한다. `rm -rf`의 광범위한 상위 경로 삭제, 임시 폴더 정리 스크립트의 확장, 전체 홈 디렉터리 청소에 이 원본을 섞지 않는다. AWS에서 제거하는 Secret 복사본과 보존할 로컬 원본의 이름·위치를 실행 기록에서 분리한다.
