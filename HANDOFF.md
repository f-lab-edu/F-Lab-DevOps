# URL Shortener AI Ops 인계

## 현재 인계 · 2026-11-13 · 재설치 요청 전까지 작업 대기

**사용자 결정: 인프라 없이 끝낼 수 있는 코드 검증/PR 반영까지만 완료하고, 새 환경에서 다시 해야 하는 연결 설정은 재설치 후 함께 진행한다.** 지금 OIDC·이미지 발행·private chart·실행기/CI/원복/collector 설정·새 실습을 진행하지 않는다. 아래 과거 기록의 “다음은 로컬 검증/PR1 병합/즉시 연결 전환 계획/정리 계속”보다 이 절이 우선한다.

### 여기까지 완료

- **인프라 정리:** 2026-11-13 최종 검증에서 운영/AI managed state 각각0개·잠금 없음, 프로젝트 EKS/RDS/EC2/NAT/VPC/LB/ECR/SQS·기록 EBS10개/EIP1개 실제 부재 확인. state/잠금 복구도 완료했다. 이번 문서 갱신에서는 AWS 재조회나 리소스 변경을 하지 않았다.
- **보존:** DNS 전체·S3 backend/state/버전·기존 RDS 스냅샷6개·operator와 로그인/정책·로컬 자료를 유지했다. 새 최종 RDS 스냅샷 `urlshortener-postgres-final-snapshot-urlshortener-postgres-6962a7a0`은 마지막 확인 available/encrypted, 그 KMS는 Enabled다. 별도 EKS키는 예약 삭제(확인 당시 2026-12-13 00:55 KST 예정)이며 공유 RDSOSMetrics는 빈 그룹만 보존했다. 보존 자원은 비용0을 뜻하지 않는다. [정리 완료 기록](docs/runbooks/infrastructure-cleanup-2026-11-13.md) 참조.
- **AI 저장소 분리:** 비공개 [joungGo/url-shortener-ai-ops](https://github.com/joungGo/url-shortener-ai-ops), 로컬 `/Users/jounghyeon/dev/url-shortener-ai-ops`가 AI 코드 기준이다. 운영 앱/기반은 플랫폼, 격리 복구 PR은 `joungGo/c03-execution-lab` 소유다. 실제 실행 연결/권한은 아직 새 AI 저장소로 전환하지 않았다.
- **C-04 로컬 검증:** 전체880개/집중15개·skip0, 구문287파일·schema14개·fixture4종·저장소경계·고정 chart Helm 검사 통과. 012의 관측/요청 차이2.009996초와 최대5초/기존마감 내 실제대기·동일 투영 재조회·미래요청 미저장·실패시중지를 검증했다. 호스트 시계 원인은 미측정이다.
- **PR1 반영:** 검증head0997을 일반squash로 AI main **`69f99a08d25242b5d7774aacdc2844bb471f0997`**에 병합하고 로컬main/전체트리/파일해시 일치를 확인했다. 새 PR CI43257307388의5종과 main CI43257778832(4성공·PR전용gate skip)가 성공했다. 비공개 요금제 보호기능 미지원·protected=false/리뷰0건을 확인한 일반 병합이며 보호변경/관리자우회는 없었다. 원본 PR156은 변경하지 않았다. **PR1을 재병합하거나 과거 PR156 병합 helper를 실행하지 않는다.**
- **미완료:** C-04 실환경 복구 성공·C-05 최종 완료·별도 AI 저장소 실행 연결 전환은 남았다. A5/A7은 미측정, 기존 B 범위와 AI 포트폴리오 보류를 유지한다.

### 재설치 요청 후 재개 순서

1. 별도 AI 저장소의 **[최신 HANDOFF](/Users/jounghyeon/dev/url-shortener-ai-ops/HANDOFF.md)**·AGENTS와 integration/README.md를 먼저 읽는다. main/보존 자료 무결성·계정764428911194/서울 리전·기존 프로필 세션/권한·현재 backend·DNS·최종 스냅샷/KMS를 새로 확인한다. 새 계정/프로필을 만들지 않는다.
2. 플랫폼 기반의 새 plan과 보존 대조·명령을 준비한다. **Terraform apply/destroy는 사용자 터미널에서 실행**한다. 종료된 이번 정리/과거 설치의 직접실행 위임은 새 설치에 재사용하지 않는다. RDS 복원/새 비밀번호 회전·operator 보존과 사용자 실행 후 실물 검증을 포함한다. [설치 런북](docs/runbooks/infrastructure-installation-and-validation.md)을 따른다.
3. 새 AI 저장소의 이미지 발행/OIDC 신뢰/private chart·실행기/CI/원복/collector 연결을 이번 설치와 함께 구체화·검증한다. 기존 repository literal을 일괄 치환하거나 새 대상의 권한 확대를 과거 예외로 자동 승인하지 않는다. ECR 이미지/digest·scan, 필요시 승인 workflow 빌드·등록을 확인한다.
4. 새 암호화 PVC(AI5Gi·ci-queue1Gi·collector1Gi), 권한·worker/relay·조회 경로와 현재 UID/endpoint/인증/binding을 준비한다. 격리 lab-target은 검증한 정상 revision에 수동 고정한다. 새 정상 CI artifact 출처·worker의 새 설정 로드/처리·기존5분 지표 보호를 확인하고 필요한 workflow만 다시 활성화한다.
5. 새 준비 결과·양쪽 독립 원복 감시를 확보한 뒤 새 실행별 승인을 받아 즉시 시작한다. 새 복구 PR 정확head의 실제 GitHub 사람 리뷰/CI 후에만 병합·동기화한다. 15초 시작·120초 리뷰·210초 리뷰마감·240초 소유원복·300초 독립원복·120초 회복관찰·자격/Secret 회수 조건을 유지한다.
6. 실환경 성공과 취소/중단/독립 원복 근거를 종합해 C-04/C-05 최종 정리한다. 연결 전환 후에만 플랫폼의 중복 AI 코드를 별도 PR로 제거한다. 이전 승인/토큰/ready hash·UID/사건 큐·plan을 새 실행에 재사용하지 않는다.

### 보존 위치와 Git 상태

- 최종 정리/state 복구·이미지OCI·최신AI자료·로그: `/Users/jounghyeon/.local/share/url-shortener-ai-ops/cleanup-20261112-after-ai-split/` (`CHECKSUMS.json`, `images.oci/`, `volumes/`, `state-recovery/`).
- 기존 bundle/DB/PVC·오프라인 환경: 같은 상위 폴더의 `continuation-20261112/`; 분리 bundle: `repository-separation-20261112/`; 과거 실습: `c04-20261112/`; 설치 원본: `install-20261112/`.
- 새 검증/병합 원본: `local-c04-validation-20261113/`, `pr1-integration-20261113/`; 이번 양쪽 인계 사본: `handoff-20261113-reinstallation/`.
- 원본 자격은 `/Users/jounghyeon/.local/share/url-shortener-install/credentials/`, App키/C02원본은 기존 `c03-app-keys/`·`c2-projection-identity-20261028/`에 유지한다. 원문/자격/state를 Git에 넣지 않는다.
- AI main 구현은 PR1로 병합됐다. 사용자의 후속 요청으로 **이 저장소의 HANDOFF와 2026-11-13 정리 결과 문서/JSON(3개), AI 저장소의 HANDOFF와 로컬검증/PR1반영 문서·assets JSON(5개)만 각각 한글 메시지로 로컬 커밋**한다. 원격 push는 하지 않으며 다른 기존 변경은 포함하거나 reset/삭제하지 않는다.
- 상세 결과는 별도 AI 저장소 `ai-ops/docs/C4-local-validation-20261113.md`와 `C4-pr1-integration-20261113.md`, 각 assets JSON에 있다. 코드 변경이 없다면 완료한 로컬 검증/병합을 처음부터 반복하지 않는다.

다음 작업 추천: **GPT-6 SOL · High**. **다음 실작업은 사용자의 재설치 요청 이후**다.

## 이하 과거 진행 기록

아래 기록은 당시 상태·승인·실패 복구 경위를 보존한 이력이다. “최신”, “실행 대기”, “위임 유효”라는 과거 표현이나 실행 명령은 현재 지시가 아니다. 현재 완료/보류 상태와 재개 순서는 위 현재 인계를 따른다.


## 2026-11-13 · 별도 AI 저장소 PR1 main 반영 완료

- 사용자가 요청한 PR1 정확head0997의 새CI5종 성공을 확인하고 일반squash로 main `69f99a08d25242b5d7774aacdc2844bb471f0997`에 병합했다. 병합후main CI43257778832 성공, 로컬main/검증트리 일치·미커밋문서 보존 확인. main은 비공개요금제의 보호기능미지원으로 protected=false이며 별도리뷰0건이다. 보호변경/관리자우회 없음.
- 상세: `/Users/jounghyeon/dev/url-shortener-ai-ops/ai-ops/docs/C4-pr1-integration-20261113.md`와 같은 assets JSON. 개인근거 `~/.local/share/url-shortener-ai-ops/pr1-integration-20261113/`. 다음은 별도저장소 integration 연결전환 계획이며 원본PR156·플랫폼code/권한·인프라는 변경하지 않았다. C04/C05실환경 완료는 아직 아니다. 다음추천 **GPT-6 SOL · High**.


## 2026-11-13 · 별도 AI 저장소의 C-04 로컬 재검증 완료

- `/Users/jounghyeon/dev/url-shortener-ai-ops` head0997에서 전체880개·집중15개/skip0·fixture4종·schema14개·구문287파일·저장소경계·고정운영chart Helm 검사를 통과했다. 구현 코드는 수정하지 않았고 인프라는 정리 상태를 유지한다.
- 012시각차2.009996초와 최대5초/기존마감 내 실제대기·미래요청미저장·동일투영 재조회·실패시중지를 로컬 검증했다. 호스트 시계 원인과 C04실환경 성공/C05최종완료는 아직 미확인이다.
- 상세는 별도 AI 저장소 `ai-ops/docs/C4-local-validation-20261113.md`·assets의 같은 이름JSON, 개인 로그는 `~/.local/share/url-shortener-ai-ops/local-c04-validation-20261113/`다. 다음은 별도 저장소 draft PR1 정확head/CI/리뷰 확인·반영과 integration 연결전환 계획이다. 이번에 원격PR/설정·인프라·클라우드/모델은 변경하거나 호출하지 않았다. 다음추천 **GPT-6 SOL · High**.


## 2026-11-13 · 인프라 정리 완료·다음은 별도 AI 저장소의 로컬 C04

- 이번 정리 위임으로 운영/AI Terraform managed 각각0개와 AWS 실물 부재를 검증했다. EKS/RDS/EC2/NAT/VPC/LB/ECR/SQS·기록 EBS10개/EIP1개 삭제 확인. 두state 잠금 없음. 프로젝트 잔여 CloudWatch로그1개도 로컬 원문 백업 후 제거했다.
- DNS전체·S3backend/state/과거버전·operator/로그인/정책·기존 RDS스냅샷6개와 새 **`urlshortener-postgres-final-snapshot-urlshortener-postgres-6962a7a0` available/encrypted**·그 KMS Enabled를 보존했다. EKS전용KMS키는 예약삭제 상태로 남으며 스냅샷키와 다르다. 새계정/프로필/권한부여 없음. 이번정리 예외는 종료됐고 다음 일반Terraform 실행 선호는 사용자터미널 원칙이다.
- 관리자 만료로 실패한 Primary삭제의 오류state131/원격130은 본인잠금/실행프로세스부재를 대조하고 정상statepush+리소스변경0개 새계획으로 복구(serial133)한 뒤 잔여100개를 정리했다. 옛plan/state를 다시 적용하지 않는다. 복구/최종실물 증거는 `/Users/jounghyeon/.local/share/url-shortener-ai-ops/cleanup-20261112-after-ai-split`에 있다.
- [완료 기록](docs/runbooks/infrastructure-cleanup-2026-11-13.md)·[기계 결과](docs/runbooks/infrastructure-cleanup-2026-11-13.json), 같은 개인폴더 `final-verification.json`·`state-final-verification.json`·`images.oci`·`volumes`·`state-recovery`·`CHECKSUMS.json`·`handoff`를 따른다. 기존 continuation/repository-separation/install 폴더·원본 자격/키는 유지했고 무결성·권한을 재확인했다.
- AI후속은 `/Users/jounghyeon/dev/url-shortener-ai-ops`에서 **인프라 없이 로컬 C04 검증/PR1 확인부터** 재개한다. C04실환경 성공·C05최종완료는 미완료다. 재설치 시 새스냅샷/비밀번호회전·이미지·PVC/UID/권한·정상CI/worker로드·새실행연결을 검증한다. 이전장애/리뷰/토큰/readyHash 재사용금지. 다음추천 **GPT-6 SOL · High**.


## 2026-11-13 · 재인증·state 복구 완료·운영 잔여100개 정리 진행

- 관리자 재인증 후 계정764428911194를 확인했다. Primary와 Replica는 모두 실제 부재이고 새 최종 스냅샷 `urlshortener-postgres-final-snapshot-urlshortener-postgres-6962a7a0`은 available·encrypted이며 연결 KMS키는 Enabled다.
- 중단 적용의 잠금 ID dc986bed-cd73-8d01-9649-e00c7dee5040은 소유자/호스트·시각/경로/버전·실행Terraform0개를 확인해 그 잠금만 해제했다. 동일lineage의 오류state131을 원격130에 **-force 없는 정상 state push**로 복원하고, 이미 삭제된 Primary를 **리소스변경0개**의 새 계획으로 정합화했다. 복구 완료 serial133·운영 managed100개. 상세 `state-recovery/recovery-complete.json`.
- AI managed0개는 유지된다. 현재 `tools/cleanup-production-20261113.py`가 전리전 Spot공용역할사용·스냅샷/KMS·현재state주소와보존범위를대조한 새 운영삭제계획을 검증·적용한다. 아직 운영 전체 삭제/최종검증 완료가 아니다. 완료 후 `tools/cleanup-final-verify-20261113.py`로 AWS잔여/state/DNS/operator/스냅샷/로그그룹·백업을 확인한다. 이번 정리 직접실행 위임은 유효하며 추가확인을 반복하지 않는다.


## 2026-11-13 · 관리자 세션 만료·Primary 삭제 후 state 복구 준비 (최신)

- 이번 정리 직접 위임으로 AI managed10개/ECR·Role3/access entry2 삭제와 state0을 검증했고, RDS 보호 해제1건·Replica1개 삭제도 완료했다. Primary는 `skip_final_snapshot=false`, 새 ID `urlshortener-postgres-final-snapshot-urlshortener-postgres-6962a7a0`로 삭제 요청이 AWS에 접수됐다.
- **UTC15:30:35(KST00:30:35) 관리자 토큰 만료**로 Primary 삭제 대기·S3 state 저장·잠금 해제가 실패했다. 실제 Primary/스냅샷 종료 상태는 재인증 뒤 확인해야 하며 아직 완료로 표시하지 않는다. 실패 적용을 바로 재실행하지 않는다.
- 작업 폴더 cleanup-20261112-after-ai-split의 `state-recovery/production-errored-serial131.tfstate`를0600으로 보존했다. 마지막 확인 원격state serial130·오류state131·동일lineage. `state-recovery/recovery-checkpoint.json`·`primary-destroy-apply.log`에 전체 근거가 있다. 기존 source/terraform/errored.tfstate도 유지했다.
- 사용자에게 기존 로그인 선호대로 본인 터미널 `aws login --profile urlshortener-new --region ap-northeast-2` 재인증을 요청했다. 새계정/프로필/권한부여 없음. 인증 완료 후 root계정764428911194 확인 → 실제Primary/스냅샷 확인 → 원격state/lock의 ID·소유자·시각·현재Terraform 프로세스 대조 → 본 실행의 중단된 잠금만 정리 → lineage/serial 검증 후 오류state의 정상push 또는 최신state와대조 복구 → 새계획으로DBstate 정합화 → 스냅샷available 확인 → 운영잔여destroy로 이어간다. 잠금삭제/state강제덮어쓰기/이전plan맹목재적용 금지.
- 남은 실행기는 `/private/tmp/cleanup-production-20261113.py`, `/private/tmp/cleanup-final-verify-20261113.py`로 준비만 했다. RDS 스냅샷 검증 파일이 없으면 실행하지 않으며 아직 운영EKS/NAT/VPC가 남아 있다. 임시파일은 이어서 개인 tools 폴더로 보존한다. 이번 정리 위임은 유효하므로 재인증 뒤 추가 실행 확인을 반복하지 않는다. 기존 DNS/스냅샷/state/operator·로그인·소스/백업 보존 정책은 유지한다.


## 2026-11-13 · 이번 정리 Terraform 직접 실행 위임

- 사용자가 직전 사용자 터미널 실행 요청에 “너가 해줘”라고 명시해 이번 정리의 남은 apply/destroy를 Codex에 위임했다. 아래 사용자 적용 대기는 해제됐다. 기존 AI plan10건을 재검증·적용한 뒤 RDS 보호 해제/Replica 삭제/Primary 최종 스냅샷·운영 삭제까지 이어간다. 기존 보존 대상·검증은 유지하며 다음 작업의 일반 Terraform 실행 선호는 변경하지 않는다. 진행 근거는 cleanup-20261112-after-ai-split에 남긴다.

## 2026-11-13 · 클러스터 선행 정리 완료·사용자 AI Terraform 삭제 적용 대기

- 사용자 전체 정리 요청으로 앱/AI/실습/모니터링·Traefik·Argo·LB Controller와 비기본 namespace를 정리했다. **최초 기록 EBS7개/PV 실제 부재·VPC LB/TG/NLB ENI/k8s SG0·기본 namespace4·Helm0**을 확인했다. EKS/CSI·Managed Node Group·RDS·NAT/VPC 등 운영 Terraform 자원은 아직 남아 있다. 전체 정리 완료가 아니다.
- DNS 레코드·operator/기존 로그인·기존 스냅샷·state bucket 버전 관리·로컬 원본을 보존 검증했다. 새로운 계정/프로필/권한/로그인 없이 기존 관리자 세션을 사용했다. 두 state 원본 백업·현재 이미지 OCI108,718,513바이트·최신 AI 자료를 개인 폴더에 보존했다. 배포 workflow6개 동결, 오래된 Argo Secret/서버 변수만 제거했으며 GitHub 저장소/App/다른 설정은 보존했다.
- 작업 폴더 `/Users/jounghyeon/.local/share/url-shortener-ai-ops/cleanup-20261112-after-ai-split/`. 실제 설치 source Git63e220e와 Terraform 파일 일치·두 backend를 검증했다. **AI 계획0생성/0변경/10삭제/0교체**, 전용ECR1/lifecycle1/Role3/inline3/access entry2. operator·DNS·RDS·state·production OIDC 삭제 없음. 사용자 실행 스크립트 `USER-APPLY-AI-DESTROY.sh`를 작성·읽기 전용 guard/구문 검사했으며 **Codex는 apply/destroy를 실행하지 않았다**.
- 다음은 사용자가 `sh /Users/jounghyeon/.local/share/url-shortener-ai-ops/cleanup-20261112-after-ai-split/USER-APPLY-AI-DESTROY.sh`를 자기 터미널에서 실행한 결과를 받는 단계다. 이후 AI state/AWS 부재 검증 → RDS 보호 해제 새 plan/사용자 apply → Replica 삭제 → Primary 최종 스냅샷/운영 destroy → 보존/잔여 과금 검증으로 이어간다. RDS 두 개는 available, Primary 보호true다. **새 최종 스냅샷은 아직 생성 전**이다.
- [상세 진행 기록](docs/runbooks/infrastructure-cleanup-2026-11-13.md)·[기계 판독 결과](docs/runbooks/infrastructure-cleanup-2026-11-13.json)를 따른다. 재설치 이미지 사본은 새 작업 폴더 images.oci이고 최종 RDS snapshot ID는 이후 실물 확인 후 확정한다. AI 작업 위치/PR1 미병합/C04·C05 미완료는 아래 인계대로 유지한다. 다음 추천 **GPT-6 SOL · High**.


## 2026-11-12 · AI 전용 저장소 분리 (최신 작업 위치)

- 앞으로 AI 구현·시험·전용 배포 코드 변경은 **비공개 [joungGo/url-shortener-ai-ops](https://github.com/joungGo/url-shortener-ai-ops)**, 로컬 `/Users/jounghyeon/dev/url-shortener-ai-ops`에서 한다. 그 저장소의 HANDOFF.md와 integration/README.md를 먼저 읽는다. [분리 결과와 연결 계획](docs/runbooks/ai-repository-separation-2026-11-12.md) 참조.
- AI 경로의 저작자·날짜·이력을 보존해 추출했다. 새 main `fb4ecb22c2c39ce5852a69c39f7c1776964f899f`는 원본 main9573 기반이고, PR156/e327의 미병합 변경은 **[새 초안 PR1](https://github.com/joungGo/url-shortener-ai-ops/pull/1)** / head `0997a96142ca37e8195b9574c7766b095ac81753`에 보존했다. 원본 PR156은 OPEN 그대로이며 승인·병합하지 않았다. 기존 root AI 미커밋 변경은 archive/root-working-copy-20261112 브랜치에 별도 보존했다.
- 독립 main 로컬870개·이관 브랜치880개·배포/fixture/schema 정적 검사 통과. 새 초안 PR1의 정확한 head0997에서 GitHub CI5종 모두success(실행43241547898). 운영 workflow는 새 저장소의 비실행 fixture로 분리하고 실환경 workflow는 기존 repository identity 조건으로 차단했다. **Secret·OIDC·App 권한·실제 실행 연결은 아직 이전하지 않았다.** 기존 플랫폼 AI 복사본은 정리와 연결 전환까지 보존한다.
- 기존 `continuation-20261112/`와 자격·DB/PVC·실습 근거는 그대로 보존한다. 분리 검증/새 bundle은 별도 `~/.local/share/url-shortener-ai-ops/repository-separation-20261112/`에 둔다. 원문 사건·키·state/plan은 GitHub에 올리지 않았다.
- 이 작업은 코드 관리 분리이며 **인프라 삭제·C04/C05 완료가 아니다**. 인프라 정리는 이 저장소의 런북으로 최신 state/권한 확인 후 진행하고 Terraform apply/destroy는 사용자 터미널에서 실행한다. 기존 보존 정책을 유지한다. 다음 추천 **GPT-6 SOL · High**.


## 2026-11-12 · 실습 중단·인프라 정리 전 재개 자료 보존 (최신 지시)

**사용자가 추가 실습을 중단하고 정리 전 보존을 요청했다. 아래 과거 절의 실행·승인 대기는 이 지시로 중단한다. PR #156 병합 예외는 승인되지 않았으며 옛 helper를 실행하지 않는다. 이번 작업은 보존 준비만 완료한 것으로, 인프라 삭제 완료가 아니다.**

- 상세 인계: [정리 전 보존·재개 런북](docs/runbooks/ai-ops-continuation-2026-11-12.md). 체크포인트 `/Users/jounghyeon/.local/share/url-shortener-ai-ops/continuation-20261112/`에 구현/실습 Git bundle, 기존 root WIP, 임시 요청 DB 10개, 설치 입력, AI PVC 백업, wheelhouse 및 검증 기록을 보존했다. 원문·자격 포함 개인 자료는 공개하거나 Git에 추가하지 않는다.
- 최신 구현은 `.worktrees/c05-final-report` / `codex/c04-request-time-20261112` / `e32744c7710fcc5f0dbcc193fd93204eca47846b`다. [PR #156](https://github.com/f-lab-edu/F-Lab-DevOps/pull/156) OPEN·미병합, CI 4종 통과. 운영 main은 `9573b8c9f323a643e65bb58c323dc932c681a6b2`이며 리뷰 1명·관리자 적용 유지. **C-04 실환경 성공·C-05 최종 완료는 미완료다.**
- 구현 bundle을 별도 `restored-source/`에 복원하고 고정 wheel 13개로 오프라인 의존성 설치·pip check·시각 경계 집중 10개·전체 880개 시험을 통과했다. `/private/tmp` venv나 스크립트에 의존하지 않는다. 이 wheelhouse는 macOS arm64/Python 3.13용이다.
- AI PVC `ai-ci/ci-queue`, `ai-ops-lab/lab-collector-source`, `ai-ops/data-ai-ops-0`의 파일 125개·SQLite 4개를 백업해 해시/quick_check를 검증했다. `ai-model/model-ledger`는 Pending·할당 PV 없음·실행 Pod 없음이다. 과거 큐/승인/사건은 역사 자료로만 보존하고 새 worker 활성 큐로 복원하지 않는다.
- 23:21 KST 독립 재조회에서 정상 revision `0cfef83c36578090dfc1b65513789b1b10159e03`·Healthy/Synced·Ready 1·`/readyz`/`/items` HTTP 200·빈 Lease·제한 Argo 토큰/원복 Secret 회수·원격 감시 종료를 확인했다. `execution-closed-readonly.json` 참조. 현재 격리 Git main과 정상 revision은 다르므로 재설치 때 실습 main을 자동 동기화하지 않는다.
- AI 원격 Terraform state는 GetObject 전후 VersionId/ETag 일치로 로컬 백업했다. **운영 state 및 RDS 조회는 operator 권한 거부로 미확인**이다. 실제 정리 plan 전에 권한과 최신 state/실물을 대조한다. 옛 state를 삭제 후 빈 state 위에 복원하거나 옛 plan을 실행하지 않는다.
- `/Users/jounghyeon/.local/share/url-shortener-install/credentials/` 7개, `c03-app-keys/` 2개, `c2-projection-identity-20261028/` 3개 원본을 기존 0600 경로에 보존했다. 기존 `c04-20261112/`, `install-20261112/`, 작업트리·GitHub 저장소/App·리뷰 이력도 보존한다. 다음 세션은 인프라 없이 체크포인트 검증과 시각 경계 로컬 시험부터 시작한다.
- 남은 순서: PR156 재확인/정상 반영 → 현재 권한·backend 확인 및 정리/설치의 새 plan → 사용자 터미널 apply/destroy → 새 클러스터/PVC/이미지/UID/인증/binding·정상 CI/worker 로드 → 새 실습 준비/실행별 승인/새 PR 정확 head 사람 리뷰 → C-04/C-05 최종 근거 정리. A5/A7 미측정·B 합의 범위·AI 포트폴리오 보류는 유지한다.
- 실제 정리는 아직 실행하지 않았다. 기존 정책의 Route53·RDS 최종 스냅샷·S3 backend/state·`urlshortener-operator`와 로그인 자격을 보존한다. **Terraform apply/destroy는 사용자 터미널에서 직접 실행한다.** 다음 추천 **GPT-6 SOL · High**.


## 2026-11-12 23:06 KST · 012 시각 순서 보완880시험·PR156 준비

- `.worktrees/c05-final-report` 브랜치를 **codex/c04-request-time-20261112**로변경했다. 현재head **e32744c7710fcc5f0dbcc193fd93204eca47846b**, 앞코드커밋d29f534와src/scripts/tests동일. [PR156](https://github.com/f-lab-edu/F-Lab-DevOps/pull/156). **전체880/28.488초·집중22·diff/문서링크/JSON/19개test method대조 통과**. 근거 `/Users/jounghyeon/.local/share/url-shortener-ai-ops/c04-20261112/c04-request-time-e32744c`. 원문사건/자격게시없음.
- create_request는현재UTC보다미래imageFailure로요청을저장하지않는다. 새 `c_trial_request_time.py`는기존안내마감내/전체5초한도에서실제대기하고투영사본+새조회동일성확인후한번요청생성을가능하게한다. future>5초/기한/시계jump/조회실패/투영변경은거절. 실제소켓에서미래요청0건·시각경과뒤요청1건을검증했다. proposal-inputs.json에첫/갱신입력보존·post helper안전한거절프레임추가. 시각소급/시계수정/검증완화/쓰기재시도없음. **15/210/240/300/120기한유지**.
- README/C5문서/JSON에기존published controls/최신880로컬보완·012정상원복·실제/모의/미측정을반영했다. C완료flags=false. 마지막JSONcommit은새880검증소스를이전PR155head와혼동하지않도록파일sha256출처로구분한문서수정이며코드변경없다. 최종e327head의 검사 scope/contracts/image/C03 Main PR Gate **4종success**·작성자=현재사용자·main리뷰1/enforce_admins/strict유지를실제원본으로대조했다. ci-final/checks-final/protection-before-decision.json에보존했다. **이번PR156만 임시리뷰1→0/squash/즉시1복원 사용자승인을질문하고즉시응답종료한다. 실제새답변전merge script실행금지**.
- `/private/tmp/c04-merge-pr156.py`는현재head/base9573·최신검사4종·전체보호동일성·finally즉시복원·API40초timeout으로준비만했다. **PR156만의별도사용자승인전실행금지**. main은아직9573/PR155보호복원유지다. 검사4종통과확인후이번PR한정리뷰1→0/squash/즉시1복원승인질문뒤즉시응답종료한다.
- 이후새main/새normalCI현재Pod/binding/worker·5분지표보호→새013준비/실제readyhash승인→새복구PR정확head사람리뷰→C-03/C-04 120초/회수→최종CI·worker/최종정리. **012/PR155/PR15승인재사용금지**. 추가원격장애/자격발급없음. 012실패의정상/HTTP/회수와실제관측35.386910>요청33.376914(2.009996초)/다른14조건일치는아래근거참조. 추천 **GPT-6 SOL · High**.

## 2026-11-12 22:55 KST · 012 사건 접수 뒤 요청/관측 시각 역전 확인·정상 원복/회수

- 사용자012승인을 helper로검증해 **22:46:51.780 KST** 한 번 기록. **T22:46:56.638**·리뷰22:50:26.638·소유22:50:56.638·독립22:51:56.638. 실제주입 **22:47:11.389(T+14.751초)**로15초시작예산통과, 새실패Pod·잠금handoff/조사Job/인증된사건/요청까지연결됐다. 새PR생성전 `fresh_c02_proposal_recheck`에서 **fresh_proposal_not_ready**로중지해소유정상원복. result=restored_without_c03_completion·fault_injected=true·confirmed inject_set/inject_sync/rollback·pending=null·capture_job/ready_control삭제. **새PR없음/C-03·C-04완료아님**.
- `012/live/proposal-blocked.json` 전체확인: proof_ready=true·incident_ready=false·blockers=[initial_incident_proof_not_ready]. 요청원본SQLite를mode=ro/기존worker사건투영읽기로대조한 `readonly-binding-diagnosis.json`: **다른14조건모두true**, **observed_before_requested=false**. 관측 **22:47:35.386910**, 요청 **22:47:33.376914**, 차이2.009996초. bundle/report/failure_binding/Pod/Deployment/image/근거해시모두일치. 보존요청보다관측시각이뒤여서정책이거부하는 차단원인을확인했다. 어느호스트시계문제인지는직접측정전단정금지. 새쓰기없는helper `/private/tmp/c04-readonly-012-binding.py`; 투영도private `readonly-projection.json` 보존. **재현/진단자료를실행증거로재사용금지**.
- 담당61868종료·cleanup_complete/remote_stopped/secret_removed·토큰revoked·host11742자동회수확인. 독립 `/private/tmp/c04-close-012-readonly.py` **22:52:01 KST**: 정상revision/image/ready1·HTTPreadyz/items200·빈Lease·제한토큰/환경Secret/remote회수확인. `012/execution-closed-readonly.json`. 추가장애/원복PR/자격발급없음.
- 다음 로컬보완: `create_request`에서미래image_failure.observed_at요청생성을거부하고, `prepare_c4_recovery_pr.py`에서실제현재시각이관측시각에이를때까지만 **최대5초/기존안내마감내** 제한대기·동일투영재확인후단한번요청생성. 시각검증완화/시계조정/소급요청/기한확장/불명확쓰기재시도금지. 원래두recheck입력도private진단에저장해첫blocker가덮여소실되지않게한다. 아직코드수정전이다.
- 로컬작업트리 `.worktrees/c05-final-report`(main9573기준)에는최신README/C5문서/JSON **3개미커밋수정**이있다. C완료flags=false. 여기서새시간경계보완/회귀검증을진행하고최종scope에맞는codex브랜치/PR로정리한다. 기존`.worktrees/c05-completion` head67(PR155merged)는보존. 새PR/main검증후에만새기준선/준비/실행별승인/새PR리뷰를진행. **012/PR155/PR15승인재사용금지**. 추천 **GPT-6 SOL · High**.

## 2026-11-12 22:45 KST · 012 현재 준비 완료·실행 승인 대기

- actual ready **prepared22:45:12.153·expires22:50:12.153 KST**, intent **511f5b3b1963e8850720be25895edbe451c800b6a1de878ffec8ff0f2a7adfea**, readiness **e4bcf2961132c9d055399b13cbca8a8a1fb19a63ae9039fca843b52f379a2a9e**. main9573/676파일·870시험·정상CI43228160180/workerready1·양쪽실제감시/FIFO/자동회수준비완료. 사용자질문뒤즉시응답종료. 아직012 approval/activation/주입없음.
- 실제012 승인뒤에만 venv로 `/private/tmp/c04-record-ready-approval.py --trial 202611120012 --intent-hash 511f5b3b1963e8850720be25895edbe451c800b6a1de878ffec8ff0f2a7adfea --readiness-hash e4bcf2961132c9d055399b13cbca8a8a1fb19a63ae9039fca843b52f379a2a9e --execute-after-actual-user-approval`를 실행한다. helper는해시/만료전/실제PID·guard/런타임/GH신원을재확인해현재UTC를한번기록한다. 만료/다른답변이면실행금지.
- **담당61868·host48662/PID11742·회수76637/PID11804·localPID11714·runnerPID12573·remote43230005629**. O=/private/tmp/c04-202611120012. 새PR stdout질문을최우선실제async전송→accepted후기본sandboxFIFO에실제응답→notice120초검증→바로응답종료. 이전011/PR15/PR155승인재사용금지. 이후C-03/C-04/회수/최종CI·worker/최종문서로이어간다. 추천 **GPT-6 SOL · High**.

## 2026-11-12 22:45 KST · 012 양쪽 감시 실제 준비·현재 영수증 생성 중

- main9573/676파일/870시험·정상CI43228160180 success/원본artifact·workerready1·binding e0080589…·설정로드/앱·운영·PVC보존 확인 후 not-before22:42:28.265를 지키고 fresh이미지/EKS/실습검증 통과. intent **511f5b3b1963e8850720be25895edbe451c800b6a1de878ffec8ff0f2a7adfea**.
- **담당61868·FIFOhost48662/PID11742·자기호스트회수76637/PID11804·localguardPID11714·runnerPID12573**. 원격 **43230005629/job131222512196**, 실제GitHubUI환경승인/ready JSON확인. remote guard `remote:43230005629:88ccf928df694a6da80c94181db7e659`, prepared22:44:35.900·standby만료22:59:35.900 KST. `012/standby/remote-ready-observed.json`, `/private/tmp/c04-ui-proof/c04-012-guard-approved.png`. 현재012 실제사용자실행승인/approval/activation/주입은없다. 45분lab-target전용토큰/동일환경원복Secret은담당finally회수한다.
- 실제ready hash가 나오면새실행승인질문후즉시응답종료. **현재실제사용자승인뒤에만** `/private/tmp/c04-record-ready-approval.py --trial 202611120012 --intent-hash <질문intent> --readiness-hash <질문ready> --execute-after-actual-user-approval`를venv로실행한다. 이 helper는현재두hash/만료전/실제GH149856960/PID/양쪽감시/런타임검증후한번만기록하며 **아직실행안함**. AI_OPS_FILES_ROOT는전용O로고정해이전P경로오류를반복하지않는다.
- O=/private/tmp/c04-202611120012. 실제시작후 **host-requests.jsonl만최우선8초이하poll**→정확한새PR질문실제async accepted→기본sandbox FIFO host-response.fifo에실제응답JSON+newline write→review-notified>=120초확인→즉시응답종료해사람리뷰시간확보. 그전다른로그/CUA로ACK를늦추지않는다. C-03뒤담당이양쪽guard먼저종료→C-04최소120초관찰/회수. 실패시result.rejection_source_frames/고정코드를읽어 무리한재실행없음.
- 최종보고서작업트리 `.worktrees/c05-final-report`·branch `codex/c05-final-report-20261112`에main9573/R1~R8/870시험·정상CI와worker·README역사기록구분을로컬갱신했다. **실환경/최종완료flags=false 유지·커밋/게시전**. 이후실제성공/최종normalCI·worker 확인뒤마지막결과반영/PR. PR155병합승인/011실행승인/PR15리뷰재사용금지. 추천 **GPT-6 SOL · High**.

## 2026-11-12 22:31 KST · PR155 승인 병합·보호 복원·012 새 기준선 진행

- 이번 PR155 한정 사용자 승인으로 head67afeea/basec969/최신검사4종/전체보호를재확인해 **22:29:35 KST squash 병합**, main **9573b8c9f323a643e65bb58c323dc932c681a6b2**다. **22:29:38 KST 보호전체JSON 원본일치**로 리뷰1/enforce_admins/strict/나머지복원확인. 근거 `~/.local/share/url-shortener-ai-ops/c04-20261112/c05-start-budget-67afeea/merge-proof/`. 기존operator STS계정764428911194/IAMurlshortener-operator 유효.
- 새 `preparation-202611120012/runtime`은 main9573과검토head67의ai-ops/.github동일성·**676파일** 해시·비밀값미포함을검증했다. source-ready.json존재. helper `/private/tmp/c04-012-baseline.py`·`c04-align-baseline-012.py`·`c04-prepare-012-inputs.py`·`c04-final-prep-012.py`·`c04-ready-012-standby.py`를새ID/현재main으로준비했다. **baseline session99083 exit0** 현재Pod한정binding/RBAC·worker/relay로드·앱/운영/PVC보존 검증 완료. 새 정상CI **43228160180** 실제GitHubUI ai-ops-normal-ci 승인 완료(근거 `/private/tmp/c04-ui-proof/c04-012-normal-approved.png`). 입력11889 exit0/C02현재worker한정1시간접근 준비. **최종prep session83346**: 정상CI43228160180 success/원본artifact검증·worker실제ready영수증1건(22:39:06 KST, binding executor e0080589…) 확인완료. `012/worker-normal-ci-verified.json`. 지표 not-before **22:42:28.265 KST**를 유지한뒤 fresh현재조건/새intent를준비한다. 최종문서용 `.worktrees/c05-final-report`·branch `codex/c05-final-report-20261112`를main9573에서 만들었고 published controls/README의역사적상태표현만로컬갱신, 실환경완료flags=false 유지·커밋/게시전이다.
- 다음 정상CI 실제GitHubUI환경승인/성공artifact·workerready확인→현재C02한정자격·fresh이미지/조건→정상CI뒤5분지표보호→양쪽guard/FIFOhost/자동회수준비→actualreadyhash 새실행승인질문후즉시응답종료한다. PR155병합승인/011실행승인/PR15리뷰는새장애나새PR리뷰대체불가. Terraform/유료모델/새장애없음. C-04/C-05완료미판정. 추천 **GPT-6 SOL · High**.

## 2026-11-12 22:25 KST · 011 무주입 중지·기동 예산 거절 확인·추가 보완870시험 통과

- 011의 두hash/만료전·GH149856960·현재PID/양쪽guard/런타임을 재검증해 **22:16:09.433 KST** 실제승인을 기록했다. 첫 기록시 AI_OPS_FILES_ROOT=P(런타임부모)설정이 경로정책에 거절돼 파일/주입이 없었고, 기존전용O설정으로 재검증후 한 번 기록했다. **T22:16:15.114**, 리뷰22:19:45.114·소유22:20:15.114·독립22:21:15.114였다.
- 실제 결과 **stopped·fault_injected=false·ValueError·소스프레임 c_trial_execution._inject:222**. 15초 기동 직전 예산 조건에서 거절됨을 현재소스와 대조했다. 장부events=[]·confirmed={}·pending_operation=null, 새PR없음. 현재승인재사용/자동재시작금지다. 담당25757종료·cleanup_complete/remote_stopped/secret_removed=true·토큰revoked·host93285자동회수확인. 독립조회1회도구오류후 STSoperator정상·직접AppHealthy/Synced를 확인하고 같은읽기검증 재시도로 **22:20:04 KST HTTP2경로200·정상revision/image/ready1·빈Lease·토큰/Secret/remote회수**를 확인했다. `011/execution-closed-readonly.json`. C-03/C-04완료아님.
- 새 작업트리 `.worktrees/c05-completion`·브랜치 `codex/c05-completion-20261112`·한글커밋 **67afeea00035649039806b529bab7836408c585a**. **원격run/jobs 두독립GET만 병렬화**, 로컬소스/PID/marker 선행·Lease 후속새조회/신원·activation·소유권·양쪽ACK 검사는유지한다. 안전검토에서 Lease를원격조회와동시수집하면중간소유권변경을놓치는 회귀를 재현해 그 병렬화는제외했다. 해당회귀와원격동시조회검증 포함 **집중35·전체870/26.483초 통과**. 15/210/240/300/120초기한변경없음. 근거 `/Users/jounghyeon/.local/share/url-shortener-ai-ops/c04-20261112/c05-start-budget-67afeea`.
- 읽기전용 종료run의고정두GET 각3회중앙값 직렬1.0201초·병렬0.5817초, 근거 `011/readonly-remote-read-latency.json`. 전체기동성공/새복구증거아니다. C-05문서/JSON에 R1~R8/19개통과메서드·실제/모의/미측정·완료대기를정리했다. [PR155](https://github.com/f-lab-edu/F-Lab-DevOps/pull/155) head67afeea의 검사 scope/contracts/image/C03 Main PR Gate **4종 모두success**를 실제원본으로대조했다. main은아직c969(이전PR154보호복원유지)·작성자=현재사용자·리뷰1/enforce_admins/strict 유지다. checks-final/protection-before-decision/ci-final JSON에보존했다. **PR155만 리뷰1→0 임시변경/squash/즉시1복원 승인을질문하고즉시응답종료한다. 실제답변전 /private/tmp/c04-merge-pr155.py 실행금지**다. 스크립트는현재head/base/최신CI/전체보호동일성/finally복원/40초timeout으로준비만했다.
- 다음 새PR검사4종확인→정확한새PR한정리뷰/임시변경승인→main반영/보호복원→새정상CI·worker/5분지표보호→새준비/새장애실행승인→새PR리뷰→C-03/C-04/최종CI·C5정리. **011·PR15·PR154승인 재사용 금지.** Terraform/유료모델/추가장애없음. 사용자시간질문에 처음15~25분은011성공조건부라고답했으나무주입중지뒤수정필요를알렸다. 현재추가반영/CI/새실습이필요해 확정종료시간단정금지. 사용자에게새실습성공조건부25~40분+승인대기로수정안내했다. 참고: 현재 run_c4_ready_trial.py는 normal_head를workflow_main으로고정하고 warm_trial_reads도이를검증하므로 새main에서구normalCI43223088012를암묵재사용하지않는다. 새기준선CI와5분지표구간을계획에포함한다. 추천 **GPT-6 SOL · High**.

## 2026-11-12 22:13 KST · 011 현재 준비 완료·새 실행 승인 대기

- 실제 ready **prepared22:12:58.723·expires22:17:58.723 KST**, intent **4fb1c0536b7495f4b9e9a2dff544864cbfe54b13dadd7dfe62bd9c473ee8418d**, readiness **b690e063a927dfaa9863869645f27b26c88a781aed3e7d12d8c382955ffccfa5**. mainc969/675파일·868시험·정상CI43223088012/workerready1·양쪽실제감시/FIFO호스트/자동회수준비완료. 사용자질문후즉시응답종료한다. 아직011 approval/activation/주입없음.
- 실제새승인수신시 만료전/현재두hash/GH149856960/정확한자기PID·양쪽guard/런타임을재확인해 현재UTC 실제ReadyApproval만기록한다. **담당25757·host90171/PID93285·회수7062/PID93396·localPID93318·runnerPID94591·remote43224853148**. O=/private/tmp/c04-202611120011. 새PR은 host-requests.jsonl최우선→실제async accepted→기본sandbox FIFO실제응답→notice>=120초확인→즉시응답종료해사람리뷰시간확보.
- 실행전조건실패/만료시주입없이담당이회수한다. 이전010/PR15리뷰/PR154병합승인재사용금지. C-03뒤양쪽감시종료/120초C-04관찰·회수→최종정상CI/worker→C-05초안(.worktrees/c05-completion)결과반영·최종판정. 현재 C-04/C-05 완료는 아니다. 추천 **GPT-6 SOL · High**.

## 2026-11-12 22:13 KST · 새 main011 양쪽 감시 실제 준비·새 실행 승인 전

- PR154 병합 mainc969/675파일 실행묶음, 정상CI43223088012 success·worker ready1/executor e0080589…·설정reload/앱·운영·PVC 보존 검증 완료. 지표 not-before22:09:47.615 준수 후 fresh 현재 EKS/ECR/실습 사전 검증 통과했다. 011 intent **4fb1c0536b7495f4b9e9a2dff544864cbfe54b13dadd7dfe62bd9c473ee8418d**.
- **담당session25757·FIFO host90171/PID93285·자동회수watcher7062/PID93396·local guardPID93318·runnerPID94591**. 원격 **43224853148/job131205308246** 실제 GitHubUI 환경승인/준비JSON 확인. remote guard ID `remote:43224853148:e69ef9ec54de4113a616331d60ab4743`, prepared22:12:10.976·standby만료22:27:10.976 KST. 실제영수증 `011/standby/remote-ready-observed.json`, UI이미지 `/private/tmp/c04-ui-proof/c04-011-guard-approved.png`.
- 현재 runner가 실제ready 영수증을 만드는 중이며 **011 사용자 실행승인/approval.json/activation/장애 주입은 아직 없음**. 토큰45분·lab-target get/update/sync만, 같은 GitHub환경 원복Secret 전달·담당finally회수. 기존 AI구현 전 자격 예외 범위다. O=/private/tmp/c04-202611120011, P=~/.local/share/url-shortener-ai-ops/c04-20261112/preparation-202611120011.
- ready hash가 생기면 새실행승인 질문→즉시응답종료. 실제승인 뒤 현재UTC/만료전·정확두hash·GH149856960·자기프로세스/양쪽감시/런타임을 대조해 실제approval만 기록한다. 새PR 안내는 O/host-requests.jsonl을최우선poll→정확질문실제async accepted→기본sandbox FIFO에 actual응답write→notice최소120초 확인→즉시응답종료. 그전다른로그/CUA조회로ACK늦추지않는다.
- C-03후 담당이 양쪽guard먼저종료→C-04최소120초관찰/회수. C-05초안은 `.worktrees/c05-completion`에 R1~R8/19개 통과 메서드·868개 검증·범위/한계로 준비했으며 최종완료false. 새 실제성공/정상CI/worker 확인 뒤 최종 문서/목록/인계를갱신한다. PR154승인/PR15리뷰/010승인은새장애승인대체불가. 추천 **GPT-6 SOL · High**.

## 2026-11-12 21:59 KST · PR154 승인 병합·전체 보호 복원·011 기준선 준비

- 사용자의 이번 PR154 한정 승인으로 head fb3bfa9의 검사4종과 base509를 재확인해 **21:58:11 KST squash 병합**했다. main **c969b8e645a2b0a4d5a3f2e7e948bc6dcf870436**. **21:58:13 KST 전체 보호 JSON 원본 일치**로 리뷰1/enforce_admins/strict/나머지 설정 복원을 확인했다. 근거 `~/.local/share/url-shortener-ai-ops/c04-20261112/c04-observation-stability-fb3bfa9/merge-proof/`.
- 기존 operator STS는 목표 계정764428911194의 `urlshortener-operator` IAM 사용자다. AWS 문서 도구는 현재 없음; 기존 aws-containers 스킬/eks/ecr 참조와 CLI를 사용했다. 새 로그인/프로필 생성 없음.
- 새 `preparation-202611120011/runtime`은 공개 Git main c969와 fb3 검증소스 ai-ops/.github 동일성·675파일 해시를 검증했다. 개인 자격 미포함. 준비 helper `/private/tmp/c04-011-baseline.py`·`c04-align-baseline-011.py`·`c04-prepare-011-inputs.py`·`c04-final-prep-011.py`·`c04-ready-011-standby.py`를 새ID/현재main으로 준비했다. **baseline session73403 exit0**·worker/relay reload/앱·운영·PVC spec/UID 보존 검증 완료. 새 정상CI **43223088012**(main c969) dispatch 후 실제 GitHub UI ai-ops-normal-ci 승인을 완료했다. UI근거 `/private/tmp/c04-ui-proof/c04-011-normal-approved.png`. 입력 session53122 exit0·현재 worker한정 C02접근 준비완료. **최종 prep session21536**: 정상CI43223088012 success/원본artifact 검증과 worker ready영수증1건 확인 완료(`011/worker-normal-ci-verified.json`). 지표 not-before **22:09:47.615 KST**를 유지한 뒤 새 intent를 준비한다. C-05 초안 작업트리 `.worktrees/c05-completion`·브랜치 `codex/c05-completion-20261112`를 c969에서 새로 만들고 R1~R8 파일/19개 실제 통과 test method 매핑과 최신868개 근거를 로컬 정리했다. 실환경/최종완료 flags는 false·문서는 대기이며 아직 커밋/게시하지 않았다. Terraform/유료 모델/새 장애 실행 없음.
- 다음 baseline 완료→실제 GitHub UI ai-ops-normal-ci 승인→원본CI/worker영수증→현재C02접근/이미지/준비→최소5분 지표보호→양쪽감시 실제준비/FIFO호스트/자동회수→현재ready hash 새실행승인. 새질문 뒤 즉시 응답종료해 승인시간 확보한다. 새복구PR 정확head의 기한내 사람리뷰/CI는 별도이며 PR15/010승인은 재사용하지 않는다. C-03/C-04 실환경 완료는 대기다. 추천 **GPT-6 SOL · High**.

## 2026-11-12 21:39 KST · 실제 CRD 상태 갱신 확인·최종 PR154/868개 시험 통과

- PR15 기한 내 정확한 head 실제 리뷰/CI는 확인됐지만 010 입력 거절로 정상 원복되어 미병합이다. 독립 정상/HTTP200/빈 Lease/양쪽 토큰·Secret·감시 회수 검증은 `preparation-202611120010/execution-closed-readonly.json`. C-03/C-04 실환경 완료는 여전히 대기다.
- 새 읽기 근거: 실제 Application CRD `v1alpha1.subresources={}`여서 status 갱신도 generation을 올릴 수 있다. 정상 앱 자동 갱신에서 **generation225→226·reconciledAt12:34:13Z→12:36:15Z·나머지 원본 동일**을 확인했다. 근거 `010/live/readonly-status-generation.json`. 따라서 이전 21:32 기록의 generation 완전 일치 유지 방침은 보완했다. RV/시간의 일관된 전진과 나머지 원본 전체 일치에 한해 양의 정수 generation+1만 허용한다. 복수 증가/감소/타입/단독 변경은 거절하며 managedFields/UID/spec/나머지 status/Deployment/Lease 전체·values·모든 승인·CI·기한/신선도는 유지한다. 010 당시 내부 프레임은 없어 과거 원인 확정은 하지 않는다.
- 최종 branch `codex/c04-observation-stability-20261112`, 작업트리 `.worktrees/c04-observation-stability`, head **fb3bfa9f5cbc9a964d7c737f0f4a0e4e7464e014**, [PR154](https://github.com/f-lab-edu/F-Lab-DevOps/pull/154). 실제 bound gate/단일 상태 갱신·거절 경계/안전 프레임과 진단 SQLite 연결 종료를 구현했다. 867개 전체 시험 2회에서 기존 진단 stderr JSON 파싱 실패를 기록했고, 진단 임시 DB 연결 미종료를 수정·성공/예외 종료 시험 후 **868개/30.896초 모두 통과**했다. 다른 기존 ResourceWarning은 전체 로그에 남아 있다. 배포 정적 검사/diff 검사 통과. 근거 `/Users/jounghyeon/.local/share/url-shortener-ai-ops/c04-20261112/c04-observation-stability-fb3bfa9`.
- 최종 head fb3bfa9의 검사 scope/contracts/image/C03 Main PR Gate **4종 모두 success**(동일 head 중복 실행 두 묶음도 모두 통과)·현재 사용자=PR 작성자·main 리뷰1/enforce_admins/strict 유지 확인. `ci-final.json`/`checks-final.json`/`protection-before-decision.json`에 보존했다. **PR154에 한한 리뷰1→0 임시 변경·squash 병합·즉시1 복원 승인을 질문한 뒤 즉시 응답을 마친다. 실제 답변 전 병합 스크립트 실행 금지.** `/private/tmp/c04-merge-pr154.py`는 정확한 head/base·최신 검사/전체 보호 동일성·finally복원·40초 API 제한을 준비만 했고 실행하지 않았다. main 별도 리뷰1/enforce_admins/strict 유지. **PR153 예외 승인/PR15 리뷰/010 장애 승인 재사용 금지**. 새 PR154만의 리뷰/임시 변경 승인을 받아야 병합 가능하다. 이후 새 정상 CI/worker→새 준비/실행별 승인→새 복구 PR 사람 리뷰→C-03/C-04 최소120초 관찰→C-05/최종 정리다. Terraform/유료 모델/추가 장애 실행 없음. 추천 **GPT-6 SOL · High**.

## 2026-11-12 21:32 KST · PR15 승인·010 정상/회수 재검증·조회 보완 로컬 통과

- 실제 PR15 정확한 head `25c0a6b8d12d19a5db67b34fdb8ef9ca41a713dd`의 사용자 Approve는 **21:09:06 KST**, 필수 CI success다. 입력 거절 후 원복됐으며 PR은 OPEN/미병합이다. 21:30:54 독립 읽기 검증에서 정상 revision `0cfef83c36578090dfc1b65513789b1b10159e03`·Healthy/Synced·ready1·정상 이미지·HTTP `/readyz`와 `/items` 200·빈 Lease·두 토큰 회수·원복 Secret 부재·원격 감시 종료를 확인했다. 근거 `preparation-202611120010/execution-closed-readonly.json`. C-03/C-04 완료는 아니다.
- 정상 앱 watch의 변경 필드는 reconciledAt/resourceVersion/**generation/managedFields**였다. 근거 `010/live/readonly-app-refresh-diff.json`; 과거 거절의 내부 프레임이 없어 확정 원인으로 단정하지 않는다. 별도로 RV/reconciledAt만 함께 바뀌어도 같은 입력 거절이 나는 실제 bound gate 로컬 재현을 확보했다.
- 새 작업트리 `/Users/jounghyeon/dev/Url-Shortener-EKS-Platform/.worktrees/c04-observation-stability`·브랜치 `codex/c04-observation-stability-20261112`·한글 커밋 **c1e976b**. 관측 시각이 현재 이내로 엄격히 전진하고 RV도 바뀌며 나머지 전체 앱이 동일할 때만 허용한다. generation/managedFields/UID/spec/status/Deployment/Lease 전체·values·기한/신선도는 유지한다. 신뢰 소스의 상대 경로/함수/줄만 최대8개 남기는 진단 추가. **865개 시험/27.368초·배포 정적 검사·diff 검사 통과**. 근거 `/Users/jounghyeon/.local/share/url-shortener-ai-ops/c04-20261112/c04-observation-stability-c1e976b`.
- 다음: 새 PR/필수 CI를 준비하고 정확한 새 PR별 별도 리뷰 또는 임시 리뷰 수 변경 승인을 받는다. **PR153의 예외 승인/PR15 리뷰/010 실행승인 재사용 금지**. 반영 후 새 정상 CI/worker 기준선→새 준비/실행별 승인→새 복구 PR 사람 리뷰→C-03/C-04 관찰→C-05 R1~R8/최종 정리. 새 장애·Terraform·유료 모델 호출은 아직 실행하지 않았다. 추천 **GPT-6 SOL · High**.

## 2026-11-12 21:22 KST · 010 기한내PR15리뷰/CI 뒤 입력검증거부·무변경 로컬 진단

- PR15 head25c0a6b… **실제승인21:09:06 KST·필수CI success**, 마감21:10:33.862안에통과했고post context는21:09:19.055에게시됐다. 그러나source509실행기결과는 stopped·ApprovalRejected·**approval_rejection_codes=[execution_preflight_input_unavailable_or_invalid]**·fallback verified_normal_restored. merge시도없고장부정상rollback확인 **21:10:22.881**. 담당17584종료·cleanup_complete=true·두토큰revoked/Secret회수/remote종료. 새장애는추가하지않는다. C-03/C-04완료는아직아니다.
- 실제GitHubGET+MemoryDraft+명시적합성Live로순수읽기preflight재현 **ready=true**, 실제초안SQLite를mode=ro backup한복사본+실제GitHub+합성Live+실제현재clock으로도 **ready=true**였다. 원본DB/클러스터/PR수정과토큰발급이없었다. 자료010/live/readonly-mock-preflight-replay.json·readonly-copied-drafts-replay.json, helper /private/tmp/c04-readonly-preflight-replay.py. **합성Live결과는실행증거로재사용금지**다.
- 현재정상C4Transport.snapshot3회는1.22~1.83초,15초신선도한계이내였다. 실습Argo controller로그의고정이벤트/시각만추출한자료에서거부직전 **21:10:10 Refresh/Update successful**, 이어21:10:12부터원복sync를확인했다. status용메타데이터변경이전체원본동일성비교를깨뜨렸을가능성을검증중이며확정원인으로단정하지않는다. 자료readonly-current-snapshot-timing.json·readonly-argocd-reconcile-timing.json.
- 새장애없이정상Application기존자동갱신의변경필드이름만읽는boundedwatch /private/tmp/c04-watch-application-refresh.py 를시작했다. 결과010/live/readonly-app-refresh-diff.json. 다음은실제변경필드확인+동일오류로컬재현→필요시제어/정책/UID/source/digest/Lease검사는유지하며조회용메타데이터만구분하는수정·상세안전프레임진단/회귀검증이다. 변경완료/CI/새승인전실습재주입하지않는다. PR153승인/PR15리뷰재사용금지. 추천 **GPT-6 SOL · High**.

## 2026-11-12 21:08 KST · 010 새PR15 실제 안내 성공·기한내 사람리뷰 대기

- 010 실제 장애 handoff investigation_and_review·fault_injected=true. 새 [PR15](https://github.com/joungGo/c03-execution-lab/pull/15/files), 정확한head **25c0a6b8d12d19a5db67b34fdb8ef9ca41a713dd**. FIFO stdout질문을최우선실제async로보내 accepted응답을기본sandbox FIFO로전달했다. **실제notice21:08:28.145 KST·리뷰마감21:10:33.862·보장125.717초**로120초충족했다. 사용자에게즉시응답을마쳐GitHub직접리뷰시간을확보한다. 사람리뷰/CI는아직직접확인전이며합성하지않는다.
- source509faea의새조회예산·고정거절코드가적용된010이다. 실제리뷰답변후먼저GitHubPR15정확head/리뷰시각/CI와010/live/post-state.json·result-c03.json·result.json을확인한다. 실패시 approval_rejection_codes 를읽는다. 현재T21:07:03.862·소유원복21:11:03.862·독립원복21:12:03.862. 담당17584는C-03후양쪽guard종료/120초C-04/회수까지이어간다. host37321·cleanup42152·runner63423·local62512·remote43214235036. 이전PR14/009승인재사용금지. 정상복구후최종정상CI/worker기준선과C-05문서정리를마친다.

## 2026-11-12 21:07 KST · 수정 source010 실제 승인·activation 진행

- 사용자의010현재준비실행승인을 **21:06:59.188 KST** 유효기간내에서두hash/프로세스/실제양쪽guard/GH149856960과재검증해기록했다. **T=21:07:03.862 KST**, 리뷰마감 **21:10:33.862**, 소유원복목표 **21:11:03.862**, 독립원복시작기한 **21:12:03.862**. 과거승인재사용/소급없음. 현재010 activation.json 존재, source509faea/672파일/명시적조회예산·고정거절코드포함.
- 담당17584·FIFO host37321·cleanup42152·runner63423·local62512·remote43214235036 진행중. 실제주입handoff와새PR을관찰한다. **O=/private/tmp/c04-202611120010/host-requests.jsonl만빠르게poll**해새질문최우선실제async전송→accepted후기본sandbox host-response.fifo에actualJSON+newline write→review-notified.json120초보장확인→즉시응답종료해사람리뷰시간확보. 그전Cua/다른로그poll로ACK를늦추지않는다.
- 실제정확head리뷰/CI가확인되면실행기가병합/동기화하며실패시새approval_rejection_codes를우선읽는다. C-03후양쪽guard먼저종료·C-04최소120초관찰·회수·최종정상CI/worker기준선·C5정리를이어간다. 아직C-03/C-04실환경성공은확인전이다.

## 2026-11-12 21:04 KST · 수정 main010 현재 준비 완료·새 실행 승인 대기

- 현재010 intent **7ca28e1d666815c70fe2f1a6f8dc40fce7968da01cec4b958a308b93eec364c7**, readiness **aad4586b192fae825714a55027a3d407d0b5f789e7c7d1c207dd9a8235ebacda**. 준비21:03:39.211·만료 **21:08:39.211 KST**. PR153수정이포함된source509faea/672파일·새정상CI/worker ready·양쪽실제guard·FIFO안내와호스트자동회수준비완료다. 새실행승인질문뒤즉시응답을마친다. 아직010사용자답변/approval.json/activation/주입없음.
- 다음실제사용자승인수신시현재UTC/만료전·정확한두hash·모든자기프로세스/양쪽감시·GH149856960을대조하고현재UTC ReadyApproval만기록한다. O=/private/tmp/c04-202611120010, 담당17584·host37321·cleanup42152·runner63423·local62512·remote43214235036. 이후새PR stdout→최우선실제async accepted→기본sandbox FIFO ACK→notice120초검증후바로사용자에게응답해GitHub리뷰시간을확보한다.
- 실제PR리뷰뒤에는기한내정확head/CI원본·실행기상태를확인하고source509의고정거절코드를읽는다. 정상C-03후120초C-04관찰/회수·최종정상CI/worker기준선·R1~R8/문서정리로이어간다. 이전009/PR14승인이나완료된PR153병합승인을실행승인으로재사용하지않는다. Terraform/모델API없음. 추천 **GPT-6 SOL · High**.

## 2026-11-12 21:03 KST · 수정 main010 양쪽 감시 실제 준비·receipt 생성 중

- final prep39447 exit0, 새010 intent **7ca28e1d666815c70fe2f1a6f8dc40fce7968da01cec4b958a308b93eec364c7**. source **509faeaf1344fbedd59590bd0ab2c003ec796b8e**, runtime672파일. 정상CI43212655992·worker ready1건·지표보호21:00:42.124·fresh현재조건 검증완료다.
- 현재 **담당 session17584·local guard62512·runner63423·FIFO host session37321/PID62495·host-cleanup watcher session42152/PID62542**. 원격 **43214235036/job131169279194**, 실제UI환경승인과ready JSON확인. guardID remote:43214235036:b0abd353965344ed835f271b7e3c5c1e, 준비21:02:51.649·standby만료21:17:51.649 KST. 실제원격영수증010/standby/remote-ready-observed.json, UI이미지c04-010-guard-approved.png.
- 새조회예산·고정거절코드가포함된010의실제receipt를생성중이며아직실행승인/approval/activation/주입없음. 다음ready hash가나오면새승인질문후즉시응답을마친다. 이전009/PR14승인은재사용하지않는다. 실제PR질문은 O=/private/tmp/c04-202611120010/host-requests.jsonl→즉시실제async→accepted후기본sandbox host-response.fifo ACK→notice최소120초확인순서다. 다른로그조회로ACK를늦추지않는다.
- 이후실제PR의정확한head기한내리뷰·CI확인/병합/동기화/C-03인계·C-04최소120초관찰·회수까지이어간다. C-03후담당이양쪽guard를먼저종료해C-04관찰을보호한다. 실패시새 approval_rejection_codes를먼저확인하고임의재시도하지않는다. 추천 **GPT-6 SOL · High**.

## 2026-11-12 20:57 KST · 새 main010 정상CI·worker영수증 검증 완료·지표보호

- 새main509faea의정상CI **43212655992 success**, 원본artifact/binding/코드검증완료. 실제worker의동일run 영수증 **ready1건·summary존재·executor hashe0080589…**를20:56 KST읽기확인하고010/worker-normal-ci-verified.json에보존했다. worker-loaded-probe는baseline담당의exit0검증이며본문JSON출력으로가정하지않는다.
- **final prep session39447**, 실제CI후지표not-before **21:00:42.124 KST**를보존중이다. 이후 freshEKS/ECR/Git/앱/worker/PVC/회수사전검증과새010intent를생성한다. 아직010Argo자격/Secret/guard/ready/실행승인/주입없음. source509faea runtime672파일과기존서명/개인자료는보존했다.
- 010감시는 c04-ready-010-standby.py, 호스트는기존 c04-host-stdio.py의파일/FIFO경로 O=/private/tmp/c04-202611120010을사용한다. 호스트생성후 c04-close-fifo-host.py --trial202611120010을연결해결과후자기호스트만정리한다. 새ready질문뒤에는기다리지않고즉시응답종료해사용자가바로답하도록한다. 새실습/새PR의실제승인조건과120/210/240/300초기한은유지한다. 추천 **GPT-6 SOL · High**.

## 2026-11-12 20:53 KST · 새 main 정상CI010 진행·최종 로컬 작업목록 갱신

- 010 기준선담당46200 exit0. `c05-baseline-010/normal-preparation.json`의현재Pod한정RBAC/binding을적용하고worker/relay로드·앱/운영/PVC보존을검증했다. 새main509faea의정상CI **43212655992**를dispatch하고실제GitHubUI환경승인을완료했다. UI근거 /private/tmp/c04-ui-proof/c04-010-normal-approved.png. 새main push CI **43212280537 success**도확인했다.
- 010입력준비69970 exit0(672파일/normal code hash일치/현재C02조회), **최종prep session39447**가정상CI성공/원본artifact·binding→지표not-before5분보존→fresh현재조건→새intent를준비한다. 현재normal_ci_in_progress. 아직010 guard/Argo토큰/receipt/실행승인/주입없음. helper들은 /private/tmp/c04-{prepare-010-inputs,final-prep-010,ready-010-standby}.py다.
- 호스트자동정리helper를 `/private/tmp/c04-close-fifo-host.py --trial 202611120010`로일반화했다. 010intent/host/standby가생긴뒤시작한다. 정확한동일trial의host argv/PID만대조하고terminal result또는host이미종료시자기기록만남긴다. 토큰/Secret/guard회수는기존담당finally가한다. 기존privateFIFO전달은변경없다.
- root의 `실습 가이드 문서 모음/AI/구현계획/AI 작업 리스트.md`(실제폴더명NFD)는 **C현재상태만부분갱신**했다. C01설계/구현·C02인증조사/제안·C03새실습검증중·C04진행·C05로컬통제진행으로갱신하고009실패/원복·PR153소스/857시험·새010준비를명시했다. A5/A7미측정과과거A/B기록은보존하고AI포트폴리오는재개하지않았다. 이root문서변경은로컬이며새main에게시한것으로주장하지않는다.
- 다음010정상CI/worker ready·지표보호후intent→guard/실제UIready→현재ready hash의새실행승인(질문뒤즉시응답종료)→새PR정확head의실제사람리뷰/CI→병합/동기화/120초관찰/회수·C5정리. PR153승인은완료된main반영예외이며장애승인대신아님. 추천 **GPT-6 SOL · High**.

## 2026-11-12 20:49 KST · PR153 승인 병합·보호 복원·새010 기준선 준비

- 사용자의PR153한정임시리뷰변경/병합/즉시복원승인을받아정확한head e6b23e4와필수CI4개·base c66f664를재검증했다. **20:48:14 KST squash병합**, 새main **509faeaf1344fbedd59590bd0ab2c003ec796b8e**. **20:48:18 전체보호JSON원본과동일하게복원확인**(리뷰1명/strictCI/관리자적용유지). 근거 /private/tmp/c04-pr153-merge-proof 및 영구 c04-reader-budget-e6b23e4/merge-proof. e6b23e4와새main의ai-ops/.github tree동일성도확인했다. 이예외는종료됐으며새장애실행승인이아니다.
- 실제현재source공통Git(origin/main509faea)을fetch했다. 새010 runtime **672파일**, source509faea/자격미포함을 `~/.local/share/url-shortener-ai-ops/c04-20261112/preparation-202611120010/runtime`에생성했다. 새조회예산/고정거절코드진단은이묶음에포함된다. 기존실행기/source/사건자료를보존한다.
- 010기준선담당 `/private/tmp/c04-010-baseline.py` 시작. 현재Pod새binding/RBAC·worker/relay로드·앱/운영/PVC보존후 **새main정상CI**를dispatch하고결과를 `c05-baseline-010/state.json`에기록한다. 아직010최종intent/감시/receipt/승인/주입없음. 이어 `c04-prepare-010-inputs.py`→`c04-final-prep-010.py`(새CI ID를기준선state에서읽음)로원본성공/5분지표보호/fresh현재조건을검증한다. 새감시담당은 c04-ready-010-standby.py.
- 아직009는정상원복으로종료된실패실습이며PR14리뷰/승인은재사용하지않는다. 다음010 현재ready hash의새실행승인과새복구PR정확한head의실제리뷰가별도로필요하다. 승인질문뒤응답은즉시마친다. 실제PR안내는파일stdout→최우선실제async→accepted후기본sandboxFIFO ACK를유지한다. Terraform/유료모델 API 미실행. 추천 **GPT-6 SOL · High**.

## 2026-11-12 20:47 KST · PR153 필수CI4개 통과·이번PR한정 병합조건 승인 대기

- PR153 정확한head **e6b23e4670357e6fc3af657a189afd2a955c376f**, base c66f664…의 scope/contracts/image/C03 Main PR Gate **4개 success**, CodeRabbit skipped. CI43211831615. 작성자joungGo·리뷰0·REVIEW_REQUIRED, main리뷰1명·stale dismiss·strict C03 Main PR Gate·관리자적용을실제다시확인했다. 아직보호변경/병합없음.
- `/private/tmp/c04-merge-pr153.py`를준비만했다. 정확한head/base·4CI·전체보호원본을검사→이번PR에한해리뷰1→0(PATCH기존다른review설정유지)→전체보호JSON이그한필드만변했는지검사→SHA고정일반squash병합→finally에서원래리뷰1및전체보호JSON동일성복원·읽기확인한다. 응답유실에도finally복원하고3회재시도한다. 근거는실행시 `/private/tmp/c04-pr153-merge-proof/`에배타생성한다. **아직스크립트실행하지않았다.**
- 사용자에게PR153만이임시리뷰변경·병합·즉시복원을허용할지실제질문을보낸뒤응답을즉시마친다. 이전PR152예외재사용금지. 이승인은새실습장애실행을대신하지않으며 main반영후현재Pod정상CI/새runtime/현재ready결과의별도실행승인이필요하다. 사용자기한내PR14실제리뷰와009원복/회수근거는보존됐다. 추천 **GPT-6 SOL · High**.

## 2026-11-12 20:45 KST · 조회예산/고정 거절진단 보완857시험·PR153 CI 진행

- 독립worktree `.worktrees/c04-reader-budget/`, branch codex/c04-reader-budget-20261112, **base c66f664d…**에서5파일을수정해한글커밋 **e6b23e4670357e6fc3af657a189afd2a955c376f**(fix: 복구 조회 예산과 승인 거절 진단 보완)로게시했다. **PR153** https://github.com/f-lab-edu/F-Lab-DevOps/pull/153 , CI **43211831615** 진행중이다. 작성자joungGo/main별도리뷰1명 조건은유지하며아직병합/보호변경/새장애없음. PR152한정예외를재사용하지않는다.
- 새 `c_trial_read_budget.py`가실행기의소유원복기한까지남은정수초(최대180초)로GitHub reader를만들며8초미만이면거부한다. 기존기본45초벽시계가K8S/ECR수집동안소진되는명확한코드결함을수정한다. 개별GET8초·실시간자료15초·리뷰/원복시간과쓰기직전검사는유지한다. 두실행경로결과는 `approval_rejection_codes`에허용된고정코드만보존하며임의예외/API/비밀원문을출력하지않는다. 009정확한거절원인은소급확정하지않는다.
- **전체857시험통과28.536초·skip0**, CLI실제연결포함관련13개·Helm/배포정적·diff검사통과. 새회귀5개는50초비GitHub수집뒤실제subprocess/JSONreader·소유기한이후무조회·상한/8초미만거부·안전코드/순환체인·CLI동일deadline연결을확인한다. 초기새CLI시험fixture의빈binding파일누락은추가후13개/전체857개재검증했다. 근거 `/private/tmp/c04-reader-budget-full-tests.log`, 영구 `~/.local/share/url-shortener-ai-ops/c04-20261112/c04-reader-budget-e6b23e4/`.
- **009 최종실물20:43:30 KST**: 정상SHA0cfef83c…·Synced/Healthy·readyz/items200·빈Lease·제한Argo토큰/원복Secret·자기socket/FIFO부재·cleanup_complete=true. 개인009/post-state-verified.json. 사람리뷰14는기한내유효했지만승인검증거부로미병합/원복됐고C-03/C-04완료는아직아니다.
- Git주의: root저장소origin/main은별도clone의dd746a7로오래됐다. 처음만든빈worktree/branch는수정전정리했고, 실제C5공통Git(`/Users/jounghyeon/dev/Url-Shortener-EKS-Platform-c03/.git`)에서**명시적c66f664**로재생성했다. 기존root/다른worktree/개인자료는유지했다. 앞으로새작업기준SHA를명시한다.
- 다음CI통과/보호원본확인→PR153의구체적병합조건승인요청(필요한경우이번PR한정)→main반영/보호즉시복원→현재Pod기준선정상CI·새runtime/guard/ready→별도새실습실행승인→새정확head리뷰→복구/120초관찰/회수·C5정리. **승인질문뒤응답을즉시마쳐 사용자가바로답하도록한다.** 추천 **GPT-6 SOL · High**.

## 2026-11-12 20:39 KST · 009 기한내 실제리뷰/CI 통과 후 실행승인검증 거부·원복·조회예산 보완 중

- PR14 head3af09b57…의 **실제 joungGo APPROVED20:35:26 KST**, 필수 C03 Main PR Gate success로 마감20:36:32.765 안의 리뷰/CI를확인했다. post는review_and_ci_verified_context_published, 실제 context파일20:35:38.817. 그러나 runner는 **ApprovalRejected**로중지하고 병합을수행하지않았다. 원문거절코드를결과에보존하지않아 정확한게이트는확정하지못했다. 장부에는주입set/sync와 **20:36:33.874 정상rollback_confirmed**만있고 merge시도없다.
- 결과stopped·fault_injected=true·fallback=verified_normal_restored·capture_job_removed/ready_control_removed=true. 담당71998 exit0·cleanup_complete=true·secret_removed=true·remote_stopped=true·두토큰revoked=true. 사용자리뷰실패로표현하지않는다. C-03/C-04완료는아직아니다. host한건전달종료·watcher host_already_exited도확인했다. 추가실물정상응답검증을이어간다.
- 확인한코드결함: run_c4_trial.recovery_args가 기본 **ApprovalGitHubReader45초**를만들고, 이벽시계예산이이후K8S/ECR여러원본수집·잠금인계중에도소진된다. 기존preflight기본reader180초와불일치한다. 실제009 상세거절사유누락때문에 '45초때문으로확정'하지않고, 남은소유원복기한내의명시적budget과안전한고정거절코드기록을보완한다. **120/210/240/300초기한·15초실시간자료유효기간은늘리지않는다.**
- 새 worktree `.worktrees/c04-reader-budget/`, branch codex/c04-reader-budget-20261112, 기준origin/main c66f664…에서로컬수정/회귀검증을진행한다. 기존C5작업트리와미커밋최종문서초안은보존한다. 새장애/새실습승인/PR14수동병합을하지않는다. 소스반영은새PR/CI/리뷰규칙에따르며PR152일회예외재사용금지. 추천 **GPT-6 SOL · High**.

## 2026-11-12 20:34 KST · 009 실제 새PR14 안내 전달 성공·정확한head 사람리뷰 대기

- 009 실제 장애 handoff investigation_and_review·fault_injected=true. 새 [복구PR14](https://github.com/joungGo/c03-execution-lab/pull/14/files), 정확한head **3af09b57a5522390caa807860633ed43978acac7**. 새FIFO stdout질문을즉시실제async도구로전송하고 accepted=true 응답을기본sandbox FIFO로전달했다. **live/review-notified.json 실제안내20:34:29.299 KST**, 리뷰마감 **20:36:32.765**, 보장창 **123.465초**로120초충족했다. 이전007호스트ACK실패와달리이번에는실제안내영수증생성성공이다.
- **사용자가즉시GitHub리뷰할수있게 이번새PR질문뒤응답을바로마친다.** 사람리뷰기록을합성하지않는다. 현재runner/post는실제정확한head리뷰·필수CI를자동조회하며조건충족시병합·동기화한다. 담당71998은 C-03receipt이후양쪽guard종료·C-04최소120초관찰·토큰/Secret회수까지수행한다. host79245는한건전달후종료되며 watcher19130상태를확인한다. 현재C-03/C-04완료는아직아니다.
- 다음사용자답변시먼저 live/post-state.json·result-c03.json·result.json·standby/state.json 확인. 현재T20:33:02.765·리뷰20:36:32.765·소유원복20:37:02.765·독립원복20:38:02.765. 실제리뷰/CI원본은별도확인하며새승인/기한확장/리뷰우회없음. 실제완료후정상응답/worker설정·C-04관찰·회수최종검증과C-05문서정리를이어간다.

## 2026-11-12 20:33 KST · 009 실제 현재 승인·activation 시작

- 사용자의009 현재준비 실행승인을 실제 **20:32:57.483 KST**에 유효기간안에서 hash·사용자·프로세스·양쪽감시와대조해 ReadyApproval을기록했다. **T=20:33:02.765 KST**, 리뷰마감 **20:36:32.765**, 소유원복목표 **20:37:02.765**, 독립원복시작기한 **20:38:02.765**. 승인시각을소급하지않았다.
- 현재009 live/activation.json 존재. 담당71998·host79245·cleanup19130·runner50846·local50034·remote43209465530. O=/private/tmp/c04-202611120009. PR알림대기는 **O/host-requests.jsonl만빠르게확인**하고 새질문즉시실제async전송→accepted후기본sandboxFIFO ACK→notice최소120초검증이최우선이다. 기존PR13/실습승인은재사용하지않는다. C-03인계후양쪽guard종료·C-04최소120초관찰·회수까지이어간다.

## 2026-11-12 20:31 KST · 009 현재 준비 완료·즉시 응답 종료해 새 승인 대기

- 현재009 intent **4c9b05f04cf1487a3f50b04d5fd9b883fa0a69ceeb9f57750f931836170b9fe7**, readiness **d39e8c5e1e5029477b1f3111d727c419411d6aa3c119b57ab7987064808e358b**. 준비20:30:44.077·만료 **20:35:44.077 KST**. 실제 새 실행승인 질문을 전달하고 바로 사용자에게 응답을 마친다. 아직009승인/approval.json/activation/주입없음.
- 다음 답변이 도착하면 **현재UTC가만료전인지 먼저확인**한다. 같은intent/readiness hash·원본ready/현재프로세스·양쪽감시를대조한 뒤 실제 사용자승인시각으로 ReadyApproval을 새로기록한다. 승인뒤 첫주입은실행기가양쪽ACK와현재조건을다시검사한다. 이후 O/host-requests.jsonl에새질문이오면 **실제async전송→accepted후기본sandbox FIFO ACK→notice120초검증**이최우선이다. 사용자PR리뷰요청뒤에는필수CI와GitHub정확한head실제리뷰를실행기가검증한다. C-03뒤양쪽guard종료·C-04최소120초관찰·회수까지이어간다.
- 담당71998·host79245·host-cleanup watcher19130·runner50846·local50034·remote43209465530. 만료뒤답변은재사용하지않는다. 상태/회수는009/live/result.json·standby/state.json·standby/host-cleanup.json에서확인한다. 추천 **GPT-6 SOL · High**.

## 2026-11-12 20:30 KST · 009 실제 양쪽 감시·FIFO/자동호스트정리 연결

- 009 intent **4c9b05f04cf1487a3f50b04d5fd9b883fa0a69ceeb9f57750f931836170b9fe7**. 현재 정상CI43206063219·binding·EKS/ECR/Git/대상/회수 상태 재검증과 runtime670파일 생성 완료. final prep23383 exit0. 새장애/activation/실행승인 없음.
- 현재 **준비담당 session71998·local guard PID50034·runner50846·FIFO host session79245/PID50005**. 개인 `~/.local/share/url-shortener-ai-ops/c04-20261112/preparation-202611120009/`, O=/private/tmp/c04-202611120009. 실제 UI 환경 승인한 원격 **43209465530/job131153051908**, guard ID remote:43209465530:a7ba9e64a239492792a9b644804b0178, 준비20:29:40.056·standby 만료20:44:40.056 KST. 실제 화면 ready JSON을009/standby/remote-ready-observed.json에 저장했다. UI이미지 /private/tmp/c04-ui-proof/c04-009-guard-approved.png.
- 실제 결과 파일이 생기면 정확한 argv의 자기FIFO host만 종료하는 한정 watcher **session19130/PID50552**를 연결했다. 009/standby/host-cleanup.json에 host50005/watcher50552와 실제종료상태를 기록한다. 미승인만료 후 담당finally가 토큰/Secret/감시를회수하고 watcher가호스트를종료한다. 다른리소스/프로세스는대상이아니다.
- receipt 생성 뒤 실제 새승인질문을 전달하고 **즉시 사용자에게 응답을 마친다**. 현재 준비의5분 유효기간을 기다린뒤 끝내지 않는다. 다음 사용자승인수신시 UTC현재시각/readiness만료/실제hash·프로세스/guard를대조하고 ReadyApproval을기록한다. 종료된008승인 재사용금지. 승인후새PR stdout은 O/host-requests.jsonl, 실제async accepted후ACK는 O/host-response.fifo로 기본sandbox os.write를사용한다. 120초보장/210·240·300초기한유지.

## 2026-11-12 20:27 KST · 008 만료 뒤 승인 수신·009 현재 조건 재준비

- 사용자의008 실행승인은 실제20:26 KST 이후 수신해 준비만료20:23:26 및감시/자격회수 뒤였다. **008을 재기동하거나 승인시각을 소급하지 않았다.** 009를 같은 정상 기준선에서 재검증한다. 이번에는 실제준비완료와 새승인질문 뒤 **응답을 즉시마쳐 사용자가 바로 답하도록** 한다. 승인대기5분을 도구poll로 모두 소비한뒤종료하는 방식을 반복하지 않는다. 미승인만료는 기존담당finally가 무주입종료/회수한다.
- 새개인009폴더 `~/.local/share/url-shortener-ai-ops/c04-20261112/preparation-202611120009/`, 검토 main c66f664… runtime670파일. `/private/tmp/c04-prepare-009-inputs.py`, `c04-final-prep-009.py`, `c04-ready-009-standby.py`는008 경로의trialID만009로변경했다. 정상CI43206063219/c05-baseline-008은 현재정체성이유지될경우 원본을재검증해사용한다. 새실행에는009현재intent/readiness hash의 별도실제승인이필요하다.
- FIFO host전달은이전실제리허설통과경로를유지한다. 실제새PR stdout파일→최우선실제async도구→accepted응답후기본sandbox FIFO ACK→120초보장영수증 순서를지킨다. 008/007승인과PR13기한뒤리뷰재사용금지. 아직009감시/receipt/approval/activation/주입없음. 추천 **GPT-6 SOL · High**.

## 2026-11-12 20:25 KST · 008 무주입 만료 종료·호스트/감시/자격 회수

- 008은 **20:23:26.283 KST** 준비 만료 전 새 실행 승인 답변이 없어 무주입 종료했다. live/result.json stopped·fault_injected=false, approval/activation 없음. 담당36554 exit0·runner_exit1·cleanup_complete=true·remote_stopped=true·secret_removed=true·두token revoked=true. **008 종료 뒤 도착하는 승인으로 재기동하지 않는다.** 아래 승인대기/감시중 기록은 종료 이력이다.
- FIFO host14112의 실제 PID45117과 정확한 argv를 대조해 SIGTERM으로 종료했고 FIFO가 정리됐다. 처음 venv 경로 prefix로 찾은0건은 인터프리터가 실제 Homebrew 경로로 표시된 때문이며, 이후 정확한PID/argv 대조로 자기 프로세스만 종료했다. 이 host 종료241은 SIGTERM 결과다. 기본 로그인/App키/PVC/사건자료를 보존한다.
- **20:25:06 KST 최종 실물 검증 완료**:008/post-expiry-verification.json에서 정상 revision0cfef83c…·Synced/Healthy·Ready1·readyz/items200·빈 Lease·제한 Argo spec/status 토큰 부재·환경 원복Secret 부재·자기socket/FIFO 부재를 확인했다. Terraform/유료모델 API 미실행.
- 이번 작업의 실제 완료: 007 실제 장애 주입·새PR13 생성·기한부족 중지·정상 원복/회수 검증, 호스트 stdout/FIFO 모의3개와 sandbox연결 모의 및 **실제 사용자 안내 도구 전달 리허설9904 성공**, 새 정상CI43206063219/worker ready/현재설정로드·5분보호·008실제준비/무주입만료/회수. **새 C-03 병합·동기화 인계와 C-04 최소120초 관찰은 미완료**, 따라서 C-05 최종종료/전체AI완료도 주장하지 않는다. C5-final-validation-20261112.md는 작업트리 미커밋 초안이다.
- 다음 실제 실습은 사용자가 실행/약2분GitHub리뷰할 수 있는 때 새009 준비로 시작한다. 현재 정상CI/binding이 유지되고 유효한 경우 원본을 다시검증해재사용하며, 새 현재조건·양쪽감시·ready hash에 대한 새 실행승인이 필요하다. 006/007/008 승인·PR13기한뒤리뷰·리허설응답을 재사용하지 않는다. 실제PR대기는 새 FIFO stdout파일만 빠르게읽고 actual async accepted→기본sandbox FIFO ACK를 최우선 처리한다. C-04 완료 뒤 R1~R8 최종대조/문서·작업리스트·정리로 이어간다. 추천 **GPT-6 SOL · High**.

## 2026-11-12 20:18 KST · 008 현재 준비 완료·실제 실행 승인 대기

- 008 intent **27f6d2495b01179bc2edb34d28a95d28e2df9c8d399922d992536deb3b8fb37f**, readiness **4d2d878cc01d41b79b6b24d8a5788a34c92a9f78664117cab3e36c5371988f5f**. 준비20:18:26.283·만료 **20:23:26.283 KST**. 실제 async 새 실행 승인 질문을 전달했고 accepted=true. 아직008 답변/approval.json/activation/장애 없음. 만료 전 실제 승인 답변을 현재hash/프로세스/양쪽감시와 대조한 뒤 현재UTC ReadyApproval만 기록한다.
- 담당36554·FIFO host14112·runner45693·local45145·remote43207772638을 유지한다. 개인008/live/readiness.json·standby/state.json을 먼저 확인한다. 승인 후 PR대기는 `$O/host-requests.jsonl`만 빠르게 읽고 실제 질문 전송과 FIFO 실제accepted ACK를 최우선 처리한다. 120초보장/210·240·300초기한 유지. 이전실습/리허설/PR13리뷰재사용 금지. 추천 **GPT-6 SOL · High**.

## 2026-11-12 20:18 KST · 008 양쪽 감시/빠른 호스트 연결 완료·receipt 생성 중

- 008 새 intent **27f6d2495b01179bc2edb34d28a95d28e2df9c8d399922d992536deb3b8fb37f**, final prep73796 exit0. 실제 UI 원격 감시43207772638/job131147209941의 환경 승인·ready JSON을 관찰해 기록했다. guard ID `remote:43207772638:557eaedfcd4641fdbf7ebe84a215de0b`, 준비20:17:47.940·standby 만료20:32:47.940 KST. 증거008/standby/remote-ready-observed.json, UI이미지c04-008-guard-approved.png.
- 현재 **담당 session36554/PID45080, 로컬guard45145, runner45693, FIFO host session14112**. 실제 안내 stdout `$O/host-requests.jsonl`, ACK `$O/host-response.fifo`, O=/private/tmp/c04-202611120008. 정상CI43206063219·worker ready·5분보호·현재사전검증 완료. 아직008 actual receipt 생성 중·실행승인/activation/주입 없음. receipt hash가 나오면 즉시 새 승인 요청한다. 이전007/006 승인·PR13리뷰·리허설 수신 응답 재사용 금지.
- 사용자에게 리허설 확인 방법을 설명했다. 현재 메시지에 응답하면 수신확인이며 GitHub 작업은 없다. 실제 새 복구 PR이 나오면 별도로 링크와head/마감을 안내한다. 새PR출력 후 **최우선 실제async전송→기본sandbox FIFO actual accepted ACK→notice영수증 확인** 순서로 처리한다. 그 전 다른로그/CUA/담당poll을 하지 않아 전달시간을 확보한다. 추천 **GPT-6 SOL · High**.

## 2026-11-12 20:13 KST · 실제 FIFO 안내 전달 리허설 성공·008 CI/worker 통과

- 정상 CI43206063219는 **success**, 원본 artifact/binding/코드 검증 완료. 현재 worker에 ready 영수증1건·summary 존재·executor hash e0080589…를 실제 읽기 확인해008/worker-normal-ci-verified.json에 저장했다. final prep **session73796**은 지표 보호 not-before **20:15:34.382 KST**를 보존 중이며 이후 fresh preflight/intent를 생성한다. 아직008감시/실행승인/주입없음.
- 별도 리허설 **202611129904**에서 기존 검토 bridge CLI의 rehearsal모드·새 FIFO host·실제 request_user_input_async의 accepted 응답·기본 sandbox FIFO write·실제 bridge 응답을 끝까지 확인했다. **20:12:54 KST 성공**, rehearsal=true·실제실행재사용불가. stdout 파일mtime20:12:39.279→ACK20:12:54.099, 이 한 건의 실제 호스트 전달14.820초다. 자동 ACK나 사람 리뷰를 합성하지 않았다. 사람의 수신확인 답변은 아직 없으며 실제 도구 전달 성공과 구분한다. 자료 `host-fifo-validation/actual-delivery-rehearsal-9904/`, 리허설 session67401 exit0·FIFO/socket 회수완료.
- 사용자PR13 실제 GitHub 리뷰 원본은 joungGo APPROVED **20:06:34 KST**, 정확한 head c82e1cf…다. 마감20:01:05.758 뒤이므로007 안내 ACK 실패와 별개로도 재사용하지 않는다. 새008에는 새 실행 승인과 새 복구 PR 정확한 head의 실제 리뷰가 필요하다.
- FIFO 응답은 **실제 async accepted 후에만** `os.open($O/host-response.fifo, O_WRONLY|O_NONBLOCK|O_NOFOLLOW)` 후 `os.write(fd,b'{"delivery_tool_response":{"accepted":true}}\n')`로 전달한다. 기본 sandbox exec_command를 사용하면 에스컬레이션/PTY poll 왕복을 줄인다. 새 PR 대기 중에는 다른로그조회로 호스트 전달을 지연하지 않는다. T+90까지 실제 notice 완료·최소120초보장/리뷰T+210/소유T+240/감시T+300은 그대로다. 다음 추천 **GPT-6 SOL · High**.

## 2026-11-12 20:08 KST · 007 원복 확인·호스트 전달 보완·008 준비

- 사용자가 PR13의 승인을 알려왔지만 007은 이미 안내 ACK 마감 실패로 종료됐다. 이 뒤늦은 리뷰로 병합하거나 승인을 재사용하지 않는다. 007 실제 원복 검증20:04:34: 정상 SHA0cfef83c…·Synced/Healthy·Ready1·readyz/items 각200·빈 Lease·제한 Argo 토큰/원복 Secret 부재. `preparation-202611120007/post-state-verified.json`. capture_job_removed/ready_control_removed=true, 담당89769 종료·cleanup_complete=true다.
- 기존 bridge CLI는 변경하지 않았다. private 호스트 연결 `/private/tmp/c04-host-stdio.py`가 stdout을 `host-requests.jsonl`, stdin을 권한0600 전용 `host-response.fifo`로 연결한다. 모의3개 통과(2.325초), sandbox 밖 child와 안쪽 FIFO write 모의도 exit0(0.000064초). 실제 전달 성공이나 실환경 개선으로 세지 않는다. 소스·hash·증거는 `~/.local/share/url-shortener-ai-ops/c04-20261112/host-fifo-validation/`에 있다. 실제 accepted 응답 전에는 ACK를 만들지 않는다.
- 007 not-before20:04:22.913을 보존한 뒤 새 현재 Pod binding/RBAC·worker/relay 로드·앱/운영/PVC 보존 확인을 완료했다. 개인 기준선 `c05-baseline-008/`, 담당20354 exit0. 새 정상 CI **43206063219**, main c66f664…를 dispatch하고 실제 GitHub UI 환경 승인을 완료했다. UI 근거 `/private/tmp/c04-ui-proof/c04-008-normal-approved.png`.
- 새008은 `~/.local/share/url-shortener-ai-ops/c04-20261112/preparation-202611120008/`. 검토 Git main c66f664의 runtime670파일·현재worker C-02 최소 권한과 단기 파생 자격 준비 완료(session63752 exit0). **final prep session73796**, `/private/tmp/c04-final-prep-008.py`는 정상 CI43206063219 성공/원본·binding 검증 → 실제 not-before 경과 → fresh EKS/ECR/Git 사전 검증을 수행한다. 아직008 intent/감시/실행 승인/주입 없음.
- 008 감시는 `/private/tmp/c04-ready-008-standby.py`를 사용한다. 새 intent 뒤 host는 `$PY /private/tmp/c04-host-stdio.py --directory $O -- $PY $R/ai-ops/scripts/bridge_c4_review_notice.py --when-ready --plan $P/intent.json --activation $P/live/activation.json --socket $O/review.sock`로 시작한다. P는008 개인 폴더, R=P/runtime, O=/private/tmp/c04-202611120008, PY는기존venv이며 PYTHONPATH/AI_OPS_FILES_ROOT를 기존처럼 지정한다.
- **승인 후 새 PR 대기 중에는 stdout 파일만 빠르게 확인한다.** 실제 질문을 최우선 request_user_input_async로 전송하고 accepted=true 응답 뒤 즉시 기본 sandbox exec_command에서 FIFO에 실제 응답 JSON+newline을 쓴다(os.open O_WRONLY|O_NONBLOCK|O_NOFOLLOW, os.write). 느린 write_stdin ACK 경로를 재사용하지 않는다. 실제 notice 영수증의 120초 보장 확인 후에만 다른로그/CUA를 조회한다. 기한 확대·ACK 합성·시각 소급 금지. 새 실행에는 새 readiness hash의 실제 승인과 새 PR 정확한 head의 실제 리뷰가 각각 필요하다. Terraform/모델 API 미실행. 추천 **GPT-6 SOL · High**.

## 2026-11-12 20:00 KST · 007 PR13 생성·안내 ACK 마감 실패·정상 원복

- 007은 실제 사용자 재지시 승인 후 주입됐고 새 [PR #13](https://github.com/joungGo/c03-execution-lab/pull/13/files), head **c82e1cf729034fee7d045472512231b7cba3c209**를 생성했다. post stage: fresh recheck19:58:08.424 → creating writer PR19:58:35.401 → ready19:58:43.315. bridge33482에서 실제 질문을 읽어 실제 async 도구로 전송(accepted=true)했으나, 그 응답을 stdin에 전달하는 왕복이 **안내 완료 최종기한19:59:05.758 KST**를 넘겼다. bridge는 성공 영수증을 만들지 않았고 post-stage stopped TimeoutError다. **요청 출력/도구 전송만으로 안내 성공을 합성하지 않았으며 PR13을 병합하지 않았다.** 사용자에게 PR13 리뷰 불필요·원복 진행을 안내했다.
- 007 결과 **restored_without_c03_completion·fault_injected=true**. 담당89769 exit0, cleanup_complete=true·remote_stopped=true·secret_removed=true·양쪽 Argo token revoked=true. bridge33482 exit1(안내 확인 실패). 20:00 KST 실제 GET에서 정상 SHA0cfef83c…·Synced/Healthy·Ready1을 확인했다. C-04 실제 성공 관찰 완료는 아니다. 기존007 승인/PR13 리뷰를 새 실행에 재사용하지 않는다.
- 원인은 호스트가 bridge 출력을 poll하고 실제 도구를 호출한 뒤 stdin ACK를 보내는 도구 왕복 지연이다. 120초 리뷰/210초·240초·300초 기한은 유지한다. 다음은 기존 bridge 자체를 변경하지 않고 **private 파일 stdout·전용 FIFO stdin 경로로 실제 host 전달 지연을 줄이는 로컬 검증**부터 진행한다. 실제 accepted 응답 전에는 ACK를 쓰지 않으며 모의 검증을 실제 안내로 세지 않는다. 다음 실제 실행에는 새 기준선/새 준비/별도 실행 승인이 필요하다. 추천 **GPT-6 SOL · High**.

## 2026-11-12 19:58 KST · 007 사용자 재지시 승인·현재 실제 장애 주입 진행

- 사용자가 async에서 '이번 실습 실행하지 않음'을 선택한 직후 같은 준비 유효기간 안에 **'실습 진행해'**라고 명시적으로 번복했다. 취소 파일/정리/만료가 없는 같은007 readiness와 실제 프로세스·양쪽 감시·사용자 identity를 재대조했고 **19:57:28.508 KST**의 현재 시각 ReadyApproval을 기록했다. 006의 늦은 승인으로 실행한 것이 아니다.
- activation **T=19:57:35.758 KST**, actual handoff status investigation_and_review·**fault_injected=true**. 정확한 리뷰 마감 **20:01:05.758 KST**, 소유 원복 목표 **20:01:35.758**, 독립 원복 시작 **20:02:35.758**. 007의 새 실제 PR 안내/리뷰를 기다린다. 담당89769·bridge33482·runner37821·local37241·remote43204412124. 개인007폴더 live상태를 먼저 확인한다.
- bridge33482의 실제 요청을 받으면 **최우선 실제 request_user_input_async 호출→accepted응답을 같은bridge stdin에 즉시 전달**한다. 아직 새 PR URL/head를 받지 못했으며 사용자 리뷰를 합성하지 않는다. C-03 receipt 뒤 양쪽감시 중지/120초 C-04 관찰/자격회수까지 이어간다. 이전 취소/승인대기 기록은 현재 activation으로 갱신됐으며007 실습 진행 중이다.

## 2026-11-12 19:55 KST · 007 현재 준비 완료·새 실행 승인 대기

- 007 readiness hash **4c154ffb99b8a8f3583a74fa6825dc265d22283ef503e119efe68cec06b7b2c5**, intent **7c42e1e80514ce3a401372b91f3a07ffdcc70444601569a8dd7ae1c7962fd76f**. 실제 준비19:55:39.130·만료 **20:00:39.130 KST**. 실제 새 async 승인 질문 accepted=true. 아직007 답변/approval/activation/주입 없음. 실제 답변이 만료 전에 들어오면 현재프로세스/같은hash/감시를 재대조한 뒤 현재UTC의 ReadyApproval만 새로 기록한다. 승인 시각 소급 및006 답변 재사용 금지.
- 담당89769·bridge33482·runner37821·local37241·remote43204412124를 관찰한다. 개인 위치 아래007과 같다. 실제 새 복구 PR 안내 요청이 bridge stdout에 오면 먼저 실제 request_user_input_async를 호출하고 accepted=true 응답을 같은bridge stdin에 즉시 전달한다. 실제 head의 GitHub 사용자 리뷰는 별도다. 미승인 만료 시 담당finally 회수 및 실물 검증 후 종료한다. 추천 **GPT-6 SOL · High**.

## 2026-11-12 19:55 KST · 007 양쪽 감시 실제 준비·새 receipt 생성 중

- 007 intent hash **7c42e1e80514ce3a401372b91f3a07ffdcc70444601569a8dd7ae1c7962fd76f**. 정상 CI43198146431 원본·현재 기준선·이미지·Git 보호 재검증 완료, 최종 prep session76060 exit0. 실제 UI 환경 승인 후 원격 [43204412124](https://github.com/f-lab-edu/F-Lab-DevOps/actions/runs/43204412124)·job131135826871의 실제 ready JSON을 읽어 기록했다. guard ID `remote:43204412124:2b53dd6951704a519bd3986df73f257d`, 준비19:55:01.230·standby 만료20:10:01.230 KST.
- 현재 **담당 session89769·bridge33482·로컬 PID37241·runner37821**. 개인 위치 `~/.local/share/url-shortener-ai-ops/c04-20261112/preparation-202611120007/`, 소켓 `/private/tmp/c04-202611120007/review.sock`. 실제 receipt 생성 중이며 아직007 사용자 승인/approval/activation/주입 없음. 새 receipt가 생성되면 즉시 실제 새 실행 승인을 요청한다. 006의 늦은 승인 재사용 금지.

## 2026-11-12 19:52 KST · 006 만료 뒤 승인 수신·007 재준비

- 사용자가 006 준비 결과의 실행 승인을 답했으나 실제 수신은 **19:51 KST 이후**, 준비 만료19:28:58과 감시/자격 회수 뒤다. **승인을 소급하거나 006을 재기동하지 않았다.** 006 결과는 무주입 종료로 유지한다. 새 현재 결과를 준비한 뒤 별도 승인받는다고 안내했다.
- 새 실습 **202611120007**을 같은 main c66f664d…로 준비한다. 개인 위치 `~/.local/share/url-shortener-ai-ops/c04-20261112/preparation-202611120007/`, 새 Git runtime670파일(자격 미포함)을 만들었다. 기존 operator로 서울 STS 계정764428911194/IAM urlshortener-operator를 확인했다. 현재 입력 준비 **session46318**, 007용 private orchestration `/private/tmp/c05-prepare-007-inputs.py`, `c05-final-prep-007.py`, `c04-ready-007-standby.py`는 동일 검토 경로의 trial ID만 변경했다. 정상 CI43198146431·current binding·Git/계정/ECR/기준선/회수 상태를 다시 검증한다. 기존 CI 성공을 재검증하며 새 CI를 불필요하게 다시 실행하지 않는다.
- 아직007 intent/준비 영수증/감시/실행 승인/주입 없음. 다음 양쪽 standby·실제 UI ready·실제 안내 소켓 후 새로운 intent/readiness hash 승인이 필요하다. Terraform·모델 API 미실행. 추천 **GPT-6 SOL · High**.

## 2026-11-12 19:30 KST · 006 승인 전 준비 만료·무주입 종료·회수 실물 확인

- 006의 새 실행 승인 답변을 받기 전에 **19:28:58.147 KST** 준비가 만료됐다. `live/result.json`은 **stopped·fault_injected=false**, approval.json/activation.json 모두 없다. **006은 종료됐고 이후 도착하는 승인으로 재기동하지 않는다.** 아래 승인 대기/감시 진행 중은 종료 이력이다. 새 실습은 새 사전 대조·양쪽 감시·readiness hash와 실제 실행 승인이 필요하다.
- 담당 **session53868 exit0**, runner_exit1은 승인 만료 중지를 뜻한다. `standby/state.json`은 **cleanup_complete=true·remote_stopped=true·secret_removed=true·c03-sync/c03-rollback revoked=true**. bridge2153도 Ctrl-C로 종료했다. 임시 core 설정과 소켓을 정리했다. **19:29:57 KST 실제 읽기 검증**에서 앱 정상 revision0cfef83c…·Synced/Healthy·Ready1·빈 Lease, 제한 Argo spec/status 토큰0·환경 C03_ROLLBACK_ARGOCD_TOKEN 부재·자기 소켓 부재를 확인했다. 개인 근거 `preparation-202611120006/post-expiry-verification.json`.
- **PR #152 병합·main 보호 복원과 새 main 정상 CI/worker ready 검증은 완료**했다. C-04 새 실제 복구/관찰과 C-05 최종 실환경 판정은 아직 미완료다. 새 안내 경로의 실측 속도 개선도 아직 주장하지 않는다. `.worktrees/c05-final-validation/ai-ops/docs/C5-final-validation-20261112.md`에 최종 대조 초안(미커밋)을 작성했고 006 만료를 반영했다. 기존 root/다른 worktree의 변경은 보존했다.
- 남은 작업은 새 준비 결과의 실행 승인 → 새 복구 PR 정확한 head 실제 사람 리뷰·필수 CI → 병합/동기화·C-03 인계·C-04 최소120초 관찰 → 회수/최종 R1~R8·문서/작업 리스트 정리다. A5 HolmesGPT 동등 조건 비교·A7 사람 독립 의미 채점/수동 기준선은 별도 미측정이며 기능 검증으로 완료 처리하지 않는다. B단계 최신 범위 근거는 이전 worktree `B6-completion-gate-20261028.md`와 `B-stage-readiness-audit-20261028.md`(독립 의미 검토는 당시 사용자 선택으로 생략)다. 추천 **GPT-6 SOL · High**.

## 2026-11-12 19:24 KST · 006 실제 준비 완료·새 실행 승인 대기

- **현재006**의 실제 readiness hash **3b2606fc1f5497790e158e45258c608b2d4b469d576be244eb23ca3b8741d777**, intent hash **95617bbcb0faf23e6533cbd260f0556ead008ebed769d3c63773b26141484775**. 실제 준비19:23:58.147·만료 **19:28:58.147 KST**. 새 실제 async 승인 질문을 전달했고 accepted=true다. **아직 답변/approval.json/activation/장애 없음**. 만료 전에 현재 프로세스/양쪽감시/정체성과 실제 답변을 확인한 경우에만 ReadyApproval을 새로 기록한다. 답변 시각을 소급하지 않으며 만료된 승인은 재사용하지 않는다.
- 현재 개인 폴더 `~/.local/share/url-shortener-ai-ops/c04-20261112/preparation-202611120006/`, 담당 session53868·bridge2153·runner28981·local28192·remote43199747397을 유지한다. 상태와 정리는 `standby/state.json`, `live/result.json`. 아래 19:24 준비 생성 중 기록은 이 receipt로 갱신됐다. 실제 새 PR 안내 시 accepted 응답을 같은 bridge stdin에 즉시 전달한다. 추천 **GPT-6 SOL · High**.

## 2026-11-12 19:24 KST · 006 양쪽 standby 실제 준비 확인·준비 영수증 생성 중

- 새 intent hash **95617bbcb0faf23e6533cbd260f0556ead008ebed769d3c63773b26141484775**. 최종 사전 검증 exec 8847 exit0, 정상 CI/현재 worker ready 영수증을 보존했다. 실제 GitHub UI에서 원격 감시 [43199747397](https://github.com/f-lab-edu/F-Lab-DevOps/actions/runs/43199747397) 환경 승인을 처리하고 job **131119736084**의 실제 ready JSON을 읽었다. 원격 ID `remote:43199747397:a9e53b8249b7420ab616c377ea0a7e0b`, 준비 19:23:23.489 KST·standby 만료19:38:23.489. `standby/remote-ready-observed.json`과 UI 이미지 `/private/tmp/c04-ui-proof/c04-006-guard-approved.png`에 기록했다.
- **현재 담당 exec session53868**, 로컬 guard PID28192, runner PID28981. 실제 안내 bridge **exec session2153**, 소켓 `/private/tmp/c04-202611120006/review.sock`로 연결했다. 개인 준비 위치는 아래 006 폴더와 같다. 제한된 45분 Argo sync/rollback 토큰과 환경 Secret은 이번 담당 finally가 회수한다. 현재 실제 준비 receipt를 생성 중이며 **아직006 사용자 실행 승인/approval/activation/주입 없음**. `live/readiness.json`이 만들어지면 즉시 hash·만료를 읽고 실제 새 승인을 요청한다.
- 실제 새 PR 안내를 받으면 **먼저 실제 request_user_input_async 전송**, accepted=true 응답을 같은 bridge2153 stdin에 전달하고 사용자가 GitHub에서 정확한 head를 직접 리뷰하게 한다. 실제 안내 뒤 최소120초·T+210초 리뷰 마감·T+240초 소유 원복·T+300초 독립 원복, C-03 후120초 C-04 관찰을 유지한다. 이전 승인/PR12 기한 뒤 리뷰 재사용 금지. PR152 보호 복원 증거 영구 보존은 `~/.local/share/url-shortener-ai-ops/c04-20261112/c05-final-validation-d1b7975/merge-proof/`다.

## 2026-11-12 19:17 KST · PR #152 병합·보호 복원, 새 main 정상 CI 통과·006 준비 중

- 사용자에게 받은 **PR #152 한정 임시 리뷰 변경 승인**에 따라 정확한 head d1b79754f3034a4abc9da70be72d45cc9f72571b를 squash 병합했다. 새 main **c66f664d0ede76b8c60fba11d40fb2ffd51445aa**, 병합 시각 19:10:59 KST. 리뷰 수 1→0 외 보호 설정을 유지했고 **병합 직후 리뷰 1명과 전체 원래 보호 JSON의 동일성까지 복원·확인**했다. 근거 `/private/tmp/c05-pr152-merge-proof/`. 이 예외는 종료됐으며 새 장애 실행 승인은 아니다.
- 새 main push CI 43198080376 성공. 현재 Pod의 binding/RBAC 및 worker/relay 로드를 검증한 뒤 **정상 CI 43198146431 성공**, 실제 artifact·소스 c66f664·binding·정상 데이터 제거·기준선 보존·Lease 해제 영수증을 검증했다. 정상 CI 지표 보호 not-before **19:21:14.556 KST**를 보존 중이다.
- 새 실습 **202611120006** 준비 위치 `~/.local/share/url-shortener-ai-ops/c04-20261112/preparation-202611120006/`. 병합 commit에서 runtime 670파일을 새로 만들었고 정상 executor code hash·현재 worker 한정 C-02 조회 권한/1시간 파생 자격을 준비했다. **final 준비 exec session 8847**: 정상 CI 검증 후 cooldown이 끝나면 EKS/계정/ECR/대상 원본을 새로 읽어 시각 없는 intent/preflight를 생성한다. 상태 `final-preparation-state.json`을 먼저 확인한다.
- 아직 006의 Argo 토큰·원복 Secret·양쪽 감시·준비 영수증·실행 승인·activation·장애 주입은 없다. 다음은 양쪽 standby/실제 원격 UI ready 확인·실제 안내 bridge → 현재 intent/readiness hash의 새 사용자 승인 → 즉시 시작 → 새 PR의 정확한 head 실제 사람 리뷰·CI → C-03/C-04 120초 관찰·회수다. 005 승인과 PR #12의 기한 뒤 리뷰는 재사용하지 않는다. Terraform·유료 모델 API 미실행. 추천 **GPT-6 SOL · High**.

## 2026-11-12 19:07 KST · C-05 최종 852시험 통과·PR #152 CI 확인 중

- C-05의 실행 중 취소/장부/병합 뒤 수동 원복과 PR 안내 전 단계별 병렬 조회 보완을 **커밋 `d1b79754f3034a4abc9da70be72d45cc9f72571b`**, [PR #152](https://github.com/f-lab-edu/F-Lab-DevOps/pull/152)로 게시했다. 작업 트리 `.worktrees/c05-final-validation/`, 브랜치 `codex/c05-final-validation-20261112`, 기준 main 1b487a4. 공개 코드/시험/운영 문서 17파일만 포함하며 개인 실습 사건/자격은 게시하지 않았다. main 보호 설정은 변경하지 않았다.
- **최종 전체 852개 시험 통과(26.541초, skip 0)**, 최종 기한 보완 관련 84개도 통과했다. `/private/tmp/c05-final-full-tests.log`, `/private/tmp/c05-final-focused.log`에 근거가 있다. Helm/배포 정적·구문·diff 검사 통과. R4/R7 모의 10개에서 병합 전/후 취소·set 이후 취소·응답 유실 무재시도·Git 실제 원본 재검증·사망한 정확한 소유자의 원복과 중복 재개 거부·취소 중 승인된 원복 유지 등을 실제 실행 함수와 연결했다. 관찰 취소가 포워드를 정리하는 사례와 독립 읽기 phase barrier 검사를 추가했다.
- [C-05 운영 문서](.worktrees/c05-final-validation/ai-ops/docs/C5-execution-controls.md)와 assets/C5-local-controls-validation-20261112.json은 **로컬 통제 검증/새 실환경 복구 대기**로 명시한다. CLI `cancel_c4_trial.py`, 원복 `restore_c4_trial.py --journal`, 결과 execution_control의 confirmed_changes/pending_or_unconfirmed_operation과 automatic_resume_allowed=false를 사용한다. 취소로 이미 적용된 Git을 revert하지 않는다. 실제 main/PR/부모/한 필드 diff/고정 병합 App·기존 Lease를 다시 확인한 원복만 재개한다.
- 정확한 head의 CI [43197229454](https://github.com/f-lab-edu/F-Lab-DevOps/actions/runs/43197229454)의 scope·contracts·image·C03 Main PR Gate **4개 모두 통과**, CodeRabbit skipped다. 작성자/현재 계정 joungGo, 리뷰 0·REVIEW_REQUIRED·BLOCKED이며 main 리뷰1·strict CI·관리자 적용을 다시 확인했다. 사용자에게 **PR #152에 한해** 리뷰 수 1→0·squash 병합·즉시 0→1 복원을 허용할지 실제 async 질문을 전달했다(accepted=true). 아직 답변·규칙 변경·병합은 없다. **PR #151의 일회 보호 예외 승인은 #152에 재사용하지 않는다.** 실제 새 장애 실행 승인도 별도다. 현재 앱은 005의 정상 원복 후 상태이며 감시/임시 Argo 토큰/전용 Secret은 회수됐다. 소스 보완 중 새 장애·Terraform·모델 API를 실행하지 않았다.
- 독립 준비로 현재 정상 앱 Pod의 binding/RBAC 계획을 읽기 생성했고, `/private/tmp/c05-align-current-baseline.py`·**exec session 71363**에서 기존 실습 binding/RBAC·GitHub variable을 현재 Pod에 맞추고 **worker/relay 로드·앱/운영/PVC 보존 검증을 완료했다**(exit 0). 개인 자료 `~/.local/share/url-shortener-ai-ops/c04-20261112/c05-current-baseline/`. 앱/운영/PVC UID/spec 보존을 대조하며 새 장애·이미지 변경은 없다. main 반영 후에만 새 main 소스의 정상 CI를 실행한다. worker 교체 뒤 C-02 Pod 한정 Role/조회 자격을 새로 대조해야 한다.
- 남은 작업: #152 정상 main 반영 → 변경된 현재 정상 앱 Pod/worker의 기준선·C-02 조회 권한 갱신 → 새 main의 정상 CI → 새 runtime/양쪽 guard/실제 준비 영수증 → 새 사용자 장애 실행 승인 → 더 이른 새 PR 안내·정확한 head의 실제 사람 리뷰 → C-03/C-04 최소120초 관찰과 회수 → R1~R8 최종 판정·전체 정리. 추천 **GPT-6 SOL · High**.

## 2026-11-12 19:03 KST · 005 기한 뒤 리뷰·정상 원복 확인, C-05 보완 구현/시험 중

- PR #12 정확한 head f000d022…의 실제 joungGo APPROVED는 **18:48:07 KST**였다. 현재 계획 리뷰 마감 **18:47:59.100 KST**보다 약 **7.9초 뒤**여서 병합하지 않았다. `live/post-state.json`은 review_deadline_passed_no_execution_context, `live/result.json`은 **restored_without_c03_completion**·fault_injected=true다. 005 승인/리뷰는 종료됐으며 다음 실행에 재사용하지 않는다. 아래 진행 중·리뷰 대기는 종료 이력이다.
- 실제 **18:50:50 KST** 재검증: 정상 SHA 0cfef83c…·정상 이미지·Synced/Healthy·Ready 1·`/readyz=200`·`/items=200`·빈 Lease. 기존 보호 not-before **18:53:19.641 KST**를 유지했다. `preparation-202611120005/live/post-state-verified.json`에 근거를 보존했다. 준비 담당 59617은 종료했고 `standby/state.json` cleanup_complete=true·remote_stopped=true·Secret 삭제·두 Argo 토큰 revoked=true를 확인했다. C-04 실제 회복 관찰 완료는 아니다.
- 사용자 목표를 이어 C-05 잔여와 안내 지연을 **새 작업 트리 `.worktrees/c05-final-validation/`**, 브랜치 **codex/c05-final-validation-20261112**, 기준 main **1b487a4**에서 보완 중이다. 이전 worktree/개인 증거를 보존한다. 새 fault/guard/token/Terraform/모델 API는 시작하지 않았다.
- 변경: `_collect`의 독립 원본 GET·정상 CI/계정/클러스터/연결·ECR/Pod 읽기를 단계별 병렬화하고 마지막 원본 barrier를 유지한다. 실제 stage_history로 다음 안내 지연을 측정한다. `ExecutionControl`의 개인 원자 장부가 외부 쓰기 시도/확인/응답 불확실·취소를 기록하고 실제 복구 실행 함수에 연결됐다. 제안 취소 또는 현재 계획의 운영자 취소 파일을 병합/각 Argo 쓰기 전에 재검사하며 이미 병합한 Git을 임의 revert하지 않는다. 관찰 취소는 읽기/대기 경계에서 중지한다. 원복은 취소·장부 기록 실패에도 기존 승인 범위에서 허용한다.
- 새 `cancel_c4_trial.py`는 실제 gh 사용자와 기존 소유자/plan hash에 결합한 취소만 기록한다. `restore_c4_trial.py --journal`은 로컬 SHA만 믿지 않고 실제 GitHub PR/main/부모/한 필드 diff/고정 병합 App·기존 Lease 소유권을 대조해 **원복만** 재개한다. 중복 병합/자동 정상 실행 재개를 허용하지 않는다. 안내 자식의 SIGTERM은 Python finally를 거쳐 자기 App 토큰을 회수하도록 연결했다.
- 로컬 전체 **851개 시험 통과(28.299초)** 후 장부 쓰기 이후 기한 검사 등을 추가해 관련 **84개 시험 통과(1.008초)**. 안전 원복이 취소 때문에 막히지 않는 한 사례를 더 추가해 **최종 전체 시험 exec session 42628** 진행 중이다(`/private/tmp/c05-final-full-tests.log`). 초기 datetime 직렬화·시험의 전용 경로 설정 실패는 고쳐 집중 43/44개에서 재검증했다. Helm/배포 정적·구문·diff 검사 통과. **아직 C-05 최종 코드는 커밋/게시하지 않았고, 로컬 통과를 새 실환경 동작으로 주장하지 않는다.**
- 다음: 최종 시험/검토 → C-05 및 지연 개선 코드 PR/CI → 실제 main 반영 → 현재 Pod 정상 CI 기준선 재고정 → 새 준비 영수증·새 장애 실행 승인 → 더 이른 새 PR 안내/정확한 head의 실제 리뷰 → C-03/C-04 관찰/회수 → R1~R8 최종 판정과 문서 정리. PR #151의 일회 보호 예외는 새 PR에 재사용하지 않는다. 추천 **GPT-6 SOL · High**.

## 2026-11-12 18:46 KST · 005 실제 승인·장애 주입·복구 PR #12 실제 리뷰 대기

- 사용자가 **“예, 새 005 준비 결과로 즉시 실행 승인”**을 제출했고 실제 **18:44:24.285 KST**, 준비 만료 전 intent/readiness hash·프로세스 생존을 대조해 `live/approval.json`을 새로 기록했다. **18:44:29.100 KST**에 activation을 한 번 고정하고 양쪽 ACK 후 격리 앱 장애를 주입했다. `live/result-handoff.json`의 fault_injected=true·새 실패 인계를 확인했다. 이전 승인 대기는 종료 이력이며 현재 실행은 진행 중이다.
- 새 [복구 PR #12](https://github.com/joungGo/c03-execution-lab/pull/12/files), 정확한 head **`f000d0227ad32be45ce05c3b5dde7e4bb06fff47`**. 필수 C03 Main PR Gate는 성공했다. 실제 bridge session 24257의 요청을 실제 async 도구에 전달하고 accepted=true를 같은 stdin에 즉시 전달했다. 영수증 `live/review-notified.json`의 실제 안내 **18:45:58.282 KST**, 리뷰 마감 **18:47:59.100 KST**, 보장 창 **120.818초**다. IAB에 실제 PR 변경 화면을 열었다(사용자 직접 리뷰 대상). 아직 읽기 확인 시 실제 리뷰 0건·REVIEW_REQUIRED다.
- **현재 준비/회수 담당 exec session 59617**을 계속 관찰한다. 소유 원복 시작 목표 **18:48:29.100 KST**, 독립 감시 원복 시작 기한 **18:49:29.100 KST**. 원격 감시 43193403773, runner PID 14630, 로컬 guard PID 13893. bridge 24257은 한 건 전달 후 정상 종료했다. 새 GitHub 실제 head 리뷰/CI 확인 → 병합/동기화/C-03 인계 → C-04 최소120초 관찰 → 토큰/Secret/프로세스 회수를 이어간다. 실제 리뷰는 chat 답변으로 합성하지 않는다. Terraform·유료 모델 API 미실행, C-04/C-05 전체 완료는 아직 아니다.

## 2026-11-12 18:41 KST · 새 005 실제 준비 완료·새 실행 승인 대기

- **현재 trial 202611120005**. intent hash **`02bed139e6c3abfc1a9fbcecc08f19c91f866e9b445fa236d4cc4d3e63cf1f48`**, readiness hash **`9dc656878ee420b1e2a87306c5b89bf797a13e3b02840857c9f542cbac7ea20d`**. 실제 준비 완료 **18:41:32.983 KST**, 만료 **18:46:32.983 KST**. 이 결과의 새 실제 async 실행 승인을 요청했고 accepted=true다. **아직 승인 답변·approval.json·activation·장애 없음.** 이전 004 승인 답변은 만료 후였으므로 재사용하지 않았다.
- 개인 위치 **`~/.local/share/url-shortener-ai-ops/c04-20261112/preparation-202611120005/`**, runtime 소스/manifest는 검토된 main 1b487a4 / `8a761c151426b822a8ff24e3c992f91d0e6091e0369c58024cbd516307b399c1`다. 정상 CI 43187116785·현재 worker/collector binding·실제 대상/ECR/Git 보호·자격 회수 상태를 다시 검증했고 새 1시간 C-02 현재 Pod 조회 자격을 준비했다. 최초 문자열 run ID 입력 거부는 검증 함수의 정수 타입으로 정규화해 재검증 성공했으며 원격 상태/설정은 변경하지 않았다.
- **현재 준비 담당 exec session 59617**, runner PID **14630**, 로컬 guard PID **13893** / guard ID `local:13893:4d6f1ede51f14d3f904d448f817683c7`. **실제 안내 bridge exec session 24257**, 소켓 `/private/tmp/c04-202611120005/review.sock`. 원격 [guard 43193403773](https://github.com/f-lab-edu/F-Lab-DevOps/actions/runs/43193403773), job `131098202977`, guard ID `remote:43193403773:4c0e025a506a4fe2bc79e578c465a5c1`. 필수 environment 승인은 GitHub UI에서 정상 처리했고 실제 stdout JSON을 화면에서 읽어 `standby/remote-ready-observed.json`에 기록했다. 최신 상태는 `standby/state.json`·`live/readiness.json`·`live/result.json` 존재 여부를 확인한다.
- 실제 사용자 새 승인 답변을 받으면 위 정확한 두 hash와 receipt 유효기간·runner/guard 생존·결과/activation 부재를 대조하고, 같은 runtime의 ReadyApproval로 현재 실제 승인 시각·actor 149856960을 결합해 **`live/approval.json`을 새로 기록**한다. 만료됐으면 기록하지 않는다. 승인 뒤 달력 대기 없이 즉시 현재 조건/양쪽 ACK/15초 시작 예산을 검증한다.
- bridge session **24257**에 `review_notice_requested`가 나오면 arguments를 실제 request_user_input_async로 전송하고 실제 accepted 응답을 받은 직후 **같은 stdin**에 `{"delivery_tool_response":{"accepted":true}}`+개행을 전달한다. PR 링크·정확한 head·실제 리뷰 마감은 즉시 안내하고 브라우저로 열어 리뷰 진입을 돕는다. chat 답변만으로 GitHub 리뷰를 합성하지 않는다. T+90 안내/최소120 리뷰/T+210 리뷰 마감/T+240 소유 원복 목표/T+300 독립 원복 시작/C-03 인계 후 최소120 관찰을 유지한다.
- 준비 담당자가 C-03 영수증을 보면 감시 둘을 종료하고 최종 결과 뒤 자기 Argo 45분 토큰·원복 Secret·파생 설정을 회수한다. 만료에도 주입 없이 종료·회수한다. 마지막 실물 검증과 C-05 R4/R7·최종 데모/정리까지 사용자 목표를 이어간다. 아직 C-04/C-05/전체 AI 완료가 아니다. Terraform·유료 모델 API 미실행. 추천 **GPT-6 SOL · High**.

## 2026-11-12 18:37 KST · 004 준비 만료 후 답변 수신·주입 없이 정리 확인·005 재준비

- 사용자가 004 준비 hash에 “예, 이 준비 결과로 즉시 장애 주입·복구 검증 승인”을 답했으나 실제 처리 시작 시각은 **18:36:49 KST**로 영수증 만료 **18:17:33.800 KST** 이후였다. 승인 기록 전에 시각 검사가 거부해 **approval.json/activation 없음·장애 주입 0건**이다. 기존 만료 승인을 새 intent에 재사용하지 않는다. 아래 18:12 승인 대기는 종료 이력이다.
- 004 `live/result.json`은 stopped·fault_injected=false. `standby-retry1/state.json`은 runner_exit=1·cleanup_complete=true·remote_stopped=true·secret_removed=true·두 token revoked=true다. 실물 조회로 원격 guard 43189174309 completed/cancelled, c03-sync/c03-rollback spec/status 토큰 부재, GitHub environment의 C03_ROLLBACK_ARGOCD_TOKEN 부재를 확인했다(기존 S10_ARGOCD_TOKEN은 보존). bridge session 64206도 종료했다.
- 사용자 목표를 이어 **새 trial 202611120005**를 준비한다. `/private/tmp/c04-ready-005-prepare.py`·exec session **81797**가 현재 대상/ECR/Git/정상 CI/worker/PVC를 다시 대조한다. 개인 위치 `~/.local/share/url-shortener-ai-ops/c04-20261112/preparation-202611120005/`, 소스는 같은 검토된 main 1b487a4다. 기존 정상 CI 43187116785와 배포 image는 변경하지 않고 현재 binding과 다시 대조한다. 실제 준비 영수증이 발행된 뒤 새 승인을 요청한다. C-05 R4/R7 경계와 최종 판정은 계속 남아 있다. Terraform·유료 모델 API 미실행. 추천 **GPT-6 SOL · High**.

## 2026-11-12 18:12 KST · C-04 현재 준비 완료·실행 승인 대기 (202611120004)

- **현재 실제 준비 영수증**: trial `202611120004`, intent hash `94f06f24cc4937f57f88c9971ba47b46d8954c80dc3b5e97a7f653d43677e5db`, readiness hash `c4193be89e4c908e00a0e98f240d218184e4e7256113c3ed93da407c5869baff`. 준비 완료 **18:12:33.800 KST**, 만료 **18:17:33.800 KST**. 실제 async 도구로 이 hash들의 새 장애 주입/복구 실행 승인을 요청했고 accepted=true다. **사용자 승인 답변·approval.json·activation·장애는 아직 없다.** 만료한 경우 이 영수증으로 시작하지 않는다.
- 개인 작업 위치 `~/.local/share/url-shortener-ai-ops/c04-20261112/preparation-202611120004/`. 현재 준비 담당자는 **exec session 13471**, PID는 `standby-retry1/state.json`. **실제 안내 bridge exec session 64206**, 소켓 `/private/tmp/c04-202611120004/review.sock`. 실제 runner PID **6148**, 로컬 guard PID **5432**·ID `local:5432:058494c8f835416ba063ec658d562bfd`, 원격 [guard 43189174309](https://github.com/f-lab-edu/F-Lab-DevOps/actions/runs/43189174309)·ID `remote:43189174309:7279acd2ecdd4b6eade5ec6bb699f1ca`. GitHub UI에서 environment를 승인하고 job `131083839458`의 실제 JSON marker를 직접 확인했다. `standby-retry1/remote-ready-observed.json`·`standby-guards.json`에 원본 관찰을 연결했다.
- runner가 실제 TLS 사건 조회·현재 정상 기준선·양쪽 프로세스/run/step/source와 정상 CI 43187116785를 검증해 `live/readiness.json`을 발행했다. runtime manifest sha256 `8a761c151426b822a8ff24e3c992f91d0e6091e0369c58024cbd516307b399c1`, 소스 main `1b487a4315d0bb5e607c159bf66ef37e17166471`이다. 새 이미지와 worker 정상 CI ready 요약·C-02 권한 검사는 앞선 기록 참조. 실제 Argo 단기 토큰은 프로세스 환경과 기존 GitHub 전용 Secret에만 연결돼 있으며 값은 출력/파일 보관하지 않았다. metadata는 개인 state에 있다.
- **재개 우선순위**: 사용자 실제 승인 답변을 받으면 만료/runner 생존/결과 미생성을 확인하고 같은 runtime의 ReadyApproval로 **실제 승인 시각**·현재 intent/readiness hash·actor 149856960을 결합해 `live/approval.json`을 새로 쓴다. 그 외에는 파일을 만들지 않는다. 바로 runner/bridge를 관찰한다. 새 PR 안내가 bridge stdout에 나오면 arguments를 실제 request_user_input_async에 전달하고, 실제 accepted 응답을 받은 즉시 **같은 session 64206 stdin**으로 `{"delivery_tool_response":{"accepted":true}}`+개행을 보낸다. GitHub PR 리뷰는 실제 원본 기록과 CI로 별도 확인한다. 실제 안내 120초·T+210 리뷰 마감·T+240 소유 원복 목표·T+300 독립 원복 시작 기한을 유지한다.
- 준비 담당자가 `live/result-c03.json`을 확인하면 양쪽 guard를 중지하고 runner의 C-04 관찰이 끝난 뒤 자기 토큰/Secret/파생 설정을 회수한다. 준비 만료에도 runner가 주입 없이 종료하고 회수한다. 최종 `standby-retry1/state.json` cleanup_complete·원격 completed·역할 token/Secret 부재·앱 정상·빈 Lease·worker/PVC 보존을 실제 다시 확인한다. **이 상태는 C-04/C-05 완료가 아니다.**
- 첫 standby 시도는 누락된 `projection-probe.json` 입력으로 runner CLI가 승인 전 거부했다. guard 43188634487 종료·두 토큰 회수·Secret 삭제·cleanup_complete=true를 확인하고 보존했다(`standby/`). 기존 bridge 76018은 종료했다. 조회 점검 파일을 복원하고 실제 parse_args로 전체 입력 경로를 검증했으며 운영자 전용 소켓 root를 AI_OPS_FILES_ROOT에 등록했다. 첫 anchor는 `standby-guards-first-attempt.json`으로 보존했다. 새 프로세스/guard를 사용하되 **실행 승인을 소비한 적이 없으며 장애 0건**이다.
- 이후 사용자 목표대로 C-05 R1~R8의 남은 R4/R7 경계·실제 R2·최종 데모/절차/완료 판정을 이어간다. 현재 21개 로컬 매핑은 사전 대조다. Terraform·유료 모델 API 미실행. 추천 **GPT-6 SOL · High**.

## 2026-11-12 18:02 KST · 오늘 AI 구현 완료 목표·C-04 새 이미지와 정상 CI 검증, 준비 진행 중

- 사용자 요청: C-04 실환경 검증 → C-05 R1~R8/최종 데모/정리까지 진행하고, 실제 실행 준비가 끝나면 승인 내용을 안내한다. **아직 새 장애 실행 승인은 없다.** 새 trial **202611120004**, 개인 위치 `~/.local/share/url-shortener-ai-ops/c04-20261112/preparation-202611120004/`다. 처음 준비한 intent는 정상 코드 binding 차이를 발견한 준비 초안이며 최종 영수증/승인이 아니다. `final-preparation-state.json`을 현재 상태로 읽는다.
- 기존 `urlshortener-operator` 세션으로 계정/ARN·실습 앱 정상·PVC·Git 보호·ECR 정상/실패 digest를 확인했다. 새 main 1b487a4의 b6_coordination 변경으로 정상 CI executor hash가 기존 배포와 달라 **새 검증 이미지**를 등록했다. [이미지 CI 43186403868](https://github.com/f-lab-edu/F-Lab-DevOps/actions/runs/43186403868) 성공, 이미지 `urlshortener-practice-ai-ops@sha256:ea168dc3a4d24ce8baa999446b4a4eeaec2f6aeabb84558536fe0a63e5181c10`. 필수 environment 승인은 GitHub UI로 정상 처리했다.
- 기존 collector의 자료 33항목/26,594 bytes를 `collector-incidents-before-image.tar.gz`로 먼저 로컬 보존했다(sha256 `aa1d841ba4e495d9af1acea6087d089ba9a4a98519164781283a8b8ed67924b6`). 실습 collector·ai-ci worker/조회 sidecar·relay만 새 이미지로 맞췄다. 정상 binding/RBAC/GitHub variable을 현재 collector에 고정했고 앱/운영 Deployment·기존 PVC의 UID/spec 불변을 대조했다. worker·relay Ready, 기존 Secret/로그인/App 키는 보존했다. worker Pod 교체에 따른 C-02 Pod 한정 Role을 갱신하고 기존 한정 자격 예외로 1시간 조회 kubeconfig를 새 개인 파일에 발급했다. 원문은 출력하지 않았다.
- [새 정상 CI 43187116785](https://github.com/f-lab-edu/F-Lab-DevOps/actions/runs/43187116785)의 성공/정제 artifact를 검증했고, 수집기 내부 `source.json`이 completed·관측 4개임을 원문 없이 확인했다. 기존 공유 잠금의 지표 보호 not-before **18:06:38.047 KST**까지 그대로 기다린다. 현재 **최종 준비 exec session 45210**, 스크립트 `/private/tmp/c04-ready-004-final-prep.py`가 이 기한 뒤 ECR/대상/역할/정상 상태를 다시 읽어 최종 intent/preflight를 생성한다. 아직 단기 Argo 토큰·원복 Secret·양쪽 standby·실제 승인·장애는 없다.
- 다음 준비 담당 스크립트 `/private/tmp/c04-ready-004-standby.py`를 작성·구문 확인했다. 실제 실행 전에 검토하고, 기존 범위의 c03-sync/c03-rollback 45분 토큰을 화면/로그에 출력하지 않고 runtime 프로세스 env/기존 GitHub environment Secret에만 연결한다. 새 workflow의 필수 배포 승인도 UI에서 처리하고 실제 원격 준비 JSON marker를 본 뒤 `standby/remote-ready-observed.json`에 관찰 근거를 기록한다. 로컬 marker/원격 run/step/source를 대조한 다음 별도 bridge stdin을 호스트가 보유한 상태에서 runner를 시작한다. 준비 영수증 300초 내 실제 승인만 수용한다. C-03 영수증 발행 즉시 양쪽 감시를 중지하고 runner 종료 후 자기 토큰/Secret/파생 설정을 회수하도록 구성했다. **승인 파일을 먼저 만들지 않는다.**
- C-05 사전 대조 `/private/tmp/c05-control-map.py`가 실제 R1~R8 관련 **21개 로컬 시험을 통과**했다. `/private/tmp/c05-control-map-20261112/{matrix.json,tests.log}`에 기록했다. 이는 전체 완료 판정이 아니다. 현재 C-04 실측 R2, 병합 이후 재시작의 보존/재개 경계(R4), 실행 중 취소의 적용/미실행 구분(R7), 최종 데모·종료 판정이 남는다. 과거 A5/A7 비교·사람 평가 기록도 기존 범위 결정과 대조해야 한다. 미측정을 실측으로 바꾸지 않는다.
- 준비 중 최초 C-02 명령을 시스템 Python으로 호출해 pydantic 부재로 거부된 이력이 있다. 검증된 기존 venv로 수정해 성공했다. Terraform apply/destroy·유료 모델 API·장애 주입은 없다. 기존 한정 실습 자격 예외는 계속 적용하며 AI 구현 완료 통보로 계산하지 않는다. 추천 **GPT-6 SOL · High**.

## 2026-11-12 17:40 KST · PR #151 병합·보호 복원·준비 완료 승인 워크플로 등록 확인

- 사용자가 **“예, PR #151만 임시 변경·병합·즉시 복원”**을 승인했다. head `8cd9ba53cd6ff5997c7d86397241faba1473d5dd`·기존 base·검사 7개 통과를 직전 재검증한 뒤 리뷰 수만 1→0으로 바꾸고 [PR #151](https://github.com/f-lab-edu/F-Lab-DevOps/pull/151)을 squash 병합했다. **병합 commit `1b487a4315d0bb5e607c159bf66ef37e17166471`**, 17:39:28 KST. finally에서 즉시 리뷰 수를 1로 복원하고 **전체 보호 규칙 JSON이 원본과 동일**함을 확인했다. strict C03 Main PR Gate·관리자 적용 등 다른 규칙은 유지했다. 아래 병합 승인 대기는 종료 이력이다.
- 병합 tree와 검증한 PR head의 diff는 0이다. main의 [후속 CI 43184754167](https://github.com/f-lab-edu/F-Lab-DevOps/actions/runs/43184754167)은 **completed/success**다. 새 `.github/workflows/ai-ops-c4-ready-rollback.yml`은 GitHub workflow ID `415750847`, **state active**로 등록됐고 원격 소스 바이트와 병합 commit의 파일이 일치한다(sha256 `8b18688a97a39ba0adde4de30047b8bb072a922e0c14e21fc98bab3c38d8c713`). 트리거는 workflow_dispatch만 있으며 **dispatch하지 않았다**.
- 개인 과거 runtime에 의존하지 않고 **병합 commit으로 새 665파일 runtime**을 만들고 manifest를 검증했다. CLI 보존 경로는 전용 `AI_OPS_FILES_ROOT`를 명시한다. 최초 추가 경로 미등록 거부는 설정 후 해결했으며 코드·정책은 변경하지 않았다. 영구 보존: **`~/.local/share/url-shortener-ai-ops/c04-20261112/ready-start-published-1b487a4/`**의 `runtime/`, `publication-verified.json`, `workflow-source.json`, `result.json`, `protection-before.json`, `protection-during.json`, `protection-after.json`, `checks-before.json`, `merge-response.json`. 사용자 전용 폴더/파일 권한 0700/0600, 자격 없음. 임시 원본 `/private/tmp/c04-pr151-merge-proof/`도 있다.
- 기존 로컬 전체 **840시험**·GitHub PR 검사 **7개** 통과 근거를 유지한다. 동일 소스이므로 이 병합 단계에서 전체 로컬 시험을 반복하지 않았다. 현재 게시 작업 트리는 `.worktrees/c04-ready-publication/`(head 8cd9ba5), fetch된 origin/main은 1b487a4다. 기존 작업 트리·자격·설치 자원·PVC·사건 기록은 보존했다. **실제 standby 감시 실행·새 자격 발급·Secret 연결·장애 주입·Terraform은 수행하지 않았다. C-04 실환경 성공이 아니다.**
- 다음: 병합 runtime으로 새 시각 없는 intent 준비 → 실제 양쪽 standby/ACK/회수 경로 확인 → 현재 준비 영수증의 hash에 대한 새 사용자 실행 승인 → 즉시 시작 → 새 복구 PR의 정확한 head에 대한 실제 사람 리뷰/CI → 병합·동기화/C-03 인계 → C-04 최소 120초 관찰 → 양쪽 감시 종료 및 임시 자격/Secret 회수. **기존 실행 승인·PR #11 리뷰·이번 코드 병합 승인을 장애 주입에 재사용하지 않는다.** 실제 안내 후 최소 120초 리뷰·T+210초 리뷰 마감·T+240초 소유 원복 목표·T+300초 독립 원복 시작 기한은 유지한다. 추천 **GPT-6 SOL · High**.

## 2026-11-12 C-04 준비 완료 승인 방식 · PR #151 검증 완료·병합 조건 승인 대기

- 사용자 “진행해”에 따라 변경 검토와 원격 감시 코드 반영을 진행한다. **실제 감시 dispatch·임시 자격 연결·장애 주입·Terraform은 아직 하지 않았다.** [PR #151](https://github.com/f-lab-edu/F-Lab-DevOps/pull/151), head **8cd9ba53cd6ff5997c7d86397241faba1473d5dd**를 게시했다. 현재 main은 63e220e…이며 보호 규칙(리뷰 1명·strict CI·관리자 적용)은 유지한다. PR 작성자와 현재 GitHub 사용자가 joungGo로 같아 별도 리뷰는 아직 필요하다. 과거 PR별 임시 예외 승인을 재사용하지 않는다.
- 현재 게시 작업 트리는 **`.worktrees/c04-ready-publication/`**, 브랜치 `codex/c04-ready-publication-20261112`다. 이전 `.worktrees/c04-recovery-verification/`와 개인 실습 기록은 보존했다. 게시 브랜치는 최신 origin/main에서 만들고 코드·시험·필요 문서만 선택해 담았다. 개인 사건/자격·과거 실습 계획은 게시하지 않았다.
- 검토에서 실제 저장소 체크아웃의 C-03 모듈 누락(787시험·12skip)을 확인했다. 실제 사용한 C-03 코드/시험을 함께 포함해 필수 통합 시험을 건너뛰지 않게 했다. 저장소의 더 최신인 격리 원복 안전 검사는 유지했다. Lease/안내 소켓 accept 도중 activation 게시 경합, 공통 원복 함수의 kubeconfig 인자 누락, 스키마 exporter 누락과 CI 범위/Helm 준비 순서를 수정했다.
- 저장소 기준 전체 **840시험(27.404초) 통과**, 경로/안내 29개 집중 시험 통과. 고정 commit `0e89057490a0de351a11befc020d597149957cac`에서 **개인 runtime 없이 새로 만든 독립 묶음 665파일의 840시험(27.779초)**도 통과했다. 새 `build_c4_runtime.py`로 Git commit의 공개 `ai-ops/`·`.github/`만 추출하며 미커밋 자격·symlink를 거부한다. 기존 831시험 결합 묶음은 이력이며 새 실행은 게시/병합된 commit에서 다시 빌드한다. fixture 4종·schema 재생성·Helm/배포 정적 검사·구문 검사 통과.
- 세 번째 테스트 전용 커밋은 경로 대역 실패를 확인하기 전에 브랜치에 먼저 게시된 이력이 있다. main에는 반영하지 않았고 실제 원인과 추가 accept 경합을 수정한 최종 전체 시험·집중 시험 및 종료 코드 0을 확인해 head 8cd9ba5에 반영했다. 필수 CI는 이 최종 head만 기준으로 확인한다.
- GitHub CI: AI 검사 [43183353910](https://github.com/f-lab-edu/F-Lab-DevOps/actions/runs/43183353910), dependency [43183353906](https://github.com/f-lab-edu/F-Lab-DevOps/actions/runs/43183353906). 최종 head 8cd9ba53cd6ff5997c7d86397241faba1473d5dd의 C03 Main PR Gate·contracts·image·application·helm·scope·terraform 7개 모두 통과, CodeRabbit은 skipped다. 리뷰 0건·REVIEW_REQUIRED·BLOCKED다. main 리뷰 1명·stale dismiss·strict C03 Main PR Gate·관리자 적용을 다시 읽어 확인했다. 작성자와 현재 계정이 같아 자체 승인이 불가능하다. 사용자에게 이번 PR #151에 한해 리뷰 수 1→0·squash 병합·즉시 0→1 복원(필수 CI/관리자 적용 유지)을 허용할지 실제 async 질문을 전달했다. 아직 답변·규칙 변경·병합은 없다. 기존 `ai-ops-lab-ci` environment의 joungGo 승인·custom branch policy와 S10_CI_ROLE_ARN 변수 이름을 읽기 확인했고 변경하지 않았다.
- 로컬 검증 근거: `/private/tmp/c04-publication-reviewed-final.log`, `/private/tmp/c04-publication-portable-corrected-v2.log`, `/private/tmp/c04-publication-verification/`의 source.json/runtime/full-tests.log. 공개 요약은 게시 트리 `ai-ops/docs/assets/C4-ready-publication-validation-20261112.json`, PR 본문은 `/private/tmp/c04-ready-publication-pr.md`다. 다음은 실제 답변을 받은 뒤 승인된 병합 방식으로만 진행하고, 보호 규칙 복원·main 소스/워크플로 등록을 확인한다. 이후 실제 standby/ACK/회수 경로 확인과 새 준비 영수증의 실행 승인이 필요하다. 실제 장애는 새 준비 영수증에 대한 별도 승인이 필요하다. 추천 **GPT-6 SOL · High**.

## 2026-11-12 17:10 KST · 고정 시각 대기 제거·준비 완료 후 승인 시작 로컬 검증 완료

- 사용자가 **“고정 시각까지 기다리지 않고 준비 완료 후 승인하면 바로 시작…리뷰 시간과 원복 기한 유지…먼저 로컬 검증”**을 요청했다. **기존 17:00 계획 202611120003은 미승인 폐기**했다. 개인 `superseded.json`과 공개 준비 JSON에 기록했으며 authorization/감독기/실행 흔적 없이 종료된 준비 자료로 보존했다. 아래 17:00 승인 대기 기록은 종료 이력이며 이후 답변으로 재기동하지 않는다.
- `.worktrees/c04-recovery-verification/`에 새 **시각 없는 intent → 양쪽 standby 감시 → 실제 현재 준비 영수증 → 그 intent/readiness hash의 실제 사용자 승인 → T 한 번 고정 → 동일 기한 ACK → 즉시 주입** 경로를 구현했다. 기존 고정 감독기의 12분/5분 대기를 새 경로에서 사용하지 않는다. 새 준비 유효기간은 **300초**, 시작 handshake/첫 쓰기는 T부터 **15초 이내**로 제한한다. 만료/다른 승인/false·숫자 승인/프로세스 재기동/시계 변경/대상·코드·감시 변경/ACK 부족은 주입 없이 중지한다. 실패해도 기한을 밀거나 승인을 재사용하지 않는다.
- **실제 안내 뒤 최소 120초 리뷰**, T+210초 정확한 head 리뷰, T+240초 소유 원복 목표, **T+300초 독립 원복 시작 기한**, 실제 C-03 인계 후 **최소 120초 C-04 관찰**을 유지한다. 주입이 T보다 늦어져도 기한을 늘리지 않아 실제 장애 이후 300초를 넘지 않는다. 준비 승인과 새 PR의 실제 사람 리뷰는 별도다. actual accepted 응답 → 동일 bridge stdin → 원자적 안내 영수증도 유지했다.
- 새 실제 진입점: `prepare_c4_trial.py --when-ready`, `run_c4_ready_guard.py`, **`run_c4_ready_trial.py`**, `bridge_c4_review_notice.py --when-ready --activation …`. 원격 준비 감시 선언은 `.github/workflows/ai-ops-c4-ready-rollback.yml`이며 environment/OIDC/동시 실행 제한과 기존 원복의 잠금 탈취 금지를 유지했다. **원격 workflow는 아직 게시/실행하지 않았다.** 새 실행기는 기존 C-02/새 PR/정확한 리뷰·CI/C-03 복구/C-04 관찰 함수를 공유한다. 감시 활성화/첫 쓰기 전에 `active-plan.json`, 파생 승인, `result-owner.json`을 남겨 기존 수동 원복이 소유자를 대조할 수 있게 했다.
- 최종 **831개 전체 로컬 시험 통과(26.262초)**. 외부 네트워크와 실제 AWS/GitHub/Kubernetes/Terraform/Helm 실행은 차단하고 로컬 소켓·임시 가짜 CLI·오프라인 helm template만 허용했다. 최종 차단 시도 **0건**, 실제 API/자격 발급/장애/Terraform **0건**. Schema 4개·YAML 승인/환경 설정·CLI help 4개·Python 구문 21파일·보존 C-03 핵심 5파일 불변도 확인했다. 초기 소켓 bind 제한·새 테스트의 소켓 준비 경합·테스트 가짜 CLI까지 막은 오프라인 가드 설정을 수정한 이력은 임시 로그에 남기고 최종 통과 결과와 구분한다.
- 영구 보존: **`~/.local/share/url-shortener-ai-ops/c04-20261112/ready-start-local/`**의 `runtime/`, `full-verified.log`, `static-validation.json`, `verification.json`, `offline/`. 최종 runtime **671파일**, manifest **`391fe5a37d07f658e02f4e437081824f39e416f547be064d8a97a49345987ff9`**. 임시 이력은 `/private/tmp/c04-ready-local-20261112/`이며 재개는 영구 보존의 최종 runtime만 사용한다. 이전 17:00용 runtime은 새 실행에 사용하지 않는다.
- [구현/사용 절차](.worktrees/c04-recovery-verification/ai-ops/docs/C4-ready-start-local-20261112.md), [결과 JSON](.worktrees/c04-recovery-verification/ai-ops/docs/assets/C4-ready-start-local-20261112.json). 코드·문서는 아직 커밋/푸시하지 않았고 원격 변경·실환경 실행은 하지 않았다. 기존 설치 자원/로그인/App 키/PVC/사건 근거는 이 로컬 작업에서 변경하지 않았다. **C-04 실환경 완료가 아니다.**
- 남은 작업: 변경 검토/CI/정상 병합으로 새 원격 감시 소스 반영 → 실제 standby/ACK/회수 경로 확인 → 새 intent/현재 준비 영수증의 실행 승인 → 새 PR 정확한 head의 실제 사용자 리뷰/CI → 병합·동기화/C-03 인계/C-04 120초 관찰 → 임시 자격 회수. 실제 C-03 인계가 확인되면 준비 담당자가 양쪽 감시를 종료하고 최종 결과 뒤 자기 token/Secret/파생 설정을 회수해야 한다. 이 코드 변경 요청이나 PR #11 리뷰를 실제 장애 승인으로 계산하지 않는다. 추천 **GPT-6 SOL · High**.

## 2026-11-12 16:31 KST · 새 17:00 C-04 계획 사전 검증 완료·실행 승인 대기

- 사용자 “진행해”에 따라 [새 계획 202611120003](.worktrees/c04-recovery-verification/ai-ops/docs/C4-live-trial-202611120003.md), hash **`3f68a28da19e45af07009bb5235cf02861b848870f96969b41d02a06c7498018`**를 준비했다. **실행 승인·감독기·감시·장애는 아직 없다.** 이전 001/002 승인과 PR #11 늦은 리뷰를 재사용하지 않는다. Terraform 미실행.
- 예정 장애 **오늘 17:00 KST**, supervisor 시작/준비 시작 **16:48 직전/16:48**, 감시 기동 **16:50**, 실제 실행기 준비 마감 **16:55 전**, 자동 진입 **16:59:30**. 새 PR 안내 마감 **17:01:30**, 정확한 head의 실제 사용자 리뷰 마감 **17:03:30**(실제 안내 영수증 뒤 최소 120초), 소유 원복 목표 **17:04**, 독립 감시 원복 시작 기한 **17:05**, C-03 인계 뒤 C-04 관찰 최소 120초. 승인 **16:40까지 권장**. 새 PR이 나오면 즉시 실제 안내와 영수증 처리를 우선하고 브라우저에도 새 PR을 열어 리뷰 진입을 돕는다. 사용자가 직접 GitHub 리뷰를 제출해야 한다.
- 실제 operator AWS/ECR·앱 정상/빈 Lease·이전 cooldown 종료·worker/PVC/relay·Project 최소 권한·Git seed/CI/리뷰/관리자 보호·병합 App·Job dry-run·원복 자격/활성 run 부재·조회 전후 불변 검증 통과. 자동 snapshot/cache 세 번 **1.23/1.21/1.17초**. 기존 정상 CI **43167289475**는 새 실행한 것이 아니라 현재 binding·원격 artifact·영수증·worker ready를 재검증했다. 현재 Pod 한정 C-02 1시간 자격 갱신과 실제 TLS/bundle 검증 성공, 만료 **17:29:10 KST**다. 원문 자격은 출력하지 않았다.
- 코드 변경 없음. 이전 **800시험 통과** 동일 runtime 658파일을 새 폴더에 복사해 전체 파일·SDK·manifest **`d303076fb0deffbfba4768109198ee252277252b66cd51d98a9b53d40f0de7ff`**를 검증했다. 이번 준비에서 800시험을 다시 실행한 것은 아니다. 새 45분 Argo 토큰·권한·실제 양쪽 감시는 새 실행 승인 뒤 검사한다.
- 개인 자료 **`~/.local/share/url-shortener-ai-ops/c04-20261112/preparation-202611120003/`**의 `plan.json`, `preflight.json`, `release-summary.json`, `normal-binding.json`, `normal-ci-verified.json`, `worker-baseline-recheck.json`, `projection-probe.json`, `projection-live-read.json`, `current-c2-access.json`, `c2-kubeconfig.json`, `live-readonly-final.json`, `runtime/`, `guard-runtime/`이다. 소켓 루트 `/private/tmp/c04-202611120003`. 실제 안내 `{"accepted":true}`를 받은 즉시 동일 bridge stdin으로 `{"delivery_tool_response":{"accepted":true}}`와 개행을 전달하며 도구 응답을 합성하지 않는다. 기한 실패 시 주입 없이 종료하고 자동 연기하지 않는다. 추천 **GPT-6 SOL · High**.

## 2026-11-12 16:25 KST · PR #11 실제 늦은 승인 확인

- 사용자 “승인” 답변 뒤 GitHub 실제 리뷰 **5870666095 / joungGo / APPROVED**를 확인했다. head **832731739f1a61073da821f4e6314e2a73192f8e**는 일치하지만 제출 시각 **16:24:51 KST**는 마감 **16:18:30**보다 **6분 21초 늦다**. 따라서 이전 “기한 내 승인 없음” 판정은 유지한다.
- PR #11은 OPEN·미병합이며 종료된 202611120002를 재시작하지 않았다. 정상 원복·임시 자격 정리 완료 상태와 C-04 미완료 판정을 유지한다. 이 리뷰를 새 실습 승인이나 새 PR head 리뷰로 재사용하지 않는다. 개인 근거 `preparation-202611120002/late-review-observed.json` 및 실행 문서·결과 JSON에 기록했다. 다음 추천 **GPT-6 SOL · High**.

## 2026-11-12 16:21 KST · 16:15 실습 종료·정상 원복·임시 자격 정리 완료

- 승인된 **202611120002 / `d61531ec8e6ec320c6882d211f6c1baf0969fdc00c9d9bdf1db3393af884e139`**를 실제 실행했다. 자동 준비 **16:07:07**, 진입 **16:14:30**, 장애 주입 **16:15:01.279 KST**. 격리 앱 실패와 새 규칙 사건 `inc-fa93296acf784f00acaffbc3898ab0ba` / `run-fbe149661e0c4603ab0a14714da07d6b`를 확인했다.
- [복구 PR #11](https://github.com/joungGo/c03-execution-lab/pull/11/files), 정확한 head **`832731739f1a61073da821f4e6314e2a73192f8e`**는 필수 CI 통과. 실제 도구 accepted 응답을 동일 bridge stdin에 즉시 전달했고 실제 안내 영수증 **16:16:28.072**, 리뷰 마감 **16:18:30**까지 121.928초였다. **마감까지 실제 승인 리뷰가 없어 미병합**으로 보존했다.
- 결과 **`restored_without_c03_completion`**, 사유 **`review_deadline_passed_no_execution_context`**. 소유 실행기가 정상 SHA **0cfef83c36578090dfc1b65513789b1b10159e03**로 복원하고 자기 Lease를 해제했다. **C-03/C-04 완료가 아니다.** 종료된 승인·계획·늦은 리뷰를 재사용하지 않는다. 아래 대기 중 기록은 전부 종료 이력이다.
- 최종 **16:20:57** 실물 검증: `lab-target` Synced/Healthy·정상 digest·Ready 1·`/readyz=200`·`/items=200`·빈 Lease. 기존 5분 지표 창의 다음 주입 제한 **16:23:49.140 KST**를 그대로 보존했다.
- c03 역할 토큰 둘 회수·원복 Secret 부재·원격 감시 [43172645824](https://github.com/f-lab-edu/F-Lab-DevOps/actions/runs/43172645824) completed/cancelled·로컬 supervisor/guard/runner/PR 연결기 종료·bridge 종료·소켓/파생 core 설정 제거·이번 완료 조사 Job UID 확인 후 삭제를 검증했다. 첫 취소 대기 조회의 `cleanup_complete=false`는 실제 취소 완료 재조회 뒤 최종 **true**로 갱신했다. 기존 로그인·App 키·C-02 제한 자격·worker/relay·PVC·사건 근거는 보존, Terraform 미실행.
- 개인 자료는 `~/.local/share/url-shortener-ai-ops/c04-20261112/preparation-202611120002/`의 `execution-closed.json`, `release-summary.json`, `live/result.json`, `live/result-handoff.json`, `live/review-notified.json`, `live/post-state.json`, `live/state.json`이다. [실행 기록](.worktrees/c04-recovery-verification/ai-ops/docs/C4-live-trial-202611120002.md), [결과 JSON](.worktrees/c04-recovery-verification/ai-ops/docs/assets/C4-execution-202611120002.json)에 요약했다. 준비 실패 001과 실제 실패/원복 002를 구분한다. 소스 수정/전체 800시험·runtime hash는 아래 기록을 유지하며 아직 커밋/푸시하지 않았다.
- 남은 작업: 사용자 리뷰가 가능한 새 시각의 계획·새 실행 승인 → 새 사건·새 PR의 정확한 head 실제 리뷰/CI → 병합·동기화 → C-03 인계 → 최소 120초 C-04 HTTP/지표/경보 관찰 → 임시 자격 회수. 이번 결과를 C-04 성공 또는 독립 감시의 기한 원복 실동작 성공으로 계산하지 않는다. 다음 추천 **GPT-6 SOL · High**.

## 2026-11-12 16:08 KST · 양쪽 감시 준비 확인·자동 실행기 준비 완료

- 현재 승인된 실습 **202611120002**의 로컬 감시와 [원격 감시 43172645824](https://github.com/f-lab-edu/F-Lab-DevOps/actions/runs/43172645824)은 실제 준비 완료 로그를 확인했다. 원격 필수 배포 승인은 GitHub UI로 처리했다. 최초 관찰 **16:06:58 KST**를 `live/remote-ready-observed.json`에 기록했다.
- 자동 실행기는 **16:07:07 KST**에 준비 완료했다(`live/result-prepared.json`, PID 69332). 준비 마감 16:10 전이며 아직 장애 없음. 후속 PR 연결기 PID 69333은 정적 자료 갱신을 마치고 이번 실제 실패 인계를 기다린다. supervisor session **52553**, 실제 안내 bridge session **45807**을 계속 관찰한다.
- 예정 자동 진입 **16:14:30**, 장애 **16:15**, 새 PR 실제 안내 마감 **16:16:30**, 실제 head 리뷰 마감 **16:18:30**이다. 안내 도구의 실제 accepted 응답을 같은 bridge stdin에 즉시 전달해야 한다. 원복 목표 16:19/독립 감시 시작 기한 16:20. 종료·C-04 결과·토큰/Secret 회수 확인까지 현재 작업을 계속한다. Terraform 미실행.

## 2026-11-12 16:01 KST · 새 16:15 계획 승인·감독기 초기 검증 통과

- 사용자가 **202611120002 / `d61531ec8e6ec320c6882d211f6c1baf0969fdc00c9d9bdf1db3393af884e139`**에 “예, 16:15 계획 실행 승인”을 제출했다. 실제 답변으로 `authorization.json`을 새로 기록하고 정상 대상·ECR·runtime hash를 재검증했다. 아래 새 승인 대기는 종료됐으며 종료된 16:00 승인은 사용하지 않았다.
- 개인 `preparation-202611120002/runtime/`에서 **안내 bridge exec session 45807**, **supervisor exec session 52553 / PID 67923**를 시작했다. `live/state.json`은 `waiting_preparation_window`, `local_kubeconfig_verified=true`다. 16:03 준비 시작을 기다리며 아직 토큰/Secret·감시·장애는 없다. 상세 제어는 `execution-control.json`이다.
- 16:05 이후 원격 감시의 실제 GitHub UI 배포 승인을 처리하고 양쪽 실제 준비 marker를 16:10 전에 확인한다. 새 복구 PR 안내 발생 시 실제 request_user_input_async 응답을 **동일 bridge 45807 stdin**으로 즉시 전달한다. 실제 안내 마감 16:16:30, head 리뷰 마감 16:18:30이다. 원복·C-04 관찰·임시 자격 회수까지 이어서 검증한다. Terraform은 실행하지 않는다.

## 2026-11-12 15:59 KST · 16:00 준비 실패 종료·수정 검증, 새 16:15 계획 승인 대기

- 16:00 계획 `202611120001`은 15:48 준비 단계에서 기존 AWS CLI 절대 경로(`/opt/homebrew/bin/aws`)를 문자열 검사로 거부해 종료됐다. **장애 주입·원격 감시·실습 토큰 발급 0건**, 앱 정상·빈 Lease·토큰/Secret 부재·프로세스/소켓 정리를 실제 확인했다. 아래 감독기 대기는 종료 이력이며 이전 승인은 재사용하지 않는다. 개인 `preparation-202611120001/execution-closed.json`에 근거가 있다.
- PATH의 기존 AWS와 같은 실행 파일인 절대 경로만 정규화하도록 고쳤다. 원본 kubeconfig는 보존하며 대기 전에 확인하고 준비 시작 때 다시 대조한다. **전체 800개 시험 통과**. 실제 5분 역할 토큰 두 개로 허용·거부 권한과 로컬 원복 `--prepare-only`를 확인하고 모두 회수했다. 이 준비 검사는 장애·실제 감시 실행이 아니다.
- [새 계획 202611120002](.worktrees/c04-recovery-verification/ai-ops/docs/C4-live-trial-202611120002.md), hash **`d61531ec8e6ec320c6882d211f6c1baf0969fdc00c9d9bdf1db3393af884e139`**는 **새 실행 승인 대기**다. 감독기 시작은 **16:03 이전**, 실제 준비 마감 **16:10 전**, 장애 예정 **16:15 KST**다. 새 PR 안내 마감 **16:16:30**, 정확한 head 실제 리뷰 마감 **16:18:30**(실제 안내 뒤 최소 120초), 소유 원복 목표 **16:19**, 독립 원복 시작 기한 **16:20**, C-04 관찰 최소 120초다. 마감 실패 시 주입 없이 종료하며 자동 연기하지 않는다. Terraform은 실행하지 않는다.
- 개인 자료는 `~/.local/share/url-shortener-ai-ops/c04-20261112/preparation-202611120002/`의 `plan.json`, `preflight.json`, `release-summary.json`, `preparation-doctor-deep.json`, `current-c2-access.json`, `c2-kubeconfig.json`, `runtime/`, `guard-runtime/`이다. 현재 Pod 한정 C-02 1시간 자격을 새 관찰 종료까지 유효하도록 갱신했다. runtime manifest **`d303076fb0deffbfba4768109198ee252277252b66cd51d98a9b53d40f0de7ff`**, 658파일. 정상 CI **43167289475**·현재 binding/UID를 유지하고 자동 조회 세 번 **1.15/1.14/1.16초**를 확인했다.
- 새 승인 전 authorization·감독기·감시·장애는 없다. 승인 뒤 새 runtime의 실제 bridge/supervisor를 사용한다. 실제 `request_user_input_async`의 `{"accepted":true}`를 받은 즉시 **동일 bridge stdin**에 `{"delivery_tool_response":{"accepted":true}}`와 개행을 전달한다. 사용자 답변이나 CI를 기다리지 않는다. 새 PR의 실제 head 리뷰·CI는 별도 검증한다. GitHub 환경 승인은 UI로 수행한다. 임시 45분 토큰/원복 Secret은 종료 뒤 회수하고 원본 자격·App 키·PVC·사건 근거는 보존한다. 추천 **GPT-6 SOL · High**.

## 2026-11-12 15:35 KST · 16:00 실행 승인 확인, 감독기·실제 안내 브리지 기동

- 사용자가 정확한 계획 **202611120001 / `1da34635ab2602f1a6804484611f6f53a7a59b610ede9bbb8b725d4fa4ab9b7c`**에 “예, 16:00 계획 실행 승인”을 제출했다. 실제 답변으로 새 `authorization.json`을 만들고 runtime hash·현재 정상 대상·EKS/ECR를 재검증했다. 아래 실행 승인 대기는 종료됐다. 새 복구 PR의 사람 리뷰는 여전히 별도다.
- 같은 개인 준비 폴더의 `runtime-accepted-response/`에서 **실제 안내 브리지 exec session `28817`**, **감독기 exec session `39941`**을 시작했다. 제어 메타데이터는 `execution-control.json`이다. 감독기는 `live/`를 생성하고 **15:48 준비 시작까지 대기**한다. 아직 토큰·Secret·감시·장애 주입은 시작하지 않았다.
- 현재 작업은 종료하지 않고 감독기를 관찰한다. 15:48 이후 단기 자격/제한 권한과 원격 원복 run을 확인하고, GitHub 필수 배포 승인은 Computer Use로 처리한다. 준비는 15:55 전 완료돼야 하며 기한 실패 시 주입 없이 중지한다.
- **중요:** 브리지 stdout에 `review_notice_requested`가 나오면 arguments를 즉시 실제 `functions.request_user_input_async`로 전달한다. `{"accepted":true}`를 실제로 받은 직후 같은 bridge session `28817` stdin에 **`{"delivery_tool_response":{"accepted":true}}` + 개행**을 보낸다. 사용자 답변이나 CI 완료를 기다리지 않는다. 도구 실패 시 성공을 합성하지 않는다. 새 PR의 정확한 head 리뷰·CI는 실행기가 별도로 검증한다. 안내 영수증 게시 마감은 16:01:30, 리뷰 마감은 16:03:30 KST다.
- 정상 CI는 `43167289475`, projection probe는 같은 준비 폴더의 `projection-probe.json`, kubeconfig는 개인 `install-20261112/kubeconfig-verification.json`이다. 실제 단기 자격 원문을 로그·채팅·문서에 출력하지 않는다. Terraform은 실행하지 않는다. 이번 실행의 종료·원복·C-04 판정 및 회수 확인까지 이어서 진행한다.

## 2026-11-12 · 새 C-04 16:00 계획 준비 검증 완료, 실행별 승인 대기

- 사용자의 “진행해”에 따라 설치 후 C-04 준비를 수행했다. [새 계획 202611120001](.worktrees/c04-recovery-verification/ai-ops/docs/C4-live-trial-202611120001.md), hash **`1da34635ab2602f1a6804484611f6f53a7a59b610ede9bbb8b725d4fa4ab9b7c`**다. **실행 승인·감시·예약 실행기·장애는 아직 없다. Terraform도 실행하지 않았다.** 아래 종료된 실습의 시각·승인을 재사용하지 않는다.
- 예정 주입 **오늘 16:00 KST**, 감독기 시작/준비 시작 **15:48 직전/15:48**, 실제 준비 마감 **15:55 전**이다. 새 복구 PR 안내 마감 **16:01:30**, 실제 정확한 head 리뷰 마감 **16:03:30**이며 실제 안내 완료 뒤 최소 120초를 요구한다. 소유 원복 목표 **16:04**, 별도 감시 원복 시작 기한 **16:05**, C-03 인계 뒤 C-04 관찰 최소 120초다. 실행 승인은 **15:40까지 권장**하며 15:48을 놓치면 시각을 자동 연기하지 않고 새 계획을 만든다.
- 재설치로 빠진 c03-sync/c03-rollback 역할을 lab-target get/update/sync로만 복원했고 토큰·그룹은 연결하지 않았다. collector와 ci-worker의 실제 Argo repo_url을 분리 실습 저장소로 맞췄다. 앱·PVC UID/spec을 보존하며 relay 중지→worker 설정 갱신/재기동→relay 복귀를 확인했다. 현재 C-02 Role과 1시간 조회 자격도 새 worker Pod `ci-worker-5b4cb57794-5w4km`/UID `f7822cef-714a-48f8-be29-d1ad32f522be`에 고정했다.
- 초기 정상 CI `43166509589`는 보존 실행 묶음의 다른 Lease 코드 해시를 감지해 관측 전에 거절했다. 승인 이미지/CI와 동일한 main 코드로 정상 binding을 다시 생성했고 [새 정상 CI 43167289475](https://github.com/f-lab-edu/F-Lab-DevOps/actions/runs/43167289475)는 success다. 현재 binding·원격 artifact digest·자기 데이터 제거·기준선 불변·잠금 해제·외부 호출 0을 검증했고 relay 자동 발견→worker ready도 확인했다. GitHub 환경 승인은 UI로 수행했다.
- 새 설정의 중립 조사 `inc-c8a6335ec16645318f04ed5199755ad8` / `run-9b0b10bfd6864cfbb69643e817f13324`는 partial 규칙 보고서다. Argo 근거와 요청/감사 config hash 일치를 확인했고 부족한 자료는 그대로 보존했다. 제한된 Pod 한정 자격의 실제 TLS 조회에서 bundle hash 일치를 확인했다. 실제 장애나 C-04 해결 결과로 계산하지 않는다.
- 실제 안내 도구가 식별자 없이 **`{"accepted":true}`**만 반환하는 것을 확인했다. 첫 브리지는 성공을 합성하지 않고 종료했다. 실제 성공 응답을 `{"delivery_tool_response":{"accepted":true}}`로 전달하도록 보완했고, provider ID가 없는 경우 ID를 만들지 않는다. 리허설 action/ping/응답은 실제 실행·리뷰 영수증에서 거절한다. 사용자 수신 확인도 받았으며 직접 연결 왕복은 **11.18초**였다. 이 수신 확인은 실행 승인이나 GitHub 리뷰가 아니다. 새 PR에서는 그때의 실제 도구 응답을 즉시 같은 bridge stdin에 전달한다.
- 전체 로컬 **795개 시험 통과**, 새 runtime **658개 파일**, manifest **`4d6363358154f6ab645f1df4fe4a693b897f400707a72d0320e6ed01ef51ab66`**. 자동 AWS 갱신/마지막 snapshot-cache 세 번은 **1.23/1.18/1.20초**다. 실제 새 UID·빈 Lease·이미지·Git/App/권한·Job 서버 dry-run·원복 자격 부재·원격 소스 hash·전후 불변 검사를 모두 통과했다. **실제 양쪽 감시와 주입 직전 검사는 실행 승인 뒤** 진행한다.
- Git main은 기존 검토된 실패 seed `1e0e107d…`, 앱은 정상 `0cfef83c…` 수동 고정이다. 기존 seed를 불변 입력으로 새 계획에 명시했으며 새 seed 작성/병합이나 PR #10 정상화는 하지 않았다. PR #10은 뒤늦은 APPROVED가 있으나 OPEN·미병합이고 새 실습에서 재사용하지 않는다. 실제 실패 뒤 새 복구 PR·정확한 head의 사용자 리뷰와 CI가 필요하다. source/main 변경 시 중지한다.
- 개인 자료: **`~/.local/share/url-shortener-ai-ops/c04-20261112/preparation-202611120001/`**. 최종 `plan.json`, `preflight.json`, `normal-binding.json`, `normal-ci-verified.json`, `projection-probe.json`, `projection-live-read.json`, `live-readonly-final.json`, `runtime-accepted-response/`, `guard-runtime/`, `tools/`를 사용한다. 이전 `runtime/`와 `normal-binding-before-main-hash.json`·실패 CI는 이력이며 실행 입력으로 사용하지 않는다. 실제 승인 후 같은 새 plan으로 `runtime-accepted-response/ai-ops/scripts/bridge_c4_review_notice.py`와 `supervise_c4_trial.py`를 시작한다. operator 소켓 루트는 `/private/tmp/c04-202611120001`이다.
- C-04 소스 변경은 `.worktrees/c04-recovery-verification/`의 알림 모듈·브리지·테스트 및 문서에 있으며 아직 새 커밋/PR로 게시하지 않았다. 원격 원복은 변경하지 않은 main `63e220e…`를 별도 guard-runtime으로 고정했다. 추천 **GPT-6 SOL · High**.

## 2026-11-12 15:01 KST · 남은 workflow 4개 재활성화 완료

- 사용자가 직전 재활성화 승인 질문에 **“진행해”**라고 답해 `ci-cd.yml`, `ai-ops-image.yml`, `ai-ops-lab-ci.yml`, `ai-ops-c2-rollback.yml` 네 개를 활성화했다. GitHub 재조회에서 네 개 모두 `active`이며 다른 workflow 상태는 그대로다. 아래 14:40의 비활성 대기는 종료된 이력이다.
- main 보호 규칙과 모든 GitHub environment의 승인·브랜치 제한 설정을 전후 대조해 **변경 없음**을 확인했다. workflow dispatch·장애 주입·배포 실행은 시작하지 않았다. workflow 활성화는 이후 각 선언의 trigger를 사용할 수 있게 한 것이며, 실제 배포·장애·복구 성공을 추가 검증한 의미는 아니다.
- 근거는 개인 `~/.local/share/url-shortener-ai-ops/install-20261112/workflow-reactivation-20261112T060146Z/`의 전후 상태·보호 설정·`result.json`이다. [설치 기록](docs/runbooks/installation-execution-2026-11-12.md)과 [결과 JSON](docs/runbooks/installation-execution-2026-11-12.json)도 갱신했다.
- 다음은 C-04 새 실습 계획 준비다. 실제 장애 실행의 실행별 승인과 새 복구 PR 정확한 head의 실제 사용자 리뷰 원칙을 유지한다. 추천 **GPT-6 SOL · High**.

## 2026-11-12 14:40 KST · 운영·AI 실행 환경 설치와 정상 CI 검증 완료

- 최신 **“그냥 너가 다 설치혀”** 위임으로 남은 설치를 Codex가 수행했다. 아래 수동 설치 대기는 종료된 이력이다. 향후 일반 Terraform 사용자 직접 실행 원칙을 바꾸지는 않는다. 새 AWS 계정/프로필이나 operator 설치 정책을 추가하지 않았다. 기존 관리자 세션은 AWS 설치에, operator는 Kubernetes·일상 접속에 사용했다.
- 운영 Terraform **102개**, AI root **10개** 관리 리소스. RDS 새 비밀번호 회전 완료, Primary/Replica available·암호화·대기 변경 없음. 모든 API Pod에서 실제 DB 인증과 Primary `false`/Replica `true`, Redis PING, `/readyz`·`/items` 200을 확인했다. 최종 두 root plan 모두 **No changes**다.
- 노드 **3/3 Ready**, 가동 Pod 미준비 **0개**, Argo Application **8개 모두 Synced/Healthy**. LB Controller·Argo·metrics-server·Redis·Prometheus/Grafana/Alertmanager·cert-manager·Traefik·Karpenter가 준비됐다. API·Argo 외부 HTTPS **200 / 인증서 검증 성공**이며 Argo 내부 연결도 TLS 검증을 유지한다. 인증서 발급 후 Argo 서버를 한 번 재시작해 새 인증서 로드를 확인했다.
- AI worker/내부 경보 수신기 **2/2 Ready**, 실제 Prometheus up=1·최신 루프 진행·고정 큐 8상태·예약 보관 정리 성공을 확인했다. 격리 실습 Pod **4/4 Ready**, `ai-ci` worker **2/2**·relay **1/1 Ready**다. 세 AI PVC(5Gi/1Gi/1Gi)의 실제 EBS는 **gp3·encrypted=true**다. 전체 가동 PVC 7개 Bound. 운영 gp2 볼륨은 기존 chart 선언대로 복원했으며 실제 암호화 여부는 결과 JSON에 구분했다.
- `lab-target`은 분리 저장소 `joungGo/c03-execution-lab`의 정상 SHA **0cfef83c36578090dfc1b65513789b1b10159e03**에 수동 고정했다. 실패 main·PR #10은 적용·병합하지 않았다. 최소 권한과 서버 대상 제한을 적용하고 실제 수집·규칙 보고서·경계 거부를 검증했다.
- 새 [정상 CI #43162016876](https://github.com/f-lab-edu/F-Lab-DevOps/actions/runs/43162016876) **success**. GitHub의 `Review deployments → Approve and deploy`를 UI로 처리했다. 정확한 head `63e220e…`·현재 binding·`miss/hit/miss/hit`·자기 데이터 원복·기준선 불변·잠금 해제를 영수증으로 검증했다. relay 자동 발견→worker 처리 **ready**와 두 프로세스의 새 설정 로드도 확인했다. 장애 주입·외부 모델 호출은 **0건**이다.
- 기존 C-02 TLS 원본을 복원하고 현재 worker Pod 한 개의 get/port-forward 전용 **1시간** kubeconfig를 새로 발급했다. 목록·exec·Secret·다른 Pod는 거부, 실제 TLS 조회는 무인증 401/유효 인증의 없는 기록 404/쓰기 405를 확인했다. 파일은 개인 `ci-runtime/c2-kubeconfig.json`, 인증서 만료는 **2026-11-27 18:22 KST**다. 이후 실습 때 만료·Pod 교체를 다시 확인하고 기존 한정 예외에 따라 갱신한다.
- 자동 승인 검토가 장애·원복·일반 배포 workflow의 일괄 재활성화를 거절했다. 안전한 대안으로 `ai-ops-normal-ci.yml` **하나만 활성화**했다. `ci-cd.yml`, `ai-ops-image.yml`, `ai-ops-lab-ci.yml`, `ai-ops-c2-rollback.yml`은 비활성 상태다. 기존 승인·main 보호를 우회하지 않았다. 이 상태를 일반 자동 배포·실제 장애 실행까지 검증된 상태로 표현하지 않는다.
- 모델 gateway/proxy는 **0/0**, API 키 없음·외부 전송 비활성이다. 모델 준비용 1Gi PVC는 WFFC Pending이며 EBS가 할당되지 않았다. 삭제 전 보존 데이터·OCI·state·스냅샷·App 키·원본 자격은 유지했고 과거 큐 작업을 재실행하지 않았다.
- 결과: [설치 기록](docs/runbooks/installation-execution-2026-11-12.md), [결과 JSON](docs/runbooks/installation-execution-2026-11-12.json), [승인·CI 완료 화면](docs/runbooks/assets/installation-normal-ci-2026-11-12.png). 비밀·state·plan·상세 증거와 실제 실행 스크립트는 **`~/.local/share/url-shortener-ai-ops/install-20261112/`**에 있다. 표준 `~/.kube/config`도 기존 operator로 새 endpoint에 갱신했고 이전 사본은 개인 폴더에 보존했다.
- 다음 C-04 작업은 기존 로컬 알림·완료 영수증 구현의 원격 반영 경로 검토 및 **새 실행 계획 준비**다. 지금 설치 완료를 C-04 실습 완료로 처리하지 않는다. 새 장애 실행에는 실행별 승인과 새 복구 PR 정확한 head의 실제 사용자 리뷰가 필요하다. 추천 **GPT-6 SOL · High**.

## 2026-11-12 · 남은 전체 재설치 직접 실행 위임

- 최신 사용자 요청 **“그냥 너가 다 설치혀”**에 따라 이번 남은 운영·AI 설치는 Codex가 직접 진행한다. 아래 비-AI 수동 대기는 이 설치에서 해제됐다. 새 계정/프로필을 만들지 않으며 AWS 관리자 기존 세션과 Kubernetes operator 세션을 사용한다.
- 운영 Terraform 102개 리소스와 RDS 비밀번호 회전 검증을 완료했다. AI root 10개 생성도 완료했고 후속 plan은 변경 없음이다. Argo CD·AWS Load Balancer Controller·metrics-server가 준비됐다. metrics-server 첫 확인은 생성 직전 경합으로 NotFound였으며 재조회에서 Synced/Healthy와 Deployment Available을 확인했다.
- 보존 OCI에서 정상 앱 `sha-181ca13`과 승인 AI digest `sha256:280164c6b901765d11a0b1c36d250e973b76db73deb0e180ee646d53dd11206b`를 변경 없이 ECR에 복원했다. 설치 기록·스크립트·비밀 원본은 `~/.local/share/url-shortener-ai-ops/install-20261112/`에 보호해 저장한다.
- 이어서 Redis·모니터링·Ingress·앱과 AI 수동 worker·격리 실습을 설치·검증한다. 실습은 정상 SHA 수동 고정이며 장애 주입·복구 PR 병합·외부 모델 캠페인은 실행하지 않는다. 완료 여부는 후속 실제 검증 기록을 따른다.

## 2026-11-12 14:05 KST · 이번 RDS 회전은 Codex 위임으로 완료

- 사용자가 직전 보호 로컬 파일 RDS 회전 절차에 **“너가 해줘”**라고 명시 위임했다. 이번 건에만 Codex가 Terraform plan과 saved-plan apply를 실행했다. **다른 비-AI 설치는 사용자 직접 실행** 원칙을 그대로 유지한다. RDS 회전 사용자 실행 대기는 아래의 종료된 이력이다.
- 현재 Primary 안정·Replica 관계와 state의 회전 버전 null을 재확인하고 새 32자리 비밀번호를 보호 파일에 생성했다. 값은 채팅/로그에 출력하지 않았다. 계획은 **0 생성·1 변경·0 삭제**, 변경 필드는 Primary의 `password_wo_version: null→1`뿐이었다. 저장 계획 SHA-256 `dbac8c9bfccbe01536fa48d0bcf13250f71e83e7d1dcfbd3680b74fb52e8e502`를 적용했다. 생성/삭제·삭제 보호 해제·스냅샷 설정 변경은 없다.
- 실제 적용 후 production state serial **128**, 회전 버전 **1**. Primary·Replica 모두 available·대기 변경 없음, Primary 삭제 보호·최종 스냅샷 보존 설정을 확인했다. canonical 입력의 후속 재계획 **No changes**다. 새 비밀번호 문자열이 plan JSON/state/실행 로그에 없는지도 검사했다. **실제 SQL 비밀번호 인증은 아직 미검증**이며 앱 설치 뒤 확인해야 한다.
- 새 비밀번호는 개인 `~/.local/share/url-shortener-ai-ops/install-20261112/rds-rotation.tfvars` **0600**에 있다. `source/terraform/reinstall.tfvars`도 같은 값·버전 1로 유지했다. 이후 계획에서 과거 null 입력을 사용하지 않는다. `source/terraform/reinstall.before-rds-rotation.tfvars`는 과거 이력일 뿐이다. 비밀 파일을 출력·커밋하지 않는다.
- 개인 폴더의 `rds-rotation-reviewed.json`, `rds-rotation-plan.json/tfplan`, `rds-rotation-apply.log`, `rds-rotation-state-before/after.json`, `rds-rotation-post-plan.log`, `rds-rotation-result.json`에 원본을 보존했다. 공개 요약은 [회전 결과](docs/runbooks/rds-password-rotation-2026-11-12.json)다. 회전 실행기는 `/private/tmp/urlshortener-rds-rotation-20261112.py`이며 완료된 계획/스크립트를 다시 적용하지 않는다.
- 다음은 사용자 수동 **AWS Load Balancer Controller → Argo CD → Metrics Server** 설치다. [수동 안내](docs/runbooks/manual-installation-2026-11-12.md)를 따른다. AI 인증 Secret의 Prometheus 전 선행 인계 원칙도 유지한다. 이번에 다른 앱/차트/AI 리소스나 IAM 권한을 설치·변경하지 않았다. 추천 **GPT-6 SOL · High**.


## 2026-11-12 13:56 KST · 사용자 운영 apply 실물 검증 완료, RDS 회전 수동 실행 대기

- 사용자가 운영 `production-admin.tfplan` 적용 완료를 알렸다. 실제 production state **102개**, serial **127**, 계획 주소 집합/lineage 일치, 동일 source/backend/입력의 재계획 **No changes**를 확인했다. Codex가 apply나 비-AI 설치·IAM 변경을 실행하지 않았다.
- 실제 AWS EKS `urlshortener` **1.35 ACTIVE**, 관리형 nodegroup ACTIVE·desired 3·건강 문제 없음. 실제 Kubernetes API를 operator 신원으로 읽어 **3/3 Ready**를 확인했다. EBS CSI·CoreDNS·Pod Identity Agent·kube-proxy·VPC CNI 5개 addon도 ACTIVE·건강 문제 없음이다. 조회용 개인 `kubeconfig-verification.json`만 만들었고 사용자 `~/.kube/config`는 변경하지 않았다.
- 실제 RDS Primary/Replica 모두 **available·암호화·PendingModifiedValues={}**, parameter group in-sync. Primary Multi-AZ·삭제 보호 true, Replica의 source는 현재 Primary다. 최신 보존 스냅샷에서 복원됐고 password_wo_version은 **null**이다. 앱 비밀번호 인증을 확인한 상태는 아니므로 다음 사용자 단계는 **새 DB 비밀번호 + 회전 버전 1**의 별도 apply다.
- DNS 전체 레코드·기존 operator 사용자/연결 정책·보존 RDS 스냅샷/KMS·backend 버전 관리를 확인했다. 앱 ECR은 IMMUTABLE이다. AI root와 운영 Kubernetes 앱·controller·Argo·모니터링 설치는 이 검증에서 실행하지 않았다.
- [검증 요약](docs/runbooks/installation-verification-2026-11-12.json), [현재 수동 안내 3절](docs/runbooks/manual-installation-2026-11-12.md#3-rds-비밀번호-회전--현재-다음-단계)을 우선한다. 개인 `install-20261112/`의 `production-apply-verification.json`, `production-state-after-user-apply.json`, `eks-after-user-apply.json`, `rds-after-user-apply.json`, `nodes-after-user-apply.json`, `production-post-apply-check.json/log/tfplan`에 원본을 보존한다.
- 사용자 터미널에서 기존 reinstall.tfvars를 별도 0600 `rds-rotation.tfvars`로 복사하고 새 비밀번호/버전 1만 수정한다. 최신 복원 스냅샷과 나머지 입력은 유지한다. 같은 파일의 대화형 plan이 **0 생성·1 변경·0 삭제**인지 보고 apply한다. Codex가 비밀번호를 받거나 생성하지 않는다. `rds-rotation.tfvars`는 확인 당시 아직 없었다. 사용자 완료 후 실제 RDS/회전 버전을 확인한 뒤 LB Controller·Argo·Metrics Server 단계 명령을 이어서 준다.
- AI 인증 Secret의 Prometheus 전 선행 인계 원칙과 비-AI 사용자 직접 설치 원칙은 유지한다. 다음 안내 추천 **GPT-6 SOL · High**.


## 2026-11-12 · 설치는 AI 제외 사용자 수동 실행으로 전환, 운영 계획 준비 완료

- 최신 사용자 지시: **“AI 관련 설치를 제외하면 내가 직접할께. 너는 내가 어떻게 해야 하는지만 알려줘.”** 비-AI IAM·Terraform·Helm/kubectl·DNS·운영 배포 설정은 Codex가 실행하지 않고 안내만 한다. AI 설치는 기반 준비 후 Codex가 이어간다. 운영·AI Terraform apply/destroy 사용자 터미널 원칙도 유지한다. [수동 설치 안내](docs/runbooks/manual-installation-2026-11-12.md)를 우선한다.
- 사용자가 기본 로컬 콜백으로 `urlshortener-new`(목표 계정 root), `urlshortener-operator`(기존 IAM 사용자) 로그인을 각각 완료했고 STS로 확인했다. 원격 코드 중계는 중단했다. 새 계정/전역 프로필은 생성하지 않았고 중복 admin 프로필을 쓰지 않는다. 향후 로그인도 사용자 터미널 기본 콜백을 따른다.
- 운영 main `63e220ecdcb5018437c2ba4259855375af548466`은 루트 작업 저장소가 아니라 C-03 저장소 Git 객체에 있다. `.worktrees/c04-recovery-verification`의 Git에서 archive한 별도 개인 소스 `~/.local/share/url-shortener-ai-ops/install-20261112/source`를 사용했다. 운영·AI state는 관리 리소스 0개, 최신 RDS 수동 스냅샷 `...-891d94c7` available/암호화, KMS Enabled, 공개 DNS Zone 보존을 확인했다.
- 운영 `production-admin.tfplan`은 **102개 생성·변경/삭제/교체 0개**, SHA-256 `c6f8de4af113ee8e7bdfdc566de58c8191e1867dd4eb4b8b3cd45cfba824babe`. 같은 개인 설치 폴더의 `aws-terraform-config`와 `urlshortener-new`로 검증했다. DB 비밀번호/회전 버전은 최초 스냅샷 복원용 null이며 복원 후 사용자가 별도 회전한다. IAM 사용자·DNS·state 변경이 없다. 아직 apply는 하지 않았다.
- operator 기존 `eks:DescribeCluster`는 IAM 시뮬레이션 allowed다. 설치 권한 확대용 Autopilot 후보 6개는 개인 검토 파일일 뿐 **생성/첨부하지 않았다**. AWS ValidatePolicy의 구 후보 검증은 finding 0건이었지만 보존 state 버전 삭제 등 불필요한 후보 action을 제거했다. EKS 필수 `kms:CreateGrant` 추가와 불필요한 IAM policy-version 정적 입력은 자동 검토에서 거절됐고 적용하지 않았다. 추가 권한 질문은 사용자 수동 설치 전환으로 보류한다. 후보 파일을 임의 적용하지 않는다.
- 보존 OCI 49개 blob의 hash를 재검증했다. 정상 앱 태그 `sha-181ca13`/digest `dbd367bd…`와 패치된 AI `280164c6…`가 남아 있다. AI `f30ab5cd…`는 이전 취약 이미지이므로 복원 배포 대상으로 사용하지 않는다. 운영 현재 main 태그와 다른 새 image-only 태그를 성공만 보고 동기화하지 않는다.
- **다음 사용자 단계:** 수동 안내 1~2절의 운영 계획 적용·기반 확인. 이어서 RDS 회전·LB Controller·Argo·Metrics Server·Redis·앱 Secret을 안내한다. 기존 Prometheus 선언이 AI 인증 Secret을 참조하므로 **Prometheus 적용 전 AI TLS/auth 선행 설정은 Codex 인계**다. 운영 앱·Prometheus·Argo 준비 후 AI 전용 root/worker/PVC/정상 실습/CI를 이어간다. 실제 장애 주입·모델 캠페인은 설치와 별도 승인이다.
- 공개 요약: [사전 검증 JSON](docs/runbooks/installation-preflight-2026-11-12.json). 원본 plan/state·검증 로그·권한 후보는 개인 설치 폴더에 보존한다. 이 문서 추가 뒤 새 Git 커밋·원격 push는 하지 않았다. 다음 안내 추천 **GPT-6 SOL · High**.


## 2026-11-12 · C-04 안내 전달·영수증 연결 로컬 검증 완료

- C-04 코드·테스트·문서 커밋: `9792c93` — `C-04 복구 검증과 알림 전달 경로 구현`, 브랜치 `codex/c04-recovery-verification-20261111`. 로컬 커밋이며 원격 push는 하지 않았다.
- 사용자 “진행해”에 따라 **인프라 설치 없이 C-04 로컬 검증만** 이어갔다. 코드 위치는 `.worktrees/c04-recovery-verification/`이다. AWS 변경·Terraform·새 PR/병합·실제 알림·새 실습 실행/예약은 없다. 기존 정리/보존 결과는 아래 완료 기록을 유지한다.
- `c_trial_notice.py`·`bridge_c4_review_notice.py`로 전용 Unix 소켓 → Codex 호스트 안내 도구의 성공 응답 → 현재 시각 영수증 게시 경로를 연결했다. 감독기/직접 실행기의 필수 `--review-notifier-socket` ping이 실패하면 자격 발급 전 중지하고, PR 작성기는 전달 확인 전 복구 문맥을 게시하지 않는다. 계획/hash/head/URL/nonce/응답 hash·시간·최소 120초를 검사하며 수동 시각만 적은 영수증·지연/중복/동시 덮어쓰기를 거부한다. 실제 GitHub 리뷰·CI는 별도 필수다.
- 핵심 검사 **23개**, 보존 C-03 결합 전체 **791개** 통과. 실제 소켓·별도 CLI 프로세스와 합성 호스트 응답을 사용했다. 기존 SQLite ResourceWarning은 남아 있다. 최초 샌드박스 bind 거부는 로컬 소켓 권한으로 해소했고, 재결합 manifest `base_sha/source_base_sha` 처리 오류도 수정·회귀 검증했다. 이전 runtime은 변경하지 않았다.
- 새 개인 자료 `~/.local/share/url-shortener-ai-ops/c04-20261112/notice-bridge-local/`: `runtime/` 658개 파일, `full-tests.log`, `release-summary.json`. manifest SHA-256 `f0d19d8c4d68b7e60c8a8bc5a9f46d993f373a208a3c51c957561826379ef33d`. `runtime-incomplete/`는 실패한 최초 빌드 이력으로 실행하지 않는다. 임시 테스트 Python은 `/private/tmp/c04-local-20261112-venv/bin/python`이며 없어지면 소스의 두 고정 requirements 파일을 각각 설치한다.
- [연결 계약과 로컬 검증 문서](.worktrees/c04-recovery-verification/ai-ops/docs/C4-notification-bridge-local-20261112.md), [검증 요약](.worktrees/c04-recovery-verification/ai-ops/docs/assets/C4-notification-bridge-local-20261112.json)을 우선한다. 실제 호스트는 브리지 stdout의 질문 인자를 `functions.request_user_input_async`로 보내고 **도구 성공 응답 직후** 그 식별자만 같은 stdin으로 전달한다. 사용자 답변 대기/질문 stdout/합성 ack는 성공을 대신하지 않는다. stdin은 신뢰된 호스트의 확인이며 원격 서명·사용자 읽음 증명은 아니다.
- **남은 작업:** 실제 Codex 도구 응답 형태·식별자 매핑·전달 지연 리허설(아직 미검증) → 재설치 후 새 대상/자격/Git 정상화 리뷰·CI → 새 실행 계획 승인 → 새 정확한 head의 사람 리뷰·C-03 인계·최소 120초 C-04 관찰. C-04/C-05 실환경 완료로 처리하지 않는다. 현재 실습 main은 실패 seed이므로 기존 정상 SHA `0cfef83c36578090dfc1b65513789b1b10159e03` 수동 고정 원칙을 유지한다. 새 실습 승인이나 인프라 설치를 이번 로컬 작업에서 요청하지 않는다. 추천 **GPT-6 SOL · High**.


## 2026-11-12 00:12 KST · 실행 리소스 정리·보존 검증 완료

- 대상 계정 `764428911194`·서울 `ap-northeast-2`에서 EKS, RDS Primary/Replica, VPC, 실행 EC2, 활성 NAT, 기록된 EIP·EBS 10개, 운영/AI ECR, LB/TG, Karpenter 큐의 부재를 실제 확인했다. 운영·AI state의 관리 리소스는 각각 **0개**이며 원래 S3 state 버전도 남아 있다. 최종 검증 **24개 모두 통과**했다. 계정 전체·다른 프로젝트·다른 리전의 모든 리소스 삭제를 의미하지 않는다.
- RDS Primary 최종 스냅샷 `urlshortener-postgres-final-snapshot-urlshortener-postgres-891d94c7`은 **available·암호화됨**, 스냅샷 KMS 키는 **Enabled**다. 기존 5개와 새 최종 1개, 수동 스냅샷 총 **6개**를 보존했다. EKS 전용 KMS 키는 2026-12-12 00:12 KST 삭제 예약 상태이며 즉시 삭제된 키로 기록하지 않는다. 프로젝트 소유 잔여 RDS/EKS 로그도 소유권 확인 후 정리했다. 후속 00:14 KST 검증에서 공용 이름 `RDSOSMetrics`의 6개 스트림도 state·스냅샷 및 이벤트의 실제 DB 식별자가 모두 이 프로젝트에 속함을 확인해 그룹을 정리했다. 근거는 개인 `rds-os-metrics-cleanup-verified.json`·소유권/이벤트 증거에 있다.
- `bidservice.store`의 모든 DNS 레코드, S3 backend/versioning/AES256 및 과거 state 버전, `urlshortener-operator`의 사용자·로그인·MFA·기존 access key·연결 정책을 대조해 보존을 확인했다. 로컬 AWS/GitHub 로그인·App 키·재설치 비밀 원본은 유지했다. **DNS·RDS 스냅샷·S3 state 버전에는 계속 요금이 발생할 수 있다.**
- 이번 정리의 남은 apply/destroy는 사용자가 Codex에 명시 위임했다. 위임 실행 시작 시 Replica는 이미 실제 부재였고 운영 state에서도 제거돼 재삭제하지 않았다. Codex는 최신 **Primary 1개 삭제 계획 → 최종 스냅샷 available 확인 → 운영 잔여 100개 삭제 계획**을 검증·적용했다. 격리 정리 사본의 `rds.tf` 삭제 보호 설정은 원본으로 복원했다. **향후 일반 Terraform 사용자 직접 실행 원칙은 유지**한다. 아래 로그인·명령·적용 대기는 모두 종료된 이력이며 예전 plan을 재실행하지 않는다.
- 정리·보존 근거: 개인 `~/.local/share/url-shortener-ai-ops/cleanup-20261111/cleanup-final-verification.json`, `terraform-cleanup-complete.json`, `codex-*-reviewed.json`, private plan/log/state, `remaining-owned-log-groups-removed.json`. 공개 가능한 요약은 [정리 결과 JSON](docs/runbooks/infrastructure-cleanup-2026-11-11.json), 절차 이력은 [정리 기록](docs/runbooks/infrastructure-cleanup-2026-11-11.md)에 있다.
- 보존 자료는 같은 개인 폴더의 `ai-ops-data.tar.gz`, `ai-ci-data.tar.gz`, `ai-ops-lab-data.tar.gz`, `data-backup-verification.json`, `images.oci/`, `image-backup-verification.json`, `reinstall-secret-originals/`, `source/`, `tools/`다. SQLite는 backup API·quick_check로 검증했으나 전체 볼륨의 원자적 스냅샷은 아니다. 원문 비밀·state·계획은 Git/채팅에 게시하지 않는다.
- **다음은 인프라 설치 없이 C-04 로컬 검증**이다. 소스 `.worktrees/c04-recovery-verification/`, 실습 증거 `~/.local/share/url-shortener-ai-ops/c04-20261111/`를 사용해 실제 알림 전송과 완료 영수증 연결·최소 안내 시간 조건을 재현하고 수정한다. C-04/C-05 실제 완료로 처리하지 않는다. 새 실습은 새 UID·자격·계획·실행별 승인과 새 PR 정확한 head의 사람 리뷰가 필요하다. 배포/이미지/실습 workflow 5개는 비활성화 상태로 보존했다.
- 재설치 시 격리 `lab-target`은 정상 SHA `0cfef83c36578090dfc1b65513789b1b10159e03`를 수동 고정한다. 실습 저장소 main `1e0e107da3b726712830d6e68ccc128399a993a2`는 실패 seed이며 PR #10은 미병합이다. 기존 실패 main을 자동 동기화하지 않는다. 다음 작업 추천 **GPT-6 SOL · High**.

## 2026-11-11 23:55 KST · 관리자 인증 확인, 위임받은 Primary 최종 스냅샷·삭제 진행 중

- 사용자의 새 로컬 로그인 후 `urlshortener-new` STS가 목표 계정 764428911194의 root로 성공했다. 클립보드·검증 코드를 읽지 않았다. 아래 로그인/MFA/코드 전달 대기는 이전 상태다.
- 이미 위임받은 개인 `cleanup-20261111/tools/cleanup-finish.py`를 시작했다. **exec session `94081` 진행 중**이다. Replica는 시작 시 실제 부재여서 재삭제하지 않았고, Primary의 available·대기 변경 없음·삭제 보호 false를 확인했다. 새 저장 계획은 Primary DB 한 개 삭제만 포함하고 최종 스냅샷 `urlshortener-postgres-final-snapshot-urlshortener-postgres-891d94c7`, `skip_final_snapshot=false`, RDS KMS Enabled를 검증했다.
- 현재 실제 AWS는 Primary deleting, 암호화된 최종 스냅샷 creating이다. private `codex-primary-destroy-plan.json`, `codex-primary-reviewed.json`, `codex-primary-apply.log`에 원본/검토/실행 기록을 보존한다. 완료 전 재시도하거나 스냅샷을 생략하지 않는다. 실행기는 최종 snapshot available·KMS Enabled 확인 후 운영 잔여 계획을 허용 주소 집합·보존 금지 목록으로 검증해 적용하도록 준비됐다.
- 종료 후 개인 `cleanup-20261111/tools/cleanup-final-verify.py`를 실행해 실제 EKS/RDS/VPC/NAT/EIP/EC2/기록된 EBS/ECR/LB/TG/큐 부재와 두 state·기존/새 스냅샷·DNS 전체 레코드·operator/로그인/MFA/키/정책 보존을 확인한다. 프로젝트 전용 잔여 RDS/EKS 로그만 소유권 확인 후 정리한다. 이번 위임으로 Codex가 apply를 실행 중이며 향후 일반 사용자 직접 실행 원칙을 일괄 바꾸지 않는다.

## 최신 사용자 인증 인계 · 클립보드 읽기 미승인, 사용자 터미널의 새 로그인 필요

- 사용자는 `pbpaste` 전달 질문에 “직접 처리할게”, 이후 “전달했어”라고 답했다. 클립보드는 읽지 않았다. 실제 확인에서 원래 agent remote login session `25980`은 코드 입력 대기였고 `urlshortener-new` STS는 `LoginRefreshRequired`였다. 인증 성공으로 간주하지 않았고 Terraform 변경도 시작하지 않았다.
- 직접 처리 흐름의 혼동을 줄이기 위해 원래 agent 대기 로그인은 Ctrl-C로 종료했다. 사용자가 자기 터미널에서 **새 `aws login --profile urlshortener-new`**를 실행하고 그 명령에서 새로 열린 브라우저의 관리자 인증을 완료해야 한다. 이전 확인 화면의 코드를 새 로그인에 재사용하지 않는다. 코드·비밀번호를 채팅으로 받지 않는다.
- 새 로컬 로그인 성공 뒤 목표 계정 764428911194의 실제 관리자 신원을 확인하면, 이미 위임받은 개인 `cleanup-20261111/tools/cleanup-finish.py` 경로로 나머지 정리를 직접 이어간다. 남은 Terraform 직접 실행 위임은 유지되며 로그인만 사용자 인계 상태다. 이전 MCP/CUA 복구 버튼 거절을 우회하지 않는다.

## MFA 완료 후 검증 코드 전달 대기 · 최신 인증 상태

- 사용자가 올린 AWS 확인 화면과 실제 IAB에서 `Continue with an active session`·`Copy verification code`를 확인했다. **기존 MFA는 완료됐다.** 아래 MFA 미완료 기록은 이전 상태다. 코드 값을 채팅에 받거나 출력하지 않았다.
- CUA로 검증 코드를 복사했다. 원래 remote `aws login` exec session `25980`은 이 코드를 기다린다. 터미널 앱 제어는 앱 보안 정책으로 차단됐다. `pbpaste`로 방금 복사한 코드만 메모리에서 읽어 원래 CLI에만 전달해도 되는지 사용자 확인이 대기 중이다. 다른 클립보드 자료를 읽거나 코드·자격 값을 로그/파일/채팅에 출력하지 않는다.
- 승인되면 functions 실행 내부에서 `pbpaste` 출력값을 변수로만 받고 `tools.write_stdin`으로 session `25980`에 전달한다. PTY echo도 출력하지 말고 성공/프로필 불일치 상태만 요약한다. 목표 계정 764428911194 root 신원을 STS로 확인한 뒤 개인 `cleanup-20261111/tools/cleanup-finish.py`의 위임된 정리를 실행한다. 원래 프로필과 다른 계정/신원으로 덮어쓰지 않는다.
- 시도했던 미사용 임시 FIFO/helper는 제거했고 대기 cat 프로세스도 종료했다. 코드 저장 파일은 만들지 않았다. CUA 부분은 코드 복사를 결과로 completion_summary를 마쳤다. 원래 AWS 로그인 CLI와 기존 자격/백업은 유지된다. 아직 위임 이후 새 Terraform 변경은 없다.

## 2026-11-11 23:38 KST · 관리자 로그인 완료 답변 후 실제 MFA 대기 확인

- 사용자 답변은 관리자 로그인 완료였으나 IAB tab `2`의 현재 실제 화면은 `Additional verification required`로 패스키/보안 키 MFA가 남아 있다. CLI remote session `25980`도 여전히 authorization code 입력을 기다린다. 기존 root 프로필 덮어쓰기는 하지 않았고 이번 위임 후 새 Terraform 변경은 아직 없다.
- 기존 MFA 확인만 직접 완료해 달라는 사용자 질문이 대기 중이다. `Trouble signing in?` 클릭은 자동 승인 검토에서 계정 복구/대체 인증 흐름을 열 수 있어 승인 범위 밖이라는 이유로 거절됐다. 같은 동작을 우회하지 않고 기존 MFA 완료만 기다린다. CUA handoff는 유지했으며 인증의 최종 결과 전 completion_summary를 쓰지 않았다.
- 후속 정리 실행기를 영구 개인 `~/.local/share/url-shortener-ai-ops/cleanup-20261111/tools/cleanup-finish.py`에 보존했다. `admin-mfa-still-pending.json`에 실행기 hash·인증 대기 상태가 있다. 인증 후에는 아래 위임 범위대로 이 파일에서 Replica→Primary 최종 스냅샷→운영 잔여를 진행하고, 마지막 AWS 실물/보존 검증과 정리 문서 갱신까지 마쳐야 한다. 마지막 정상 검증은 Primary 보호 false·두 DB available인 23:12 기록이며 과거 상태를 현재 인증 성공으로 간주하지 않는다.

## 2026-11-11 23:29 KST · 이번 정리의 남은 apply/destroy를 Codex에 위임, 관리자 인증 입력 대기

- 사용자가 토큰 절약을 이유로 **이번 남은 Terraform apply/destroy를 Codex가 직접 진행**하도록 명시 위임했다. 기존 보존 대상은 유지하며 Replica → Primary 최종 스냅샷·삭제 → 운영 잔여 삭제·실물/보존 검증까지 추가 실행 승인을 반복 요청하지 않는다. 이 위임은 이번 정리의 한정 예외이며 향후 작업의 기본 사용자 직접 실행 원칙을 일괄 변경하지 않는다. 아래 Replica 사용자 실행 대기는 이 위임 이전 상태다.
- 첫 실제 삭제 전 `urlshortener-new`·`urlshortener-admin`의 STS가 ExpiredToken이었다. CLI 자동 갱신 경로의 자격도 실제 STS에서 거부됐다. 사용자가 기존 관리자 재로그인을 승인했다. 첫 `aws login --profile urlshortener-new`은 브라우저의 operator 신원으로 반환돼 root 프로필 덮어쓰기 질문에 **n**으로 거절했고 기존 자격을 유지했다. Terraform 변경은 아직 실행되지 않았다.
- 같은 승인된 관리자 재인증을 `aws login --profile urlshortener-new --remote`로 열었다. exec session `25980`이 브라우저 authorization code를 기다리며 IAB tab `2`는 root 이메일 로그인 화면에 handoff했다. 사용자에게 관리자 이메일/비밀번호/MFA를 직접 입력하고 완료를 알려 달라는 질문이 대기 중이다. 인증 정보는 채팅으로 받지 않는다. CUA 부분은 인증 미완료로 completion_summary를 아직 쓰지 않았다.
- 남은 정리 실행기는 `/private/tmp/cleanup-20261111-finish.py`이며 문법을 확인했다. 개인 `cleanup-20261111/delegation-awaiting-admin-login.json`에 현재 상태를 보존했다. 인증 후 목표 계정/관리자 신원을 실제 STS로 확인하고 이 스크립트를 실행한다. 스크립트는 매 단계 계획을 저장·허용 범위 검증 후 적용하며 private 로그만 남긴다. 기존 Replica 저장 계획은 재실행하지 않고 최신 Replica 한 대 계획을 다시 검증한다. Primary는 available·대기 변경 없음·삭제 보호 false를 확인하고 최종 스냅샷을 생략하지 않는다. 최종 snapshot available·KMS Enabled 확인 후에만 운영 잔여 삭제를 한다.
- 운영 잔여 계획은 최초 state의 허용 주소 집합으로 한정하고 DB·스냅샷·S3·Route53·IAM 사용자·보존 RDS KMS 삭제를 거부한다. Spot 서비스 역할의 타 워크로드 사용 여부도 확인한다. 최종 Terraform 뒤에는 AWS 실물·operator 로그인/MFA/키/정책·DNS/state/기존 및 새 스냅샷 보존, 잔여 전용 로그와 EIP/볼륨까지 확인해야 한다. 원본 재설치/실습 자료는 유지한다.

## 2026-11-11 23:13 KST · Primary 보호 해제 검증 완료, Replica 삭제 사용자 적용 대기

- 사용자가 보호 해제 계획을 적용했고, 23:12 KST의 AWS/state serial 107에서 Primary `DeletionProtection=false`를 확인했다. Primary/Replica는 모두 available·대기 변경 없음이며 복제 관계도 일치했다. Primary 최종 스냅샷 설정은 유지된다. 개인 `cleanup-20261111/rds-protection-off-verified.json`·`production-state-after-protection-off.json`이 근거다. 아래 보호 해제 적용 대기는 이전 상태다.
- Replica DB `module.rds_replica.module.db_instance.aws_db_instance.this[0]` / `urlshortener-postgres-replica`만 대상으로 저장 삭제 계획을 준비했다. **추가 0·변경 0·삭제 1개**이며 Primary·Replica subnet group·모니터링 IAM·다른 인프라 변경/삭제는 없다. Replica의 기존 `skip_final_snapshot=true`를 유지하고 Primary 최종 스냅샷 보존은 유지한다.
- 사용자 실행할 계획은 `~/.local/share/url-shortener-ai-ops/cleanup-20261111/rds-replica-destroy.tfplan`, SHA-256 `14f60f612c8736a12339f3c466ef8583e81a8dab278fa8da1d44789c6b523de8`다. 같은 계정·서울·격리 `source/terraform`·production backend·설치 입력·기존 자격 전달 설정을 사용한다. [정리 기록의 현재 Replica 삭제 명령](docs/runbooks/infrastructure-cleanup-2026-11-11.md)을 사용자 터미널에서 실행한 결과를 기다린다. Codex는 apply/destroy를 실행하지 않았다.
- 다음은 Replica state/AWS 부재 확인 → Primary가 available·대기 변경 없음으로 안정화됐는지 확인 → 최종 스냅샷을 만드는 Primary 삭제 계획과 사용자 명령 → 운영 나머지 destroy 계획·사용자 실행·최종 실물 검증이다. 남은 삭제 계획도 단계별로 검증하며 현재 일반 apply로 Replica를 재생성하지 않는다. DNS·state·스냅샷·operator/기존 자격 보존 원칙을 유지한다. 추천 **GPT-6 SOL · High**.

## 2026-11-11 23:08 KST · AI 삭제 실물 검증 완료, RDS 보호 해제 사용자 적용 대기

- 사용자의 AI Terraform 삭제 완료를 검증했다. AI state serial 12의 관리 리소스 0개, AI ECR·전용 IAM 역할 3개·EKS access entry 2개의 실제 부재를 각각 확인했다. lifecycle/inline 정책은 상위 리소스와 함께 제거됐다. 개인 `cleanup-20261111/ai-deletion-verified.json`·`ai-state-after-user-destroy.json`이 실제 근거다. operator는 보존했다. 아래 AI 적용 대기는 이전 상태다.
- 현재 RDS Primary/Replica는 모두 available·대기 변경 없음이다. RDS 스냅샷 키는 Enabled·AWS 관리이며 운영 Terraform KMS 삭제 대상 밖이다. 기존 수동 스냅샷 5개와 빈 운영 ECR을 확인했다. EKS는 아직 ACTIVE다.
- 격리 정리 사본의 Primary 삭제 보호 한 줄만 임시 false로 바꿨고 원본은 `rds-before-protection-change.tf.txt`에 보관했다. main 소스·다른 작업은 변경하지 않았다. 전체 Terraform validate/plan에서 **추가 0·변경 1·삭제 0**, 변경 필드는 Primary `deletion_protection: true→false`뿐이다. 비밀번호 회전 버전·Replica·다른 관리 리소스는 그대로이고 `skip_final_snapshot=false`다.
- 사용자 실행할 계획은 개인 `~/.local/share/url-shortener-ai-ops/cleanup-20261111/rds-protection-off.tfplan`, SHA-256 `ad0185c8ec1502bc0bb76eade35890d72799f145cab18b0bbde0fe1a5d92476c`다. 작업 디렉터리 `source/terraform`, 동일 production backend·설치 입력·기존 `aws-terraform-config`를 사용한다. [오늘 정리 기록의 현재 명령](docs/runbooks/infrastructure-cleanup-2026-11-11.md)을 사용자 터미널에서 실행한 결과를 기다린다. Codex는 apply/destroy를 실행하지 않았다.
- 다음은 사용자 보호 해제 적용 → 실제 false/available 확인 → Replica 삭제 계획·사용자 적용 → Primary 안정 상태 확인·최종 스냅샷을 포함한 삭제 계획·사용자 적용 → 나머지 운영 destroy와 최종 실물 검증이다. 예정 최종 스냅샷은 `urlshortener-postgres-final-snapshot-urlshortener-postgres-891d94c7`이며 실제 생성 여부는 아직 확인 대상이 아니다. 최종 삭제 후 정리 사본의 보호 설정도 원복한다. DNS·state·기존/최종 스냅샷·operator/로그인은 계속 보존한다. 추천 **GPT-6 SOL · High**.

## 2026-11-11 22:57 KST · 실습 중지·클러스터 선행 정리 완료, 사용자 AI Terraform 삭제 대기

- 사용자가 “여기까지 하고 인프라 및 리소스 정리”를 요청했다. **C-04 실습은 중지했고 새 장애/복구/Git 병합은 수행하지 않는다.** 아래 실행기·앱·worker/PVC가 존재한다는 기록은 삭제 전 이력이다. 새 실제 실습 승인으로 재사용하지 않는다.
- [오늘 정리 기록](docs/runbooks/infrastructure-cleanup-2026-11-11.md)을 우선한다. AI/CI/실습 데이터 3개 아카이브의 SHA·SQLite 검증, ECR 8개 참조의 OCI manifest/config/layer 전체 hash 검증을 마쳤다. 현재 state 두 개와 DNS/IAM 보존 메타데이터·재설치 비밀 원본도 개인 폴더에 보관했다.
- 프로젝트 앱·AI/CI/모델/실습·Karpenter·Traefik·모니터링·Redis·Argo CD·LB Controller를 정리했다. CSI가 살아 있을 때 PVC EBS 7개 부재, LB/TG/NLB ENI/k8s SG 0개를 실제 확인했다. 기본 namespace 네 개만 남고 PV·프로젝트 Helm release는 0개다. 배포/이미지/실습 실행 workflow 5개를 동결했고 클러스터 전용 GitHub Argo 접속 설정을 제거했다. GitHub 보호/필수 CI·저장소·App 키는 보존했다.
- **전체 AWS 정리는 아직 미완료다.** EKS·관리형 노드·RDS Primary/Replica·NAT 등 Terraform 실행 리소스는 남아 비용이 발생할 수 있다. Primary는 available·삭제 보호 true다. Route53 모든 레코드·기존 RDS 스냅샷·두 S3 state 버전·operator와 로그인/기존 키/MFA/연결 정책의 보존을 후속 조회로 확인했다. 이번 `terraform apply/destroy`는 실행하지 않았다.
- 기존 `urlshortener-new` 관리자 세션이 유효해 새 로그인/권한 확대 없이 조회·계획했다. Terraform은 기존 자격 전달 설정 사본 `aws-terraform-config`의 `urlshortener-terraform`을 사용하며 원본 자격은 `urlshortener-new`다. 전역 AWS 프로필은 만들지 않았다.
- 사용자 전용 `~/.local/share/url-shortener-ai-ops/cleanup-20261111/`에 main `63e220ecdcb5018437c2ba4259855375af548466`의 격리 소스와 동일 설치 입력을 준비했다. **AI 저장 삭제 계획: 추가 0·변경 0·삭제 10개**, `ai-destroy.tfplan` SHA-256 `d5fffc46e8df268fde04d2418317c41b060678f4c8e0b540141dabfb4ecd8f21`. 두 ECR 이미지는 비었고 operator 사용자는 계획에 없다. 사용자가 정리 기록의 명령을 자기 터미널에서 실행한 결과를 기다린다.
- 다음은 AI state/AWS 실물 삭제 검증 → RDS 보호 해제 한 건 계획·사용자 apply → Replica/Primary 최종 스냅샷 삭제 계획·사용자 적용 → 운영 잔여 destroy·최종 실물 검증이다. 사용자 실행을 받기 전 다음 Terraform 단계로 넘어가지 않는다. DNS·state·최종 스냅샷·기존 operator/로그인은 계속 보존한다.
- 인프라 설치 없이 C-04를 다시 작업할 때는 `.worktrees/c04-recovery-verification/`와 개인 `c04-20261111/` 실행·사건 자료에서 **실제 알림 전송·완료 영수증 연결의 로컬 재현부터** 시작한다. 실제 C-04/C-05 성공은 미완료다. 실습 Git main은 실패 seed `1e0e107d…`, PR #10은 승인됐으나 미병합이다. 재설치는 정상 SHA `0cfef83c…` 수동 고정이며 실패 main 자동 배포 금지다. 추천 **GPT-6 SOL · High**.

## 2026-11-11 22:38 KST · 종료 후 PR #10 실제 리뷰 확인

- 사용자 답변 뒤 GitHub 원본에서 joungGo(149856960)의 APPROVED 리뷰 `5859607618`을 확인했다. 정확한 head `fd5d2990999a75dabdd7c6d93d3a672fb2d2e7ec`이며 제출은 **22:37:39 KST**, 계획의 22:33:30 리뷰 마감 이후다. 앞선 종료 시점의 리뷰 0건 기록은 당시 원본이며 변경하지 않았다.
- 실습은 이미 알림 시간 게이트로 원복·종료됐고, 이 리뷰로 재개하지 않았다. PR #10은 여전히 open·미병합이며 새 병합·동기화·장애 주입은 없다. 실제 리뷰는 [후속 기록](.worktrees/c04-recovery-verification/ai-ops/docs/assets/C4-pr10-review-after-close-20261111.json)에 보존했다. 기존 정상 상태·보존 자료와 다음 작업은 아래 종료 기록을 따른다.

## 2026-11-11 22:36 KST · 22:30 실행은 알림 시간 게이트로 조기 원복·회수 완료

- 실습 `202611110005` / hash `383d4cc1069116d68962b0e16f8b71479398b9d5ec82865034c121c3c6dd6d2e`은 **종료됐다.** 22:22:33 준비 완료, 22:29:30.006719 자동 진입, 22:30:01.602824 실제 주입, 22:30:09.265755 실패 인계를 확인했다. 새 조사 Job·인증된 사건·제안에서 [복구 PR #10](https://github.com/joungGo/c03-execution-lab/pull/10)을 만들었다. 아래 실행 대기·감독기 진행 중 문장은 과거 상태다.
- PR #10 생성은 22:31:09, head `fd5d2990999a75dabdd7c6d93d3a672fb2d2e7ec`, 필수 CI `130553783174` success는 22:31:22 KST다. 실제 사용자 안내 질문은 전송했지만 `review-notified.json` 기록 실행 시 22:31:30 안내 마감을 넘겨 최소 120초 조건을 충족하지 못했다. 정확한 질문 전달 시각은 계측되지 않았으며 과거 시각으로 알림 영수증을 만들지 않았다. **사용자 리뷰 지연을 중지 원인으로 주장하지 않는다.** PR 최종 조회에서 리뷰는 0건·open·미병합이었다.
- 실행기가 `minimum_notification_window_missed`를 감지해 `restored_without_c03_completion`으로 조기 원복했다. 새로운 C-03 인계·병합·복구 동기화·C-04 120초 회복 관찰은 없다. 사용자 리뷰가 뒤늦게 확인돼도 이 종료된 실행을 재개하지 않는다. PR 안내 후 완료 기록을 연결하는 경로가 분리돼 생긴 지연을 다음 실행 전에 보완해야 한다.
- 후속 실제 조회에서 정상 SHA `0cfef83c36578090dfc1b65513789b1b10159e03` 수동 고정, Synced/Healthy·Ready 1, 빈 Lease, `/readyz`·`/items` HTTP 200과 응답 계약을 확인했다. 원복 cooldown `22:36:50.716918 KST`는 보존했다. 두 역할의 spec/status 토큰 ID 부재, 환경 Secret 부재, 원격 run `43028167525` completed/cancelled, 자체 PID·소켓·파생 core 설정 종료도 확인했다. 감독기 최초 `cleanup_complete=false`는 짧은 원격 취소 poll 때문이며 원본은 보존하고 후속 실제 확인으로 최종 회수를 기록했다.
- 이번 Job `c2-source-capture-202611110005` UID `459a0287-19b9-4fed-b097-ab6208ed3b2d`의 원본·로그를 보존한 뒤 UID/RV 조건으로 삭제했고 Job/Pod 부재를 확인했다. worker/relay·전용 1Gi PVC·기존 로그인/App 키는 보존했다. **Terraform apply/destroy는 수행하지 않았다.** 현재 실습 Git main은 실패 seed `1e0e107da3b726712830d6e68ccc128399a993a2`다. main을 자동 동기화하지 않는다. 별도 Git 정상화의 실제 리뷰·CI·병합이 남는다.
- 개인 실제 증거는 `~/.local/share/url-shortener-ai-ops/c04-20261111/review-window-v2-202611110005/live/`의 `result.json`, `result-handoff.json`, `proposal*.json`, `pr-ready.json`, `post-state.json`, `final-verification.json`, `capture-job-cleanup.json`, `pr-final-readback.json`, `execution-closed.json`, `closed-manifest.json`, `operator-state-closed/`, `capture-job-before-cleanup.json`, `capture-job.log`다. 공개 요약은 [실행 결과](.worktrees/c04-recovery-verification/ai-ops/docs/assets/C4-live-trial-result-202611110005.json), 설명은 [실습 문서](.worktrees/c04-recovery-verification/ai-ops/docs/C4-live-trial-202611110005.md)에 있다.
- 다음은 **실제 알림 전송·완료 영수증 연결을 지연 없이 검증 → 별도 Git 정상화 → 새 시각/hash·최신 사전 검증·실행별 승인 → 새 C-03 인계와 C-04 관찰**이다. 이번 승인을 다른 시각에 재사용하지 않는다. C-04·C-05는 미완료다. 추천 **GPT-6 SOL · High**.

## 2026-11-11 22:22 KST · 실제 준비 완료, 22:30 자동 주입 대기

- 승인된 실습 `202611110005`의 감독기 exec session `24273`, PID `30479`가 진행 중이다. 실제 runner PID `40308`, PR 연결 PID `40309`, 로컬 guard PID `39298`, 원격 guard run `43028167525` / job `130548072041`이다. 같은 실행기의 `live/result-prepared.json`에 **22:22:33.392170 KST 준비 완료**를 기록했다. 22:25 준비 마감 전 완료했다.
- 18개 최소 권한 검사와 기존 정상 CI 영수증·현재 C-02 조사 조회를 대조했다. Computer Use로 이번 `ai-ops-lab-ci`의 `Review deployments → Approve and deploy`를 직접 완료하고 원격 로그의 실제 `C-02 별도 원복 감시 준비 완료`를 확인했다. 실제 관찰은 `live/remote-ready-observed.json`에 보존했다. 두 45분 자격은 이번 실행만 사용하고 종료 후 회수한다.
- 자동 조회를 유지하며 22:29:30 자동 진입·22:30 최종 검사를 통과해야 주입한다. `post-state.json`은 `static_content_warmed_waiting_failure`다. 새 PR이 나오면 22:31:30 전 사용자에게 URL/head/22:33:30 마감을 실제 안내하고 `review-notified.json`을 기록해야 한다. 최소 120초가 없으면 실행 문맥을 만들지 않고 소유 원복으로 전환한다.
- 이 작업을 끝내지 말고 이번 `result-handoff.json`·`pr-ready.json`·`post-state.json`·`result-c03.json`을 관찰한다. 새 PR 실제 사람 리뷰/CI 뒤 병합·동기화·C-03 인계를 확인하고 C-04 최소 120초 관찰과 자기 자격/감시 회수까지 이어간다. 원래 실행기가 살아 있는 동안 소유권 재개·탈취하지 않는다. Terraform은 실행하지 않는다.

## 2026-11-11 21:57 KST · 22:30 계획 승인과 감독기 대기 시작

- 사용자가 실습 `202611110005` / hash `383d4cc1069116d68962b0e16f8b71479398b9d5ec82865034c121c3c6dd6d2e`에 “예, 해당 22:30 계획의 장애 주입·복구 실행 승인”을 제출했다. 실제 답변과 승인 기록은 개인 `review-window-v2-202611110005/authorization-source.json`·`authorization.json`에 있다. 아래 승인 대기는 이 답변 이전 상태다.
- 검증한 같은 runtime의 단일 감독기 exec session `24273`을 시작했다. `live/`는 신규 생성됐고 22:18 전까지 자격 준비 대기 중이다. 대기 구간에는 `state.json`이 아직 없을 수 있다. 22:20 양쪽 감시 → Computer Use 환경 승인·실제 준비 로그 → 22:25 전 실행기의 `result-prepared.json`을 확인해야 한다. PID나 승인만으로 준비 성공을 대체하지 않는다.
- 실제 링크 안내 시 `review-notified.json`을 남겨 최소 120초를 확인한다. 새 PR 정확한 head의 실제 사람 리뷰·CI는 별도다. 22:31:30 안내를 놓치거나 다른 준비 조건이 실패하면 이 계획을 종료·원복하고 자동 연기하지 않는다. Terraform은 실행하지 않는다.

## 2026-11-11 21:50 KST · Git 준비 병합과 검토 시간 개선, 새 계획 승인 대기

- 종료된 실습의 PR #8은 현재 실제 리뷰·CI·정상 tree를 독립 대조해 `19338b865bc207ab7943c9684fd88f653fc9c34f`로 병합했다. 과거 기한을 되살리거나 C-03 성공으로 계산하지 않았다. 이어 새 실행 준비 PR #9의 정확한 head `f637a6d0a9c9a1d1a3a2c6cf57652c5ec6229fea`에 사용자 실제 APPROVED `5858915590`·필수 CI `130524704887`을 확인하고 `1e0e107da3b726712830d6e68ccc128399a993a2`로 일반 squash 병합했다. 두 병합에서 보호 설정·대상 8개 UID/spec을 유지했고 단기 설치 자격을 회수했다.
- **현재 실습 Git main은 PR #9의 실패 seed이며, 실제 앱은 정상 SHA `0cfef83c36578090dfc1b65513789b1b10159e03` 수동 고정이다.** 기존 고정 실패 관측 branch `9338db07…`와 seed의 실패 values는 같다. main을 자동 배포하지 않는다. 아래 PR #8 미병합·Git 정상화 대기 문장은 당시의 기록이다.
- 사람 검토 시간 보완: schema 2는 실제 PR 링크 안내 이후 최소 120초, 리뷰 마감 T+210초, 소유 원복 시작 목표 T+240초, 별도 감시 원복 시작 기한 T+300초다. 안내가 늦거나 조사·PR 경로가 중지되면 소유 실행기가 일찍 원복한다. 실제 알림 기록은 사람 승인이 아니며 정확한 head 리뷰·CI를 다시 확인한다. 이전 3분 원복 목표에서 4분으로 바뀌므로 새 실행별 승인이 필요하다.
- Git SHA 객체와 정상 CI ZIP만 미리 읽어 메모리에서 재사용하고 현재 상태·리뷰·CI listing·만료/digest·클러스터 근거는 매번 다시 검증한다. 불변 콘텐츠 실측을 통과했으며 전체 조사 지연을 보장하지 않는다. 최종 결합 runtime 전체 **778개 로컬 시험 통과**, 655개 파일 manifest hash `ed6c2fa3f6c57387c89d5f511dca8bad759535590ded70a9c98f030a949fe7c5`를 검증했다. [변경·검증 문서](.worktrees/c04-recovery-verification/ai-ops/docs/C4-review-window-v2.md).
- 새 [실습 202611110005 계획](.worktrees/c04-recovery-verification/ai-ops/docs/C4-live-trial-202611110005.md)의 hash는 `383d4cc1069116d68962b0e16f8b71479398b9d5ec82865034c121c3c6dd6d2e`다. **22:30 주입 / 22:18 전 감독기 시작 / 22:25 전 실제 준비 완료 / 22:31:30 전 PR 실제 안내 / 22:33:30 리뷰 마감 / 22:34 소유 원복 목표 / 22:35 감시 원복 시작 기한 KST**. 승인 권장은 22:10까지다. 현재 실행별 승인·예약 프로세스·감시·새 장애는 없으며 `ready_to_inject=false`다. 마감 실패 시 종료하며 자동 연기하지 않는다.
- 실제 seed merge SHA를 넣은 새 읽기/서버 dry-run 사전 점검에서 정상 앱·빈 잠금·worker/relay/PVC·현재 C-02 조회 범위·operator 권한·Git 보호·App 등록·원복 코드·기존 자격 회수 상태와 AWS 정상/실패 이미지를 확인했다. 새 Argo 자격·Secret·감시·조사 Job·동기화·Terraform apply/destroy는 수행하지 않았다. 실환경 활성 감시의 준비 검사는 새 승인 후 필요하다.
- 개인 보존 위치 `~/.local/share/url-shortener-ai-ops/c04-20261111/review-window-v2-202611110005/`: 최종 `plan.json`, `preflight.json`, `expected-baseline.json`, `image-evidence.json`, `preparation-doctor.json`, `live-readonly-final.json`, `release-summary.json`, `full-tests-release.log`, `runtime/`, 별도 `guard-runtime/`. 초기 `plan-draft.json`·777개 시험의 `preparation-summary.json`은 과거 이력이며 최종 파일이 우선한다. PR 병합 원본은 형제 `git-normalization-pr8/`·`next-trial-seed-pr/`다. 과거 실제 실행 runtime/승인/증거는 변경하지 않았다.
- 다음은 새 계획의 명시 실행 승인 → 기한 내 양쪽 실제 감시·실행기 준비 → 새 주입·실제 조사 → 새 PR 정확한 head의 사용자 리뷰·CI → 병합/동기화·C-03 인계 → C-04 최소 120초 관찰과 회수다. C-04 실제 성공·C-05는 아직 미완료다. Terraform 적용은 필요 없다. 추천 **GPT-6 SOL · High**.

## 2026-11-11 21:10 KST · 실제 주입·조사·PR 생성 후 기한 원복과 정리 확인

- 실습 `202611110004` / hash `5f811eb8e10180f65fddc27bce03ce4fc4c62766947e7d2c7c3901c64c26683f`은 **종료됐다.** 자동 진입 `20:59:30.005`, 실제 주입 `21:00:01.298`, 실패 인계 `21:00:09.446` KST다. 같은 Lease 소유권으로 실제 조사 Job과 새 사건/제안을 연결했다. 자동 기동과 실제 갱신 경로는 실제 주입까지 동작했다. 아래 21:00 대기 상태는 과거 기록이다.
- 작성 App의 [복구 PR #8](https://github.com/joungGo/c03-execution-lab/pull/8)은 `21:01:20 KST` 생성, head `cfff6ebc798c3a83d8c8c0b7db3775bdb364c8b7`다. 필수 CI `C03 Main PR Gate`는 `21:01:32`에 success였지만, **21:02 마감까지 리뷰가 없었다.** 실행 문맥은 생성되지 않았고 PR은 미병합/open이다. 사용자 실제 APPROVED 리뷰 `5858453475`는 정확한 head에 `21:07:43 KST` 제출돼 마감 이후다. 이 리뷰로 종료된 실행의 병합·동기화나 새 주입을 재개하지 않는다.
- 21:03 원복 경로에서 `ValueError` 후 같은 소유 실행기의 fallback이 `verified_normal_restored`로 끝났다. 최초 오류의 세부 값은 기록되지 않아 원인을 확정하지 않는다. 기존 정상 SHA `0cfef83c36578090dfc1b65513789b1b10159e03`, Synced/Healthy·Ready 1, 빈 Lease와 `/readyz`·`/items` HTTP 200/응답 계약을 후속 실제 조회로 확인했다. 원복 cooldown은 보존했고 만료 시각은 `21:08:19 KST`다. cooldown 게이트를 원복 건강 실패로 해석하지 않는다.
- 이번 sync/rollback 토큰은 spec/status에서 해당 ID 부재·개수 0, 환경 `C03_ROLLBACK_ARGOCD_TOKEN` 부재, 원격 guard `43015194738` completed/cancelled, 자체 로컬 프로세스/소켓/파생 core 설정 종료를 확인했다. 원격 취소의 완료가 처음 짧은 poll보다 늦어 원본 감독기 `cleanup_complete=false`였고, 후속 API와 `final-verification.json`으로 최종 회수를 확인했다. 이번 Job `c2-source-capture-202611110004`는 UID/RV 조건으로 삭제하고 Pod 부재를 확인했다. 원본 Job/로그는 개인 자료로 보존했다.
- worker/relay Ready 1, 기존 1Gi PVC Bound와 UID, 기존 로그인·App 키를 보존했다. **Terraform apply/destroy는 수행하지 않았다.** 실습 Git main은 아직 실패 seed `000c1612…`이며 Application은 정상 SHA 수동 고정이다. 실습 main을 자동 배포하지 않는다. Git 정상화는 별도 현재 원본 검증과 리뷰/CI/병합으로 처리해야 한다.
- 실제 새 C-03 영수증은 없으며 **C-04 최소 120초 회복 관찰은 미완료**다. PR 생성 뒤 리뷰 마감까지 40초, CI 완료 뒤 28초로 짧았다. 다음 장애 전에 조사/제안 지연과 실제 사람 리뷰 시간을 먼저 조정·검증하고, 새 trial/시각/hash의 실행별 승인을 받아야 한다. PR #8과 이번 사건/승인을 새 실습의 근거로 재사용하지 않는다.
- 개인 보존 위치 `~/.local/share/url-shortener-ai-ops/c04-20261111/automatic-schedule-202611110004/live/`: `result.json`, `result-handoff.json`, `proposal*.json`, `pr-link.json`, `pr-ready.json`, `post-state.json`, `final-verification.json`, `execution-closed.json`, `closed-manifest.json`, `operator-state-closed/`, `capture-job-before-cleanup.json`, `capture-job.log`. 실제 C-02 사건/보고서는 기존 PVC에 보존하며 `queue-metadata.json`으로 식별한다. 공개 요약은 [실행 결과 JSON](.worktrees/c04-recovery-verification/ai-ops/docs/assets/C4-live-trial-result-20261111.json)이다.
- 다음 작업은 **Git 정상화 범위 검토 → 충분한 사람 리뷰 시간을 확보하는 실행 경로/일정 보완 → 새 실습의 C-03 인계와 C-04 관찰**이다. C-05는 C-04 이후다. 추천 **GPT-6 SOL · High**.

## 2026-11-11 20:52 KST · 실제 준비 완료, 21:00 자동 주입 대기

- 실습 `202611110004`의 명시 승인 기록은 `automatic-schedule-202611110004/authorization.json`이다. 감독기 exec session `93254`가 진행 중이며 `live/state.json`의 runner PID는 `5553`, PR 연결 PID는 `5554`, 로컬 guard PID는 `4882`다. 원격 실제 guard는 `43015194738` / job `130504401761`다.
- 실습 sync/rollback의 18개 허용/거부 검사와 실제 C-02 인증 조회, 정상 CI 영수증 대조를 완료했다. Computer Use로 해당 원격 환경 승인과 실제 준비 로그를 확인했고 `live/remote-ready-observed.json`에 관찰 사실을 기록했다.
- **`live/result-prepared.json`의 준비 완료는 `2026-11-11T11:52:04.251099Z`**, 같은 runner PID `5553`이다. 준비 마감 20:55 전 완료했다. 런타임이 자동 조회를 갱신하며 20:59:30에 주입 준비에 진입하고 21:00의 최종 검사를 통과해야만 주입한다. 현재 장애·새 PR은 아직 없다.
- 계속 `live/result-handoff.json`, `post-state.json`, **`pr-ready.json`**을 관찰한다. PR URL/head를 즉시 사용자에게 알려 **21:02 KST 전 정확한 head 실제 리뷰**를 받아야 한다. C-03 영수증(`result-c03.json`) 검증 뒤 이번 자체 감시만 취소하고 C-04 관찰을 완료한다. 시간 부족은 소유 실행기의 21:03 원복 경로를 따른다. 원래 프로세스가 살아 있는 동안 소유권 재개/탈취를 하지 않는다.

## 2026-11-11 20:46 KST · 21:00 계획 실행 승인과 준비 대기

- 사용자가 정확한 계획 `202611110004` / hash `5f811eb8e10180f65fddc27bce03ce4fc4c62766947e7d2c7c3901c64c26683f`에 “예, 해당 21:00 계획의 장애 주입·복구 실행 승인”을 제출했다. 실제 승인 기록 시각은 `2026-11-11T11:38:32Z`다. 아래 승인 대기 표기는 이 답변 이전 상태다. 새 PR의 정확한 head 리뷰는 별도로 필요하다.
- 최초 감독기 기동에서 미등록 Path 인자를 발견했다. 수정 뒤 개인 폴더 내 코드 경로를 데이터 저장 대상으로 허용하는 문제도 좁혀 수정했다. 목표·권한·일정은 바꾸지 않았다. 실제 영구 위치에서 **전체 768개 로컬 시험 통과**, 현재 runtime 651개 파일 hash를 검증했다. 개인 폴더의 `parser-fix/`, `path-boundary-fix/`, `verified-local-fixes.json`에 원본·변경·실패/통과 로그를 보존한다.
- 자격 발급 전 대기 중이던 최초 시도는 중지/회수 완료했고 `preparation-stopped-before-credentials/`에 보존했다. 토큰·원격 run·주입은 없었다. 새 감독기 exec session `93254`는 같은 계획으로 시작했으며 20:48 준비 시작을 기다린다. 실제 폴더는 개인 `automatic-schedule-202611110004/live/`다. 20:48 전에는 `state.json`이 없는 것이 현재 구현의 예상 상태다.
- 다음은 20:48 자격/조사 연결 → 20:50 양쪽 감시 → Computer Use 환경 승인·실제 원격 준비 로그 관찰 기록 → 20:55 전 실행기 `result-prepared.json` 확인이다. 이후에만 21:00 주입/새 PR/정확한 리뷰/복구/C-04 관찰을 진행한다. 현재 장애는 아직 주입하지 않았다.

## 2026-11-11 · 자동 기동/조회 갱신 검증과 새 21:00 계획 준비

- 새 계획은 [실습 202611110004](.worktrees/c04-recovery-verification/ai-ops/docs/C4-live-trial-202611110004.md)이며 hash는 `5f811eb8e10180f65fddc27bce03ce4fc4c62766947e7d2c7c3901c64c26683f`다. **21:00 KST 주입 / 20:48 이전 감독기 시작 / 20:55 이전 실제 준비 완료 / 21:02 PR 리뷰 마감 / 21:03 소유 실행기 원복 시작 목표 / 21:05 감시 원복 시작 기한**이다. 여유를 위해 20:45까지 승인을 받는 것을 권장한다. 현재 새 승인·예약 프로세스·감시·장애 주입은 없으며 `ready_to_inject=false`다. 마감을 넘기면 이 계획을 자동 연기하거나 종료된 20:00 승인을 재사용하지 않는다.
- [자동 기동 문서](.worktrees/c04-recovery-verification/ai-ops/docs/C4-automatic-schedule.md)에 구현·검증·순서를 기록했다. 실행기의 `prepare()`가 같은 프로세스에서 실제 검사를 마감 전에 완료해야 하며, `schedule_trial()`이 예정 30초 전에 자동 진입한다. 수동 launch 표식과 마지막 순간의 도구 호출을 제거했다. 늦은 준비·조회 실패/만료·시계 변경·느린 최종 읽기는 잠금/Argo 쓰기 전에 차단한다.
- 기존 operator의 STS/EKS/ECR 읽기를 boto3 client로 반복 갱신하고 실제 조회 시작 시각을 보존한다. 현재 감시 PID/로그·원격 run/step/main SHA도 별도로 갱신한다. 실패 즉시 과거 성공 캐시를 폐기하며 유효기간은 10초다. 마지막 snapshot은 두 단계 병렬 읽기로 바꾸되 전후 원본 대조와 5초 쓰기 상한은 유지한다.
- 보존 C-03 위에 새 `c_trial_*`와 명시한 진입점만 결합한 최종 runtime 전체 **766개 로컬 시험 통과**. 최초 넓은 overlay의 C-03 계약 충돌은 수정했으며 실패 기록과 최종 통과 로그를 구분해 보존했다. 최종 코드의 실제 SDK 자동 조회 4개 표본 및 정상 snapshot/캐시 세 번은 **1.49/1.26/1.29초**, 외부 쓰기 0회다. 새 43바이트 Unix 소켓의 실제 bind/close와 원격 main의 별도 schema2 guard 소스도 검증했다. 새 활성 감시의 실제 준비 검사는 향후 실행 승인 후 수행해야 한다.
- 새 읽기/서버 dry-run 사전 점검에서 정상 앱·빈 Lease·worker/relay/PVC·현재 C-02 조회 권한·operator 권한·실습 main/실패 branch·필수 리뷰/CI·작성/병합 App 등록·원복 소스·자격 회수 상태를 대조했다. 8개 대상 UID/spec과 Git 보호 설정의 전후 불변성을 확인했다. 이번 준비에서 새 Argo/설치 토큰·Secret·감시·조사 Job·PR·동기화·Terraform apply/destroy는 실행하지 않았다. 로컬 SDK/CRT 선택 의존성만 설치했다.
- 영구 자료: `~/.local/share/url-shortener-ai-ops/c04-20261111/automatic-schedule-202611110004/`의 `plan.json`, `preflight.json`, `preparation-summary.json`, `preparation-doctor.json`, `live-readonly-final.json`, `full-tests.log`, `runtime/runtime-manifest.json`, `guard-runtime/`. 현재 `authorization.json`과 `live/`는 없다. runtime 650개 파일과 SDK 버전을 고정했으며 실제 감독기/실행기가 기동 전에 검사한다. 종료된 실습의 원본은 수정하지 않았다.
- 다음은 **새 계획에 대한 실제 실행별 승인**이다. 승인 뒤 위 검토안의 단일 감독기 경로를 기동한다. GitHub 환경 승인은 Computer Use로 수행하고 실제 원격 준비 로그의 관찰 시각을 기록한다. 20:55 이전 `result-prepared.json`을 확인해야 하며 runner PID만으로 대신하지 않는다. 이후 새 실제 C-02 조사와 PR URL/head를 즉시 알리고 정확한 사람 리뷰·CI 확인 → 병합/동기화 → 새 C-03 인계 → C-04 최소 120초 관찰을 진행한다. 새 PR 승인과 장애 주입 승인은 별개다.
- 실습 main은 아직 실패 seed `000c1612…`, Application은 정상 `0cfef83c…`에 수동 고정돼 있다. 새 활성 감시 미기동 상태를 정상 준비 성공과 혼용하지 않는다. Terraform 적용은 필요 없으며 향후 apply/destroy는 사용자 터미널 원칙을 유지한다. 추천 **GPT-6 SOL · High**.

아래는 이전 20:00 중지 및 그 이전 이력이다. 다음 작업은 위 새 계획을 우선한다.

## 2026-11-11 20:01 KST · 승인된 20:00 실습은 주입 없이 종료

- 사용자 승인 계획 `202611110003` / hash `be1fa1b9c5558b25e3d719b3c53be768205995929655eb6e641ab875dd071333`을 실제 준비했으나 **장애 주입·새 복구 PR·병합·동기화는 0회**다. 실행기 결과는 `status=stopped`, `fault_injected=false`, `injection_attempted=false`다. C-04 실제 회복 검증은 아직 미완료다. 아래 승인 대기·예정 상태는 과거 준비 기록이다.
- 준비 중 Unix 소켓 경로 길이 초과와 보존 runtime의 오래된 schema1 원복 감시 코드가 발견됐다. 짧은 `/private/tmp/c04-t3-ops` 경로와 원격 main `63e220ec…`에서 추출한 별도 `guard-runtime/`으로 준비를 수정했다. 첫 준비의 자격·Secret은 회수했고, 수정 후 양쪽 실제 감시 준비 로그도 확인했다. GitHub 배포 승인은 Computer Use로 수행했다.
- 그러나 19:55 준비 마감까지 양쪽 준비가 완료되지 않았고, 마지막 실제 AWS 증거 저장·수동 기동 요청도 20:00:09 KST로 늦었다. 실행기는 허용 주입 시각 20:00:05를 넘어 외부 쓰기 전에 거부했다. 이번 승인·시각은 종료됐으며 다른 시각에 재사용하지 않는다. 시각 변경 없이 재시도하지 않는다.
- 20:01:18 KST 실제 재조회: 앱 정상 SHA `0cfef83c36578090dfc1b65513789b1b10159e03`, Synced/Healthy, Ready 1, 기존 Lease UID 유지·holder 빈 값. worker/relay Ready 1, 전용 PVC Bound다. 두 원격 감시 run `43006909409`·`43007670006`은 cancelled/completed, 자체 로컬 프로세스 종료, sync/rollback 역할 토큰 0개, `C03_ROLLBACK_ARGOCD_TOKEN` Secret 부재와 파생 core kubeconfig 제거를 확인했다. 기존 로그인·App 키·PVC는 보존했다. Terraform apply/destroy는 수행하지 않았다.
- 실습 Git main은 여전히 실패 seed `000c1612b298e9142d3eaa33142eb36891812d70`이다. Application은 정상 SHA 수동 고정을 유지한다. seed main을 자동 동기화하지 않는다. 정상화에는 새 정상화 PR의 실제 리뷰·CI·병합이 필요하다.
- 실제 개인 증거는 `~/.local/share/url-shortener-ai-ops/c04-20261111/execution-adapter/live-202611110003/`의 `result.json`, `state.json`, `final-verification.json`, `execution-closed.json`, `closed-manifest.json`, `operator-state-closed/`, `guard-runtime/manifest.json`이다. 기존 계획·승인·runtime manifest도 보존한다. 임시 소켓 경로를 실행 자료의 영구 위치로 사용하지 않는다.
- **다음 작업:** 새 승인을 요청하기 전에 원복 guard의 schema2 소스/원격 SHA 일치, 19:55 같은 준비 마감의 강제 중지, 미리 준비한 로컬 자동 기동·실제 이미지 증거 갱신, 마지막 snapshot/guard 조회가 5초 주입 창에 들어오는지를 비실행 검증한다. 그 뒤 새 trial ID·미래 일정·hash의 검토안과 실행별 승인으로 진행한다. 이후 실제 C-02 조사 → 새 복구 PR의 정확한 head 리뷰 → 병합/동기화 → 새 C-03 인계 → 최소 120초 C-04 관찰이 남아 있다. 추천 **GPT-6 SOL · High**.

---

2026-11-11 KST 기준. **실제 Mac 실행기에 잠금 인계·C-02 조사·C-03 승인 기반 복구·C-04 관찰을 연결하고 전체 736개 시험을 통과했다.** 19:18 KST의 실제 읽기·서버 dry-run 사전 점검도 통과했다. 새 실습 `202611110003`의 계획은 오늘 20:00 주입, 19:55 승인 마감, 20:03 자체 원복 시작 목표, 20:05 감시 원복 시작 기한이다. 검토 시간을 확보해 승인 전 19:30 초안을 갱신했다. 현재 실행별 승인 대기이며 `ready_to_inject=false`다. 격리 실습 자격 한정 예외는 AI 구현 완료 전까지 적용하며 Terraform apply/destroy는 사용자 터미널에서 실행한다. 추천은 **GPT-6 SOL · High**다.

## 최신 완료 · 실제 실행기 연결과 새 실습 사전 검증

- [실행기 연결 문서](.worktrees/c04-recovery-verification/ai-ops/docs/C4-executor-connection.md)와 [실습 202611110003 검토안](.worktrees/c04-recovery-verification/ai-ops/docs/C4-live-trial-202611110003.md)을 따른다. 계획 hash는 `be1fa1b9c5558b25e3d719b3c53be768205995929655eb6e641ab875dd071333`다. 이전 승인을 재사용하거나 이 일정의 마감을 넘겨 자동 연기하지 않는다.
- `c_trial_execution.py`·`c_trial_transport.py`·`c_trial_source.py`·`c_trial_github.py`와 `run_c4_trial.py`가 실제 고정 대상 API에 연결된다. 소유권은 같은 프로세스에서 유지하고, 정확한 승인 head·필수 CI·별도 병합 App의 짧은 자격을 대조한 뒤 검증한 merge SHA만 동기화한다. C-03 인계를 별도 파일에 기록하고 C-04의 120초 이상 관찰로 연결한다.
- 새 adapter 시험 30개와 결합 runtime 전체 736개가 통과했다. 원래 프로세스 종료를 확인한 같은 소유권의 원복 재개는 `restore_c4_trial.py`다. 다른 holder·새 UID·알 수 없는 SHA·진행 중 Argo 작업은 중지한다. 로컬/원격 감시는 같은 Lease에 의존해 원복을 거부할 수 있으므로 소유 실행기의 180초 원복과 종료 후 수동 재개 경로를 실행 검토안에 포함했다.
- `urlshortener-operator`로 실제 대상 계정·ACTIVE EKS와 정상 ECR ACTIVE/실패 ImageNotFoundException을 확인했다. 정상 SHA의 Synced/Healthy·Ready 1, 빈 Lease, worker·relay·PVC, 현재 worker에 한정한 C-02 조회 역할, 조사/잠금 권한, 실습 Git main·필수 리뷰/CI와 병합 App 등록을 대조했다. 조사 Job 서버 dry-run과 8개 대상의 UID/spec·Git 보호 전후 불변성을 확인했다. 실제 장애 주입·실제 조사 Job·새 복구 PR·병합은 이번 준비에서 수행하지 않았다.
- 영구 보관 위치는 `~/.local/share/url-shortener-ai-ops/c04-20261111/execution-adapter/`다. `runtime/`·`manifest.json`·`plan.json`·`preflight-final.json`·`image-evidence-final.json`·시험 로그를 사용한다. 현재 main만으로는 일부 C-03 모듈이 부족하므로 이 검증된 묶음에서 실행한다.
- 다음은 위 정확한 계획의 새 실행별 장애 승인이다. 승인 뒤 짧은 sync/rollback 자격과 실제 양쪽 감시를 기동 직전에 연결·검증한다. 새 복구 PR의 정확한 head에 대한 실제 사용자 리뷰는 PR 생성 후 별도로 필요하다. 실습 main은 실패 seed `000c1612…`, 실행 중 앱은 정상 `0cfef83c…`에 수동 고정된 상태를 사전 검증했다. 취소/fallback 후 main에 seed가 남으면 정상화 PR·리뷰·CI·병합으로 마무리한다.

아래는 앞선 단계의 기록이다. 다음 작업과 실행 상태는 위 최신 결과를 우선한다.

## 최신 완료 · 실패 잠금 인계 로컬 검증

- 같은 C-04 작업 트리에 `c_failure_handoff.py`, 인계 Schema와 집중 시험을 추가하고 C-02 조사 준비에 `--failure-handoff`를 연결했다. 기본 빈 잠금 경로는 유지한다. 지정 실습의 실패 인계를 제공한 경로만 사용 중 Lease에서 조사한다.
- `offer_failure_handoff()`가 주입 소유자의 Lease에 인계 hash를 기록한다. `acquire_failure_preflight()`는 제안 범위를 대조하고 동일 Lease UID에서 지정 c3 holder로 원자적으로 넘긴 뒤 기존 C-03 승인·필수 CI·현재 장애 검사를 수행한다. `FailureHandoffReader`와 `plan_failure_merge()`가 전후 원본·소유권·기한을 다시 검사한다. 계획은 계속 `execution_allowed=false` / `write_enabled=false`다.
- 새 집중 시험 22개, C-03 보존 runtime과 결합한 전체 706개가 통과했다. 소유권 경쟁·다른 대상/Pod/generation·기한 초과·재실행·CAS 충돌·응답 유실·전달 뒤 상태 변경·잘못된 리뷰·실패 CI를 시험했다. 소유권이 불확실하거나 검증이 실패하면 현재 Lease를 보존한다.
- 설명과 연결 순서는 [실패 인계 문서](.worktrees/c04-recovery-verification/ai-ops/docs/C4-failure-handoff.md), 검증 요약은 [로컬 시험 기록](.worktrees/c04-recovery-verification/ai-ops/docs/assets/C4-failure-handoff-local-20261111.json)에 있다. 코드·입력·응답은 로컬 합성 시험 범위다. 이 작업에서 실환경 조회·변경은 수행하지 않았다.
- 새 영구 보관 위치는 `~/.local/share/url-shortener-ai-ops/c04-20261111/failure-handoff-local/`다. `runtime/`에 C-03/C-04와 새 인계 코드, `manifest.json`에 기준/overlay/파일 해시, `focused-tests.log`·`full-tests.log`에 시험 결과를 보존한다. 기존 `handoff/` 묶음과 원본 C-03 증거는 보존했다.
- 다음 작업은 실제 주입 프로세스의 새 원본 조회와 인계 제공 호출, C-03 쓰기 adapter 직전의 현재 잠금·기한 대조, 잠금 보존 시 소유자 확인·수동 원복 경로를 포함한 새 실행 검토안이다. 새 실행별 장애 승인과 새 복구 PR 정확한 head의 실제 리뷰 후 실환경 C-03 인계·C-04 120초 관찰로 이어간다. C-04 실환경 회복 검증과 C-05는 아직 남아 있다.


## 최신 작업 위치 · C-04 수집기 연결

- 작업 브랜치: `codex/c04-recovery-verification-20261111`.
- 독립 작업 트리: `/Users/jounghyeon/dev/Url-Shortener-EKS-Platform/.worktrees/c04-recovery-verification`.
- Git 공통 저장소는 보존된 `/Users/jounghyeon/dev/Url-Shortener-EKS-Platform-c03/.git`이며 임시 설치 폴더에 의존하지 않는다. 기준 main은 `63e220ecdcb5018437c2ba4259855375af548466`이다. 기존 미커밋 C-03 작업은 변경하지 않았다.
- 구현·검증 설명: [C-04 문서](.worktrees/c04-recovery-verification/ai-ops/docs/C4-recovery-verification.md). `c_verification.py`·`c_verification_archive.py`에 더해 `c_verification_collect.py`·`c_verification_transport.py`·`collect_c4_recovery.py`와 계획 예시·Schema·시험이 이 브랜치에 있다. 아직 커밋·push·PR·배포하지 않았다.
- 판정은 목표 revision·실제 이미지·UID, 같은 관찰 창의 사용자 요청·지표·경보를 묶어 해결/미해결/검증불가로 나눈다. 실패 후에는 기본 중지·보고하며, 기존 범위와 같은 후속 후보도 `execution_allowed=false`다.
- 실제 보존 C-03 자료는 시각·지표·경보 표본이 충분하지 않아 검증불가였다. 과거 복구 실패나 현재 클러스터 장애를 뜻하지 않는다. 합성 성공 결과와 실제 보존 자료의 결과를 구분한다.
- 실제 수집기의 클러스터 GET에서도 과거 C-03와 현재 대상의 UID 불일치를 확인했다. 현재 Application UID `e8673d8e-9598-4181-8362-a772be4c7251`, Deployment UID `d1a6ea67-490b-4141-966c-7ee28190354f`였다. `target_identity_mismatch`, 관찰·HTTP·포트 전달 0회로 중지했다. 현재 시각이나 UID로 과거 영수증을 보충하지 않는다.
- 기본 CLI는 대상 대조뿐이다. `--collect`에서만 기존 운영자 자격으로 고정 대상 GET·임시 루프백 포트 전달을 수행한다. `/readyz`·`/items`, up·가용 replica와 실제 경보 정의를 검사한다. 원본 응답/비밀값은 보고서에 넣지 않는다. 합성 수집 성공·실패와 루프백 전송을 검증했고 실제 HTTP/Prometheus의 정상 관찰은 미확보다.
- 새 연결점은 `verify_c03_handoff()`다. 기존 `verify_sync_result()`가 성공한 직후 UTC 시각을 출력하며, 스냅샷부터 완료까지 15초 초과·잠금 상실·다른 대상은 인계하지 않는다. 기존 파일에 현재 시각을 넣는 진입점은 없다.
- `prepare_c4_collection.py`로 실제 `KubeDeploymentReplicasMismatch` 규칙을 확인·고정했다. 현재 대상의 `/readyz`·`/items` 200/응답 계약, up·replica·generation 여섯 지표의 신선도를 단일 준비 점검으로 확인했다. 시각 정밀도 보완 전의 두 지표 수집 실패와 수정 후 성공을 모두 보존했다. 복구 이후 120초 관찰이나 새 C-03 실행으로 사용하지 않는다.
- 현재 준비 결과는 `waiting_for_c03_handoff`, `fresh_c03_handoff_required`, `collection_plan=null`이다. 새 인계가 있으면 UID·현재 소스/적용 SHA·이미지와 대조하고 실제 규칙을 고정한다. 인계 이후 scrape/규칙 평가를 최대 약 90초 준비 대기한 뒤 별도의 관찰 창으로 넘어간다.
- 검증된 개인 로컬 실행 묶음은 `~/.local/share/url-shortener-ai-ops/c04-20261111/handoff/ai-ops/`이며 같은 폴더의 `manifest.json`에 원본 C-03 아카이브와 C-04 overlay 해시가 있다. 기존 C-03 모듈이 없는 현재 main 기반 체크아웃에서 실제 인계 함수를 호출하면 실패한다. 실행 묶음으로 C-03 검증 → 인계 → 계획 → C-04 해결 판정을 합성 자료로 확인했다. 원격 배포나 새 자격 활성화는 하지 않았다.
- 로컬 검증 결과는 사용자 전용 `~/.local/share/url-shortener-ai-ops/c04-20261111/`에 보존한다. 원본 C-03 기록은 수정하지 않았다.
- 앞선 재설치는 [11월 11일 설치 기록](docs/runbooks/installation-preflight-2026-11-11.md)에 있다. 운영·AI 기본 구성, 격리 실습·CI 자동 정상 관측까지 확인했고 모델은 비활성이다. 아래 인프라 정리 결과는 **11월 10일의 과거 기록**이며 현재 설치 상태를 뜻하지 않는다.

### 최신 실습 준비와 다음 작업

- **실습202611110002 취소:** 18:05에 실행할 예약/프로세스는 없다. 기존 승인·시각을 재사용하지 않는다. 개인 `c04-20261111/live-trial-review-01/execution-cancelled.json`·`next-stage-context.json`과 작업 트리 `C4-live-trial-cancelled-20261111.json`을 따른다.
- 실행 경로 공백: 주입 동안 공유 잠금을 소유해야 하지만 C-02 활성 수집/C-03의 잠금 전 검사는 빈 잠금을 요구하고, release는 정상 복구 확인 때만 가능하다. 정확한 주입 소유권·UID·기한·실패 근거를 묶은 인계가 필요하다. 보존 runtime의 4개 로컬 검사로 이 조건을 재현했으며 탈취/가짜 recovery_verified는 하지 않았다.
- 이번 rollback45분 자격을 실제 발급·지정 환경 전달/권한 대조했고 취소 후 ID/iat·Secret updated_at을 대조해 회수했다. sync 토큰·App 키·실제 감시·주입·새 PR은 사용/실행0이다. 정상 source/operation·실습/운영/worker/relay/PVC UID/spec과 빈 Lease가 유지된다.
- 사용자 지시로 AI 구현 완료 전까지 기존 승인 격리 실습 범위의 자격 한정 예외는 반복 동의 없이 적용한다. root AGENTS.md가 기준이다. 새 장애 실행별 승인과 새 PR 실제 리뷰는 별개다. 다음은 위 잠금 인계 구현·로컬 검증이며 먼저 새 장애 승인부터 요청하지 않는다.


- 최신 완료 근거는 `c04-20261111/active-guard-preparation/completion/approved-rehearsal-final.json`·`next-stage-context.json`과 작업 트리 `C4-active-guard-rehearsal-20261111.json`이다. 상위 개인 폴더의 초기 승인 대기 문맥은 과거 기록이다. 새 원복 자격을 실제 발급·허용/거부 검사했고 로컬/원격 prepare_only #42988913974가 모두 성공했다. Computer Use로 환경 승인을 직접 완료했다.
- 이번 고유 ID의 유일한 iat로 토큰을 회수해 spec/status ID 부재를 확인했고 원격 Secret·파생 설정도 제거했다. 원본 Project spec·기존 S10 Secret·로그인·키는 보존했다. 역할 토큰0, 정상source/operation·실습/운영/worker/relay·PVC 불변, 빈 Lease·seed main/보호 유지다. 이번 준비 예외는 소진됐고 실제 실행 자격은 기동 직전 새로 준비해야 한다.
- 다음은 실제 예정 시각·300초 원복 시작 기한·양쪽 감시 실제 준비·잠금 보존 시 소유자 확인/수동 원복·새 C-02→C-03→C-04 연결을 포함한 실행별 검토안과 장애 승인이다. actual_trial_deadline_utc=null·실제 감시 비무장·ready_to_inject=false다.


- 최신 원복 준비: 개인 `c04-20261111/active-guard-preparation/`에 현재 원본·비실행 plan·자격 검토안·guard-runbook·next-stage-context를 보존한다. `C4-active-guard-preparation-20261111.json`이 공개 가능한 hash/검사 요약이다. 앱은 정상 0cfef83c, 실패 후보9338db07, seed main000c1612로 유지한다.
- 현재 역할 토큰0·원복 Secret 부재·빈 Lease·실행 중 원복 workflow 없음, 실행 main63e220ec의 workflow/guard/계약/잠금 코드 일치를 확인했다. 기존 원복 unittest8개와 현재/합성 스냅샷 검증을 통과했다. 실제 토큰·로컬/원격 prepare_only는 이번 한정 예외 승인 뒤 수행·회수하며 실제 무장/장애 주입 승인이 아니다.
- 실제 두 감시는 같은 클러스터/Argo/잠금에 의존한다. C-03 잠금 보존 시 양쪽이 거부할 수 있어 프로세스 종료·소유권 대조·고정 정상 SHA 수동 원복 후 원래 소유자의 잠금 해제 경로를 실행별 검토안에 포함한다. 새 정상 merge SHA도 두 SHA 감시가 거부할 수 있으므로 C-03 인계 검증과 별도로 판정한다. 지연으로 기한까지240초 미만이면 주입하지 않는다. 300초는 원복 시작 기한이며 sync완료까지 보장하지 않는다.


- 최신 병합 결과: PR #7 → `000c1612b298e9142d3eaa33142eb36891812d70`, 부모/정상 원복 `0cfef83c36578090dfc1b65513789b1b10159e03`, 관측 후보 `9338db07b7856883c56e0e0e8ba5f49d6924680d`. 실패 lab tree는 `ffc2ac437dfc71fbdcbe7399dd890462a6ca8765`로 같다. 후속 복구 PR의 base와 관측 SHA를 혼용하지 않는다.
- 최초 즉시 결과 검증 실패는 보존했고 병합 재시도 없이 별도 읽기로 28개 조건을 확인했다. 최초 불일치 세부 값은 없어 원인을 확정하지 않는다. 별도 병합 App의 단기 자격 폐기/401, 보호·branch·클러스터 원본·운영 앱 불변을 확인했다. 이번 일회 키 예외는 소진됐고 다음 자격 작업에 확대하지 않는다.
- 현재 앱은 정상 SHA 0cfef83c에 수동 고정돼 있다. 재설치에서도 이 정상 SHA를 사용하며 실패 seed main을 자동 배포하지 않는다. 취소/독립 원복만으로 main 정상화가 끝나지 않으므로 별도 정상화 PR·리뷰·CI·병합이 필요하다. AWS 정리는 기존 가이드를 따르고 이 Git 상태를 보존한다.
- 최종 근거: `.worktrees/c04-recovery-verification/ai-ops/docs/assets/C4-seed-merge-20261111.json`, 개인 `c04-20261111/seed-merge/verified-result.json`·`next-stage-context.json`·검증한 `seed-main.bundle`. 다음은 새 실행용 자격·양쪽 실제 감시·잠금 보존 시 수동 복구 경로·실행별 승인을 준비한다. 새 장애 주입·새 C-03 인계·120초 회복 관찰은 아직 없다.

다음 줄부터는 앞선 단계 작성 당시의 기록이며 위 최신 결과를 우선한다.

- 사전 PR #7 실제 리뷰 확인: 사용자 joungGo(149856960), 리뷰 5855711951, APPROVED, commit_id `9338db07b7856883c56e0e0e8ba5f49d6924680d`. 모든 리뷰 페이지의 최신 유효 상태·필수 CI check 130401986241와 App 15368·base 0cfef83c·한 파일/한 필드·main 보호 hash를 확인했다.
- 앱 UID·수동/정상 SHA 고정·source/적용/operation·Ready·빈 Lease도 다시 읽었다. exact PR #7의 squash 병합과 App 5696044·설치 180499786의 한 저장소 Contents write/PR read 자격 사용 검토안을 개인 `c04-20261111/seed-review/merge-review.md`에 준비했다. hash는 `72817318278f690fe03926e95760309a155fa4f2ac5894547ca8fdc0328930fa`다. 자동 branch 삭제=false이며 branch를 보존한다. 이전 예외는 작성 App에 한정돼 현재 병합/병합 키 사용은 승인 대기다. 실제 기한은 null·ready_to_inject=false다.
- [Git 사전 PR #7](https://github.com/joungGo/c03-execution-lab/pull/7/files) 생성 완료: 작성 bot 354870297, 정확한 head `9338db07b7856883c56e0e0e8ba5f49d6924680d`, base `0cfef83c36578090dfc1b65513789b1b10159e03`, 이미지 digest 한 필드다. 사용자 “사전 PR 작성 예외 승인”에 따라 App 5696038·설치 180499782의 한 저장소 PR write/metadata read만 요청했다. Contents 권한·새 커밋·branch 변경을 사용하지 않았고 설치 자격은 폐기 후 401을 확인했다.
- 정확한 head에서 GitHub Actions App 15368의 C03 Main PR Gate check 130401986241·실행 42984387549가 success다. PR 생성 직후에는 리뷰 []였고, 후속으로 위 정확한 head의 사용자 승인을 확인했다. PR은 여전히 미병합이다. 별도 병합 App 키 사용/병합은 아직 승인되지 않았고 실제 기한·주입도 없다. 최신 원본은 개인 c04-20261111/seed-pr/에 보존한다.
- 새 선행 조건: 실제 격리 main `0cfef83c…`의 digest가 desired 정상 `dbd367bd…`여서 기존 prepare_pr_link는 장애를 관측해도 main_already_at_desired_digest·link 없음으로 끝난다. 이 조건이 이전 준비 순서에 빠져 있었음을 확인했다. 기존 시험 2개로 변경 불필요와 서로 다른 관측/main SHA 연결 경로를 확인했고 게이트를 완화하지 않았다.
- Git 사전 PR 입력을 개인 `c04-20261111/execution-preparation/`에 준비했다. base `0cfef83c…`, head `9338db07…`, 이미지 digest 한 필드이며 아직 같은 head의 PR은 없다. 현재 앱은 수동·정상 SHA 고정이라 이 준비 PR 병합만으로 배포되지 않는다. 사용자 리뷰·CI·별도 병합 승인 뒤 main의 seed merge SHA를 새 복구 PR base로 사용하고 관측 source 9338db07과 실패 lab tree 일치를 검사한다.
- 사전 PR 작성 예외 검토안 hash `2d8fa0604a9eab7cbe30d76bd661fc98efefa9936b226a5e8f376d1b41f12099`는 기존 작성 App 5696038·설치 180499782로 PR write만 요청해 한 PR을 만들고 즉시 자격 폐기하는 범위다. 이전 예외는 PR 작성을 제외했으므로 재사용하지 않는다. 당시 키·토큰 사용 전 승인 대기였고, 위 후속 승인으로 사전 PR 작성까지만 수행·회수했다.
- 취소/fallback 시 앱만 복귀해도 main에 실패 설정이 남을 수 있다. main 정상 digest를 되돌리는 별도 PR·리뷰·CI·병합까지 마무리한다. 공유 Lease를 탈취하지 않으며 C-03 소유 실패/수동 복귀 절차·실제 두 감시 준비 출력·새 시간 계획과 실행별 승인을 확정하기 전에는 주입하지 않는다. 실제 기한은 null이다.
- 현재 worker/PVC에 맞춘 조사 Job c2-source-capture-202611110001 정의·서버 dry-run은 통과했다. 실제 Job/Pod는 만들지 않았고 활성 실패 검증은 아직 하지 않았다. 실제 base의 불변 CI 객체 게이트로 9338db07 head를 검사해 통과했다.
- 실제 원격 실패 후보: 전용 작성 App이 [branch](https://github.com/joungGo/c03-execution-lab/tree/codex/c04-failure-4273d17cfde8)에 `9338db07b7856883c56e0e0e8ba5f49d6924680d`를 게시했다. 작성자 bot ID 354870297·직접 부모 `0cfef83c…`·이미지 digest 한 필드·검토한 파일 hash·main/보호 불변을 확인했다. Contents만 요청한 단기 설치 자격은 즉시 폐기 후 401을 확인했다. 로컬 초안 `3691652c…`는 실제 원격 SHA로 쓰지 않는다. 실제 커밋의 Git bundle을 별도 보존했다.
- 이번 한정 예외 요청에 대한 사용자 “진행해”를 해당 준비 범위 승인으로 기록했다. 로컬 `--core --prepare-only`와 [원격 읽기 리허설 #42978765848](https://github.com/f-lab-edu/F-Lab-DevOps/actions/runs/42978765848)이 모두 완료 문구와 success로 끝났다. 원격 환경 승인은 Computer Use로 직접 클릭했다. 각각 새 리허설 기한을 썼고 실제 장애 실습 기한으로 재사용하지 않는다.
- 45분 `c03-rollback` 자격의 lab-target get/update/sync 허용과 delete/override·운영 앱·Project 수정 거부를 검증했다. 리허설 뒤 Project 토큰 ID·전용 GitHub 환경 Secret·파생 kubeconfig/Argo CLI 설정을 제거했다. `c03-sync`·`c03-rollback` 토큰 수는 각각 0, 환경에는 기존 S10_ARGOCD_TOKEN만 있으며 원본 Project spec·로그인·키를 보존했다. 최초 회수에서 로컬 CLI의 ISSUED-AT 인수에 ID를 넘겨 실패한 기록과 정확한 iat 대조 후 회수 성공을 함께 보존했다.
- 기존 C-02 bearer로 현재 정상 사건 요약을 TLS HTTP 200으로 조회했고 bundle hash와 사건 ID를 대조했다. 정상 보고서는 partial/insufficient_evidence, image_failure=null이므로 복구 제안 입력으로 사용하지 않는다. 제한된 포트 전달은 종료했다.
- 최신 원본·실제 Git bundle·다음 문맥은 개인 `c04-20261111/rollback-rehearsal/`에 있다. 정상 source·적용·작업, 앱/운영/worker/relay UID와 spec, PVC·빈 Lease·main 보호를 최종 확인했다. 실제 기한은 null, 감시는 미기동, ready_to_inject=false다. 다음 실제 실행에는 새 자격·제안/리뷰/병합 경로와 실행별 장애 승인을 준비한다. 이번 예외에는 후속 복구 PR·병합 App 키 사용·실제 주입/원복 쓰기가 포함되지 않는다.
- 초기 실패 후보 준비: 개인 `c04-20261111/rollback-preparation/`의 로컬 branch `codex/c04-failure-4273d17cfde8`, 후보 SHA `3691652c2874f80460faa3a56d07d02a3ba43df4`는 `0cfef83c…`의 직접 자식이며 이미지 한 필드만 다르다. 후보를 담은 Git bundle도 보존했다. 초기에는 실제 봇의 원격 SHA가 없어 failure_revision을 null로 뒀고, 위 후속 리허설에서 실제 `9338db07…`를 확보했다. 서버 dry-run 2개에서 같은 UID·정상 spec·이미지 한 필드 차이를 확인했고 현재 정상 원본·UID/저장소/revision/기한 거부 계약을 대조했다.
- `ai-ops-c2-rollback.yml`은 active이며 main `63e220e…`의 workflow·guard·script가 로컬과 같다. 환경은 main·필수 승인, 새 계정의 S10 역할이다. main 보호·병합 App 제한 전체 해시는 기존과 같다. 원복 Secret은 없고 C-03 역할의 토큰/그룹은 0개다. 그 준비 시점에는 두 prepare_only와 독립 감시가 미실행이었다. 위 후속에서 두 읽기 리허설만 완료했고 실제 감시는 미기동이다. 원격 실패 SHA 확인·실행별 준비 후 UTC 기한을 새로 고정하고, 감시가 예정 주입 전에 준비되지 않으면 취소한다. 최대 장애 300초·180초 이내 수동 원복 시작 계획은 유지한다.
- 당시 자격 예외 검토안 `rollback-preparation/credential-exception-review.md` SHA-256 `01d5cf83e58519b11dfcbc94df06e7c0c5b5149cd1eb7455dd40ab38fa70eb37`을 준비했다. `AGENTS.md`가 요구하는 `aws-secrets-manager` 스킬은 MCP/로컬에서 없고, 이전 App 예외는 재사용하지 않는다. 요청은 이번 작성 App의 실패 branch 게시, 45분 rollback 자격과 지정 GitHub 환경 Secret, Argo namespace 파생 kubeconfig, 양쪽 읽기 리허설 및 기존 C-02 bearer 조회 후 이번 임시 자격만 회수하는 범위다. 후속 사용자 승인으로 위 준비 범위만 수행·회수했다. 실제 장애 주입·원복 쓰기·병합은 포함하지 않는다.
- ECR 정상 digest는 DescribeImages의 ACTIVE, 실패 digest는 API의 ImageNotFoundException으로 확인했다. 최초 조회 wrapper의 예외 이름 오류와 후속 정상 재조회 결과를 구분해 보존했다. 추가 manifest 조회는 `ecr:BatchGetImage` AccessDeniedException으로 거부돼 미검증이며 권한을 확장하지 않았다.
- 조사 binding 연결 완료: `ai-ci/ci-config`의 실습 Argo repo 한 필드만 격리 저장소로 변경했다. relay를 중지하고 worker 재시작·로드 검증 뒤 relay를 복구했다. 새 worker는 `ci-worker-fc7879598-6p6gm`, UID `96e1a734-5139-4a5b-88ff-5b7db6ee957d`, 설정 해시 `37739999c2217f45a36bf174d172a5cd865122852fa59a450bf8208739fb9df4`다. 기존 정상 CI 수집기 binding 해시 `0e4e94df…`는 다른 용도라 그대로다. PVC UID `4a004d79-4736-45ea-8ba1-e74ff3da38bf`·볼륨·정상 CI 요약 1건의 입력/결과 해시와 ready 상태를 보존했다.
- 실제 worker 조사 `job-aecc6fba50ba4a5ebe07649007c1a20c`는 요청·감사·근거 origin에 같은 새 설정 해시를 기록했고 현재 Argo 근거 available·revision `0cfef83c…`를 확인했다. 사건 `inc-bf153f8442514a2aa97d801afcfa57f3`/실행 `run-99d9c2ef0d5d4a76ac58a44efa5936ba`의 규칙 보고서는 partial이다. 트래픽·CI 이력 등의 부족과 이전 저장소 배포 이력의 범위 불일치를 보존했다. 정상 조사이며 장애 복구 성공이나 새 C-03 인계가 아니다. 새 GitHub CI를 실행하지 않았다.
- C-02 Role·RoleBinding은 새 worker 한 개로 이름·대상 UID만 회전했다. ServiceAccount·get/portforward 범위는 유지하고 이전 Pod·Secret·exec·목록·쓰기·운영 namespace 거부를 실제 확인했다. ServiceAccount 가장으로 실제 제한된 루프백 포트 전달·공개 인증서 호스트 검증·미인증 401 후 종료했다. 이번에는 유효 bearer 사건 조회를 재검증하거나 자격을 발급하지 않았다.
- 최신 원본과 `next-stage-context.json`은 개인 `c04-20261111/investigation-binding/`에 보존한다. 다음은 실패 커밋·기한·독립 원복의 prepare_only 입력과 실제 기동 경로를 준비하는 단계다. 새 조회/원복/동기화 자격·양쪽 감시와 실행별 장애 승인이 준비되기 전에는 `ready_to_inject=false`다.
- 정상 소스 인계 완료: 현재 Application UID `e8673d8e-9598-4181-8362-a772be4c7251`·Deployment UID `d1a6ea67-490b-4141-966c-7ee28190354f`를 확인하고 Project에 격리 저장소와 `lab-target` get/update/sync 전용 `c03-sync`·`c03-rollback` 역할을 추가했다. 새 역할은 token/group 0개이며 기존 ci-sync 설정은 그대로다. 수동 정책·prune=false·실습 Deployment 한 개로 정상 SHA를 동기화했다. source·적용·작업 SHA 일치, HTTP `/readyz`·`/items` 200, Deployment spec·Pod UID·운영 앱 불변을 확인하고 잠금을 해제했다. 최초 동기화 요청 거부 기록은 보존하고 같은 소유 Lease 안에서 재조회·건식 검증 후 동일 정상 요청으로 완료했다. 최초 오류 본문은 없어 정확한 원인은 확정하지 않는다.
- 정상 소스 인계 당시의 원본·검증은 개인 `c04-20261111/source-handover/`에 보존한다. 당시 미적용이던 조사 binding은 위 후속 단계에서 실제 적용·검증했다. 정상 소스 연결과 조사 로드 확인은 새 C-03 복구 영수증이나 C-04 해결 판정을 대신하지 않는다.
- PR #6 병합 완료: 정확한 승인 head `6eb5c3e…`, base `36bb912…`를 재검증하고 `c03-execution-lab-merge[bot]`(354870499)가 `0cfef83c36578090dfc1b65513789b1b10159e03`에 squash 병합했다. 부모·이미지 한 필드·정상 values·main을 대조했고 보호 설정과 업데이트 제한 규칙 전체 hash는 그대로였다. 실제 SHA를 PR head와 혼용하지 않는다.
- 이번 병합 App 키 사용은 사용자가 별도로 일회 예외 승인했다. App 5696044·설치 180499786·한 저장소 Contents write/PR read 범위를 확인하고 자격 폐기 후 401을 확인했다. 키·토큰 원문은 보관하지 않았다.
- 최초 병합 직후 `verify_merged_commit` 확인은 실패했지만 병합 API 자체는 성공했다. 병합을 재시도하지 않고 별도 읽기 재조회로 최종 확정했다. 최초 값이 없어 정확한 하위 불일치 조건은 알 수 없다. 원본·최종 검증·다음 단계 문맥은 개인 `c04-20261111/normal-baseline-merge/`에 보존한다. 브랜치 삭제·클러스터 적용·장애 주입은 하지 않았다.
- [정상 기준선 PR #6](https://github.com/joungGo/c03-execution-lab/pull/6) 생성 단계: base `36bb912fcfec359c10bd17fc48ba1afe4e21dd7f`, head `6eb5c3eefd5564d6aefda7bd52a0d69d51030e26`, 작성자 `c03-execution-lab-writer[bot]`(354870297). 사용자 `joungGo`(149856960)의 리뷰 `5854390079`는 해당 head의 APPROVED다. GitHub Actions App ID 15368의 `C03 Main PR Gate`가 성공했고 로컬에서도 같은 불변 객체 게이트를 통과했다. 후속 병합은 위 기록과 구분한다.
- 작성 App 5696038·설치 180499782와 한 저장소 접근 범위를 인증된 원본으로 확인했다. 사용자는 지정 스킬 부재에 대해 **이번 작성 App 키 사용만** 예외 승인했다. 단기 자격은 메모리에서만 사용하고 폐기 후 401을 확인했다. 병합 App 키·병합·영구 지침 변경은 이번 예외에 포함되지 않는다. 원본은 개인 `c04-20261111/normal-baseline-pr/`에 보존한다.
- [새 실습 준비 문서](.worktrees/c04-recovery-verification/ai-ops/docs/C4-isolated-trial-preparation.md)에 현재 기준선, 미적용 범위 패치, 정상/실패 이미지 변경안과 순서를 기록했다. 원본·실행 묶음은 `~/.local/share/url-shortener-ai-ops/c04-20261111/trial/`에 보존한다.
- 초기 준비 조회에서는 source·Project·조사 binding이 운영 저장소였다. 후속 인계로 source·Project를 변경했고, 그다음 조사 binding도 새 실습 저장소로 맞췄다. 초기 격리 main `36bb912`의 이미지 `838cd1c8…`는 ECR에 없어 재사용하면 안 된다. 현재 정상 digest는 `dbd367bd…`다.
- 새 정상/실패 차트 렌더는 이미지 한 필드만 다르다. 정상 렌더의 서버 dry-run은 현재 Deployment UID/spec과 같았다. 실제 리소스는 변경하지 않았다. 실패 후보 `4273d17c…`는 ECR에서 ImageNotFoundException을 확인했다. 실행 직전에 두 이미지 존재/부재를 재검사한다.
- 초기 준비의 Project 패치는 후속 인계에서 적용됐다. `ci-config`의 실습 Argo repo 한 필드 패치는 그다음 조사 연결 단계에서 현재 UID·원본을 검사해 적용했다. 기존 ci-sync·로그인은 유지했다.
- 기존 원격 원복 workflow는 active이고 환경의 S10 역할은 새 계정이다. C-03 역할은 후속 인계에서 token/group 없이 정의했으나 원복 Secret·로컬 감시용 연결은 없어 실제 prepare_only 리허설과 두 감시 기동은 남았다. 초기 원복 draft는 null SHA·기한으로 보존하고, 새 `normal-baseline-merge/next-stage-context.json`에 확정 정상 SHA `0cfef83c…`를 연결했다. 실패 SHA·기한은 여전히 null이며 `ready_to_inject=false`다. 제안은 장애 최대 300초, 180초부터 수동 원복이다.
- 격리 저장소는 리뷰 1명·필수 CI·병합 App 제한을 유지한다. 이전 열린 PR #4는 재사용하지 않는다. 초기 공개 App 조회 404와 달리, 후속 PR #6 생성·병합에서 두 App의 인증된 identity와 각각 한 저장소 설치 자격을 실제 검증했다. 발급한 단기 자격은 모두 폐기했다.
- `validate_active_failure()`와 준비 CLI에 명시적인 `--source c03-isolated`를 추가했다. 기본값은 기존 저장소이고 다른 저장소·다른 UID·사용 중 잠금은 거부한다. 관련 신규 3개와 전체 640개 시험을 통과했다. 새 runtime/manifest는 이전 인계 묶음에 이 두 소스 파일을 추가한 것이며 미배포다.

새 C-03 인계와 그 이후 같은 대상의 120초 이상 관찰 창은 아직 없다. 다음 실습에서는 보존된 기존 승인을 재사용하지 말고 현재 정상/실패 source·독립 원복 감시·정확한 제안 PR과 리뷰·실행 기한을 먼저 준비한다. C-01의 실행별 장애 주입 승인을 받은 뒤에만 진행한다. 초기 준비는 읽기 GET·임시 포트 전달만 했고, 후속 정상 소스 인계에서 위 Project·Application을 변경했다. 장애 주입·유료 모델 호출은 없다. 실제 승인·수집 출처·Lease·실행 직전 원본 검증을 로컬 JSON의 hash로 대체하지 않는다. C-05 R1~R8은 후속 범위다.

## C-03 실행 결과

- 운영 `main`과 분리된 [실습 저장소](https://github.com/joungGo/c03-execution-lab)의 `lab-target`에 장애를 재현했다. 장애 SHA `b5b59a5`에서 `ImagePullBackOff`와 Ready 0을 확인했다. 첫 시도의 [복구 PR #4](https://github.com/joungGo/c03-execution-lab/pull/4)는 기한 내 유효한 승인이 확인되지 않아 독립 원복으로 정상 SHA `7b55ebb`에 복귀했다.
- 재실행에서 사용자가 마감 전에 [복구 PR #5](https://github.com/joungGo/c03-execution-lab/pull/5)의 head `2316198`을 승인했다. 실행기는 공유 Lease 아래 승인·장애·변경을 재검증했고, 병합 App이 squash 병합한 SHA `36bb912fcfec359c10bd17fc48ba1afe4e21dd7f`를 Argo CD로 동기화했다. Argo CD는 Synced/Healthy, Pod는 Ready 1/1, `/readyz`와 `/items`는 HTTP 200이었다. Lease 해제까지 확인했다.
- 보존된 `c03/recovery-verified.json`의 `sync_verified=true`는 동기화 확인 결과다. `service_resolved=false`는 C-03에서 서비스 전체의 해결 판정을 유보한 값이다. C-04는 사용자 요청 경로, 지표, 경보의 회복을 각각 검증해야 한다.
- [정상 CI #42829522584](https://github.com/f-lab-edu/F-Lab-DevOps/actions/runs/42829522584)는 장애 재현 전 기준선이다. Artifact `13771890811`에서 HTTP 200 네 건과 캐시 `miss → hit → miss → hit`를 확인했다. 당시 재시작한 `ci-worker`는 1Gi PVC를 유지했고 해당 영수증을 `normal_ci_rules_summary`의 `ready`로 저장했다. 모델 호출은 0건이었다. PR #5 복구 후 앱 Pod UID가 바뀌었으므로 향후 실환경 정상 CI에는 새 Pod 기준 binding과 worker 로드 확인이 필요하다.
- [원격 원복 감시 #42831248882](https://github.com/f-lab-edu/F-Lab-DevOps/actions/runs/42831248882)는 기한 시점의 복구 SHA가 감시 대상으로 등록한 두 SHA와 달라 쓰기 전에 종료됐고, Actions 결과는 failure였다. 로컬 예비 감시를 종료했고 단기 원복·동기화 토큰과 GitHub 환경 Secret을 회수했다.

상세 기록은 [C-03 실행·실습 런북](docs/runbooks/c03-execution-lab-preparation.md#c-03-승인-기반-복구-완료)에 있다.

## 인프라 정리 결과

- AWS `764428911194` / `ap-northeast-2`에서 운영·AI Terraform state 모두 관리 리소스 0개를 확인했다. EKS `urlshortener`, RDS Primary·Replica, VPC·NAT·EIP·EC2, 두 ECR, 로드밸런서·Target Group, EBS 7개, Karpenter 큐, 프로젝트 CloudWatch 로그 그룹의 제거를 확인했다.
- 기존 보존 정책을 적용했다. Route 53 공개 호스팅 영역 `bidservice.store`와 기존 레코드, S3 backend `urlshortener-tfstate-764428911194-ap-northeast-2`의 운영·AI state 객체와 버전, 기존 RDS 스냅샷, IAM 사용자 `urlshortener-operator`와 로그인 자격을 보존했다. DNS·스냅샷·S3에는 보존 비용이 발생할 수 있다.
- 최종 RDS 스냅샷 `urlshortener-postgres-final-snapshot-urlshortener-postgres-82b79976`은 암호화됐고 `available`이었다. 스냅샷 암호화 키는 `Enabled`다. 별도의 EKS 키는 `PendingDeletion`이며 삭제 예정일은 2026-12-11 KST다.
- 폐기한 클러스터 전용 Argo CD 접속 설정을 GitHub에서 제거했다. 운영 자동 배포 `ci-cd.yml`은 재설치 전까지 수동 비활성 상태다. `C03 Main PR Gate`와 실습 저장소는 보존했다. 정리 후 `urlshortener-new` 관리자 세션은 로그아웃했고 `urlshortener-operator` 인증을 확인했다.
- 사용자가 AI root 삭제 계획을 직접 실행했다. 이후 남은 운영 정리에 한해 사용자 위임으로 Codex가 검토된 계획을 적용했다. 향후 `terraform apply`와 `terraform destroy`는 사용자가 직접 실행하는 것이 기본이다.

근거와 절차는 [정리 실행 기록](docs/runbooks/infrastructure-cleanup-2026-11-10.md), [정리·재설치 대상표](docs/runbooks/c03-cleanup-and-reinstall.md), [재설치 가이드](docs/runbooks/infrastructure-installation-and-validation.md)에 있다.

## 보존 자료

기본 위치는 사용자 전용 `~/.local/share/url-shortener-ai-ops/cleanup-20261110/`이다. C-04의 첫 입력은 `c03/`의 JSON 기록과 실행기 소스다.

| 위치 | 용도 |
|---|---|
| `c03/` | 장애 관측 `argo-fault-observed.json`, `pod-fault-observed.json`, PR 연결·제안·병합·동기화 기록, 복구 판정 입력 `recovery-verified.json`, `c03-restored-http.json`, `c03-restored-runtime.json`, 원복 로그 `remote-rollback.log` |
| `c03-executor-source.tgz`, `c03-executor-sha256.txt` | 로컬 실행기와 테스트. 운영 기준 `fbdf404a4832cf659bd1f173ace626bd8d9f75e1`의 `ai-ops/`에 C-03 임시 작업 트리의 Python 소스·스크립트·테스트를 덮어 담았다. 기본 체크아웃에 없는 C-03 코드가 포함된다. 임시 작업 트리나 가상환경에 의존하지 않고 압축을 풀어 쓸 수 있다. |
| `ai-ops-data.tgz`, `ai-ci-data.tgz`, `lab-collector-data.tgz`, `data-sha256.txt` | 워커·정상 CI·격리 수집기 데이터. 필요한 경우에만 로컬 임시 경로에 풀어 읽는다. |
| `urlshortener.oci`, `urlshortener-practice-ai-ops.oci`, ECR 목록 JSON | 재설치 시 사용할 앱·AI 이미지와 참조 정보. 정리 전에 앱 이미지 참조 3개와 AI 이미지 참조 2개의 digest를 검증했다. |
| `production-pre-cleanup.tfstate`, `ai-pre-cleanup.tfstate`, `terraform-state-sha256.txt` | 정리 전 상태의 비공개 기록. 민감 정보가 들어 있을 수 있으므로 로컬에 보존한다. 재설치는 현재 비어 있는 운영·AI backend에서 계획하며, 정리 전 사본을 backend에 복원하면 삭제된 리소스의 상태가 다시 등록된다. |
| `rds-final-snapshot.json`, `rds-existing-snapshots.json` | 재설치 때 복원 후보를 확인하는 메타데이터. |
| `~/.local/share/url-shortener-ai-ops/c03-app-keys/` | 별도 보관한 실습 GitHub App 개인 키. 향후 실환경 작업용이며 C-04 로컬 검증에는 필요 없다. |

인계 작성 시 소스·데이터·state 체크섬 여섯 개를 확인했다. 소스 아카이브를 별도 임시 디렉터리에 풀어 실행·승인·병합·동기화 관련 단위 테스트 45개를 실행했고 모두 통과했다.

## C-04 로컬 검증 절차 · 완료한 작업의 재현

1. 아래 명령으로 독립 작업 디렉터리를 만들고 C-03 입력 계약과 테스트 기준선을 확인한다. 다음 구현에 반영할 변경을 추릴 때는 보존 소스와 저장소의 현재 작업 트리를 먼저 대조한다.
2. 보존된 `recovery-verified.json`과 복구 증거를 읽어 목표 revision·이미지·앱·HTTP 응답을 연결한다. 이 기록은 11월 10일의 관측이다. 지표와 경보 회복의 성공·실패·증거 누락은 합성 fixture로 분리한다.
3. 목표 revision, 사용자 요청 경로, 지표·경보의 회복을 검증하고 **해결 / 미해결 / 검증불가**를 근거와 함께 보고한다. 정상, revision 불일치, HTTP 실패, 경보 지속, 지표·경보 증거 누락 또는 수집 실패를 시험한다. 저장된 HTTP 200만으로 전체 해결을 선언하지 않는다.
4. 검증 실패는 기본적으로 중지·보고한다. 승인 범위 안의 후속 복구만 허용되는지, 범위 밖 변경이 추가 승인 요청으로 이어지는지 테스트 대역으로 확인한다. 이 로컬 단계에서는 AWS, Kubernetes, Argo CD, GitHub 쓰기나 실시간 상태 조회가 필요 없다.
5. 검증기·판정 보고·실패 처리 기록과 테스트 결과를 남긴다. 이후 C-05에서 R1~R8 평가와 최종 데모를 별도로 진행한다. 실환경 재설치가 필요한 작업은 그때 범위를 정한다.

이 Mac에서는 검증에 사용한 Python 3.13으로 새 가상환경을 만든다. 의존성이 이미 준비됐다면 `C04_PYTHON`에 해당 환경의 Python 절대 경로를 지정해 다운로드 없이 실행할 수 있다.

```sh
(
  set -eu
  archive_dir="$HOME/.local/share/url-shortener-ai-ops/cleanup-20261110"
  (cd "$archive_dir" && shasum -a 256 -c c03-executor-sha256.txt)
  c04_workdir=$(mktemp -d "${TMPDIR:-/tmp}/urlshortener-c04.XXXXXX")
  tar -xzf "$archive_dir/c03-executor-source.tgz" -C "$c04_workdir"
  cd "$c04_workdir"
  printf 'C-04 작업 디렉터리 %s\n' "$PWD"
  c04_python=${C04_PYTHON:-}
  if [ -z "$c04_python" ]; then
    python3.13 -m venv .venv
    c04_python="$PWD/.venv/bin/python"
    "$c04_python" -m pip install --require-hashes --only-binary=:all: -r ai-ops/requirements.lock
  fi
  for pattern in 'test_c_execution*.py' 'test_c_approval.py' 'test_c_merge_guard.py' 'test_c_sync_guard.py'; do
    PYTHONPATH=ai-ops/src "$c04_python" -m unittest discover -s ai-ops/tests -p "$pattern"
  done
)
```

C-04와 C-05의 공식 완료 조건은 [AI 작업 리스트](<실습 가이드 문서 모음/AI/구현계획/AI 작업 리스트.md>)를 따른다.
