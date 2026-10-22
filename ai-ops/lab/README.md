# B-06 S10 실습 앱 GitOps 입력

이 경로는 `ai-ops-lab/lab-api` Deployment 한 개만 관리한다. Argo 입력 파일은 `deployment.json`으로 고정하며 다른 리소스를 이 경로에 추가하지 않는다. DB·Redis·수집기·Secret·namespace는 기존 실습 설치에서 관리한다.

현재는 인계 준비 단계다. 파일이 포함된 검토 커밋을 준비하고 [GitOps 준비 절차](../docs/B6-gitops-preparation.md)에 따라 권한·서버 검증 정책·공유 잠금과 기존 spec을 확인한 뒤 수동 동기화한다. 기존 수동 S3/S11 실행기와 동시에 사용하지 않는다.

2026-10-22 재설치 기준 앱 이미지는 image-only 실행 `39712114396`으로 등록한 `sha-38b5d85`의 digest를 사용한다. 다음 설치에서 이 파일과 생성한 실습 앱 spec이 일치하는지 확인한다. 파일 존재만으로 현재 실습이 배포됐거나 소유권 인계가 완료된 것은 아니다.
