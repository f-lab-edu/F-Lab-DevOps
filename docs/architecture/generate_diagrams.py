"""Render architecture diagrams from the repository's declared production configuration.

Run with Python 3. The SVG files are self-contained and remain editable vector files.
"""

from __future__ import annotations

from html import escape
from pathlib import Path


OUT = Path(__file__).resolve().parent

INK = "#17233b"
MUTED = "#56647a"
LINE = "#cad5e5"
BLUE = "#2864d8"
GREEN = "#087f69"
PURPLE = "#7546b8"
ORANGE = "#c87617"
RED = "#bd4b42"


class SVG:
    def __init__(self, width: int, height: int):
        self.width = width
        self.height = height
        self.parts = [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
            f'viewBox="0 0 {width} {height}" role="img">',
            """<defs>
              <marker id="arrow-blue" markerWidth="11" markerHeight="11" refX="9" refY="5.5" orient="auto"><path d="M0 0 L11 5.5 L0 11 Z" fill="#2864d8"/></marker>
              <marker id="arrow-green" markerWidth="11" markerHeight="11" refX="9" refY="5.5" orient="auto"><path d="M0 0 L11 5.5 L0 11 Z" fill="#087f69"/></marker>
              <marker id="arrow-purple" markerWidth="11" markerHeight="11" refX="9" refY="5.5" orient="auto"><path d="M0 0 L11 5.5 L0 11 Z" fill="#7546b8"/></marker>
              <marker id="arrow-orange" markerWidth="11" markerHeight="11" refX="9" refY="5.5" orient="auto"><path d="M0 0 L11 5.5 L0 11 Z" fill="#c87617"/></marker>
              <marker id="arrow-muted" markerWidth="11" markerHeight="11" refX="9" refY="5.5" orient="auto"><path d="M0 0 L11 5.5 L0 11 Z" fill="#8794a9"/></marker>
              <filter id="shadow" x="-15%" y="-20%" width="130%" height="150%"><feDropShadow dx="0" dy="4" stdDeviation="8" flood-color="#17233b" flood-opacity=".10"/></filter>
            </defs>""",
            '<rect width="100%" height="100%" fill="#f6f8fc"/>',
        ]

    def rect(self, x, y, w, h, fill, stroke="none", r=22, sw=2, dash=None, shadow=False):
        extra = f' stroke-dasharray="{dash}"' if dash else ""
        fx = ' filter="url(#shadow)"' if shadow else ""
        self.parts.append(
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="{sw}"{extra}{fx}/>'
        )

    def text(self, x, y, value, size=26, color=INK, weight=500, anchor="start", line=1.26):
        lines = value.split("\n")
        self.parts.append(
            f'<text x="{x}" y="{y}" font-family="Apple SD Gothic Neo, Noto Sans CJK KR, Arial, sans-serif" '
            f'font-size="{size}" font-weight="{weight}" fill="{color}" text-anchor="{anchor}">'
        )
        for i, item in enumerate(lines):
            dy = 0 if i == 0 else size * line
            self.parts.append(f'<tspan x="{x}" dy="{dy}">{escape(item)}</tspan>')
        self.parts.append("</text>")

    def path(self, d, color=BLUE, sw=5, marker=True, dash=None, opacity=1):
        mark = f' marker-end="url(#arrow-{self._marker_name(color)})"' if marker else ""
        dashed = f' stroke-dasharray="{dash}"' if dash else ""
        self.parts.append(
            f'<path d="{d}" fill="none" stroke="{color}" stroke-width="{sw}" '
            f'stroke-linecap="round" stroke-linejoin="round" opacity="{opacity}"{dashed}{mark}/>'
        )

    @staticmethod
    def _marker_name(color):
        return {BLUE: "blue", GREEN: "green", PURPLE: "purple", ORANGE: "orange"}.get(color, "muted")

    def card(self, x, y, w, h, title, details="", *, fill="white", stroke=LINE,
             title_color=INK, title_size=27, detail_size=20, shadow=True):
        self.rect(x, y, w, h, fill, stroke, 20, 2, shadow=shadow)
        self.text(x + 24, y + 39, title, title_size, title_color, 700)
        if details:
            self.text(x + 24, y + 73, details, detail_size, MUTED, 500, line=1.32)

    def pill(self, x, y, w, text, fill, color, size=19):
        self.rect(x, y, w, 37, fill, "none", 18)
        self.text(x + w / 2, y + 25, text, size, color, 700, "middle")

    def save(self, filename: str, title: str, description: str):
        self.parts.insert(1, f"<title>{escape(title)}</title><desc>{escape(description)}</desc>")
        self.parts.append("</svg>")
        (OUT / filename).write_text("\n".join(self.parts), encoding="utf-8")


def platform_network():
    s = SVG(2400, 1520)
    s.text(60, 83, "AWS EKS 플랫폼 네트워크 아키텍처", 53, INK, 800)
    s.text(62, 131, "운영 코드 선언 기준 · 서울 리전(ap-northeast-2) · 사용자 트래픽은 파란색, 데이터 접근은 초록색", 25, MUTED)

    # AWS and VPC boundaries. Route 53 is in AWS but outside the VPC.
    s.rect(375, 175, 1970, 1037, "#ffffff", "#9fb2ce", 30, 3)
    s.pill(407, 190, 196, "AWS Cloud", "#e9f0fc", BLUE)
    s.rect(712, 255, 1588, 932, "#fafdff", "#7699cb", 25, 3)
    s.pill(735, 269, 273, "VPC 10.0.0.0/16", "#e8f0fc", BLUE)

    # User and DNS.
    s.card(42, 326, 277, 142, "인터넷 사용자", "API: 허용 IP만 HTTPS\n관리 UI: 별도 호스트", fill="#ffffff", stroke=BLUE,
           title_size=27, detail_size=20)
    s.card(407, 322, 258, 150, "Route 53", "api.bidservice.store\nargocd.bidservice.store\n두 Alias → 같은 NLB", fill="#f1f5ff", stroke=BLUE,
           detail_size=18)
    s.path("M319 396 H407", BLUE, 6)
    s.path("M665 396 H692 V307 H1115 V430 H1131", BLUE, 6)

    # Public subnets and shared NLB.
    s.rect(754, 324, 700, 178, "#edf5ff", "#b8cde9", 19)
    s.rect(1490, 324, 755, 178, "#edf5ff", "#b8cde9", 19)
    s.text(780, 356, "Public 10.0.0.0/24 · AZ 2a", 24, BLUE, 700)
    s.text(1515, 356, "Public 10.0.1.0/24 · AZ 2c", 24, BLUE, 700)
    s.card(783, 388, 265, 88, "NAT Gateway ×1", "프라이빗 outbound 전용", fill="#fff4e5", stroke=ORANGE,
           title_size=23, detail_size=18, shadow=False)
    s.card(1134, 379, 770, 107, "인터넷 공개 Traefik NLB · 80 / 443", "두 Public Subnet · Pod IP 타깃 · Traefik TLS / HTTP 308",
           fill="#e8f0ff", stroke=BLUE, title_size=29, detail_size=21)
    s.pill(2010, 233, 234, "Internet Gateway", "#fff4e5", ORANGE, 19)

    # Private subnets. Traefik spread is strict, API spread is a preference.
    s.rect(754, 541, 700, 410, "#effaf8", "#b8dcd5", 19)
    s.rect(1490, 541, 755, 410, "#effaf8", "#b8dcd5", 19)
    s.text(780, 576, "Private 10.0.10.0/24 · AZ 2a", 24, GREEN, 700)
    s.text(1515, 576, "Private 10.0.11.0/24 · AZ 2c", 24, GREEN, 700)
    s.text(780, 608, "EKS 관리형 노드 / Karpenter 노드", 19, MUTED)
    s.text(1515, 608, "EKS 관리형 노드 / Karpenter 노드", 19, MUTED)
    s.card(805, 637, 400, 101, "Traefik Pod", "두 Pod는 AZ/노드 분산", fill="#f2eeff", stroke=PURPLE, title_size=28, detail_size=20)
    s.card(1732, 637, 400, 101, "Traefik Pod", "NLB가 Pod IP로 직접 전달", fill="#f2eeff", stroke=PURPLE,
           title_size=28, detail_size=20)
    s.path("M1310 486 V517 H1250 V625 H1005 V634", BLUE, 6)
    s.path("M1740 486 V517 H1932 V634", BLUE, 6)

    # Kubernetes Service is only in the Traefik -> API hop.
    s.card(1232, 697, 477, 90, "url-shortener-svc :80", "ClusterIP · API Pod 선택", fill="#e5f8f1", stroke=GREEN,
           title_size=27, detail_size=20)
    s.path("M1205 711 H1219 V742 H1229", GREEN, 5)
    s.path("M1732 711 H1722 V742 H1712", GREEN, 5)
    s.card(805, 815, 400, 93, "FastAPI Pod :8000", "운영 HPA 2–10 Pods", fill="#e5f8f1", stroke=GREEN,
           title_size=27, detail_size=20)
    s.card(1732, 815, 400, 93, "FastAPI Pod :8000", "AZ 분산은 선호 설정", fill="#e5f8f1", stroke=GREEN,
           title_size=27, detail_size=20)
    s.path("M1320 787 V799 H1005 V812", GREEN, 5)
    s.path("M1620 787 V799 H1932 V812", GREEN, 5)

    # Data stores are in private space; RDS placement is selected by AWS.
    s.card(783, 1020, 320, 122, "Redis Pod + PVC", "Kubernetes · Cache Aside\n캐시 장애 시 DB 조회", fill="#fff2e7", stroke=ORANGE,
           title_size=25, detail_size=19, shadow=False)
    s.card(1123, 1020, 320, 122, "RDS Primary", "쓰기 · 최근 쓰기 후 읽기\ndb.t3.micro · Multi-AZ", fill="#fff2e7", stroke=ORANGE,
           title_size=25, detail_size=19, shadow=False)
    s.card(1463, 1020, 320, 122, "Multi-AZ Standby", "Primary 장애 시 자동 전환\n일반 읽기 요청은 처리 안 함", fill="#fff2e7", stroke=ORANGE,
           title_size=25, detail_size=19, shadow=False)
    s.card(1803, 1020, 320, 122, "RDS Read Replica", "일반 캐시 미스 읽기\nPrimary에서 비동기 복제", fill="#fff2e7", stroke=ORANGE,
           title_size=25, detail_size=19, shadow=False)
    s.path("M1005 908 V973", GREEN, 4, marker=False)
    s.path("M1932 908 V973", GREEN, 4, marker=False)
    s.path("M943 973 H1963", GREEN, 4, marker=False)
    s.path("M943 973 V1017", GREEN, 4)
    s.path("M1283 973 V1017", GREEN, 4)
    s.path("M1963 973 V1017", GREEN, 4)
    s.text(782, 1172, "RDS는 두 AZ의 프라이빗 서브넷 그룹 사용 · 개별 인스턴스의 AZ는 코드에서 고정하지 않음", 20, MUTED, 600)

    # Operations are intentionally not connected as customer-request hops.
    s.text(416, 1239, "운영 제어·관측  ·  고객 요청의 중간 홉이 아닌 별도 경로", 26, INK, 700)
    s.card(415, 1270, 420, 143, "CI/CD · 이미지", "GitHub Actions → ECR push\nGit의 image.tag 갱신", fill="#f3f6fb", stroke=LINE,
           title_size=26, detail_size=20, shadow=False)
    s.card(852, 1270, 420, 143, "GitOps · 배포", "Argo CD가 main 추적 → Helm\nUI는 동일 NLB의 HTTPS", fill="#f3f6fb", stroke=LINE,
           title_size=26, detail_size=20, shadow=False)
    s.card(1289, 1270, 420, 143, "자동 확장", "Metrics Server → HPA(CPU 50%)\nKarpenter → 노드 증설", fill="#f3f6fb", stroke=LINE,
           title_size=26, detail_size=20, shadow=False)
    s.card(1726, 1270, 570, 143, "관측 · 알림", "API/Traefik → Prometheus → Grafana\nAlertmanager → Slack", fill="#f3f6fb", stroke=LINE,
           title_size=26, detail_size=20, shadow=False)

    s.text(46, 1485, "범위: 현재 저장소의 운영 선언 · 실제 AWS 리소스/Pod 배치는 별도 확인 필요 · 앱은 현재 /items CRUD 실습 API", 21, MUTED)
    s.save(
        "platform-network-current.svg",
        "AWS EKS 플랫폼 네트워크 아키텍처 (운영 코드 선언 기준)",
        "Route 53에서 Traefik NLB와 Pod IP로 이어지는 요청 경로, 별도 NAT outbound, 두 AZ의 private EKS, 데이터 계층 및 운영 제어 경로",
    )


def traffic_flow():
    s = SVG(2200, 1670)
    s.text(64, 83, "현재 API 요청 · 제어 흐름 아키텍처", 52, INK, 800)
    s.text(66, 130, "실선: 요청/데이터  ·  점선: Kubernetes 선언 감시와 설정 동기화  ·  운영 코드 선언 기준", 25, MUTED)

    s.pill(1420, 77, 210, "HTTPS 요청", "#e8f0ff", BLUE)
    s.pill(1645, 77, 210, "설정 동기화", "#f1eaff", PURPLE)
    s.pill(1870, 77, 240, "HTTP 308 분기", "#fff0df", ORANGE)

    # Entry path.
    s.card(350, 186, 355, 126, "사용자 / 브라우저", "HTTPS · 허용된 소스 IP", fill="#ffffff", stroke=BLUE,
           title_size=29, detail_size=21)
    s.card(760, 186, 365, 126, "Route 53 A Alias", "api.bidservice.store", fill="#edf3ff", stroke=BLUE,
           title_size=29, detail_size=21)
    s.card(1190, 174, 510, 151, "인터넷 공개 AWS NLB", "80 / 443 · Target type: ip\nReady Traefik Pod IP에 직접 전달", fill="#e7f0ff", stroke=BLUE,
           title_size=29, detail_size=21)
    s.path("M705 249 H757", BLUE, 6)
    s.path("M1125 249 H1187", BLUE, 6)

    # The AWS controller watches the LoadBalancer Service; it is not a traffic hop.
    s.card(75, 423, 430, 135, "AWS Load Balancer Controller", "LoadBalancer Service를 감시\nNLB · IP Target Group 관리", fill="#f1eaff", stroke=PURPLE,
           title_size=27, detail_size=21)
    s.card(75, 590, 430, 137, "traefik Service", "type: LoadBalancer\nReady Pod IP를 엔드포인트로 게시", fill="#f1eaff", stroke=PURPLE,
           title_size=27, detail_size=20)
    s.path("M290 588 V561", PURPLE, 4, dash="10 9")
    s.path("M505 658 H543 V360 H1445 V328", PURPLE, 4, dash="10 9")
    s.text(90, 759, "Service의 ClusterIP는 NLB 요청의 경유지가 아님", 19, PURPLE, 600)

    # Direct NLB to Traefik Pod IP data plane.
    s.rect(617, 462, 999, 261, "#f8f5ff", "#cbb7ef", 24, 2, dash="12 9")
    s.text(646, 501, "Traefik Deployment · replicas 2 · 두 AZ/노드로 분산", 26, PURPLE, 700)
    s.card(660, 543, 385, 122, "Traefik Pod 1", "NLB IP target · TLS 종료", fill="#ffffff", stroke=PURPLE,
           title_size=28, detail_size=21)
    s.card(1177, 543, 385, 122, "Traefik Pod 2", "NLB IP target · TLS 종료", fill="#ffffff", stroke=PURPLE,
           title_size=28, detail_size=21)
    s.path("M1365 325 V397 H595 V520 H855 V540", BLUE, 6)
    s.path("M1510 325 V540", BLUE, 6)

    # Routing declaration watched by Traefik, not AWS LBC.
    s.card(1675, 420, 445, 303, "Ingress + Middleware", "IngressClass: traefik\nHost: api.bidservice.store\nPath: / (Prefix)\nTLS Secret: url-shortener-tls\ncert-manager / Let's Encrypt\nIP allowlist · avg 5/s, burst 15", fill="#f1eaff", stroke=PURPLE,
           title_size=27, detail_size=20)
    s.path("M1672 522 H1620 V518 H1570", PURPLE, 4, dash="10 9")
    s.text(1700, 749, "Traefik이 Ingress/CRD를 읽어 라우팅 설정", 19, PURPLE, 600)

    # HTTPS app path.
    s.card(856, 793, 470, 105, "url-shortener-svc :80", "ClusterIP · selector로 API Pod 선택", fill="#e4f8f1", stroke=GREEN,
           title_size=28, detail_size=20)
    s.path("M852 665 V751 H960 V790", GREEN, 5)
    s.path("M1370 665 V751 H1220 V790", GREEN, 5)
    s.rect(617, 954, 999, 200, "#f0fbf7", "#b5ddd1", 24, 2, dash="12 9")
    s.text(647, 993, "FastAPI Deployment · HPA 2–10 (CPU 50%)", 26, GREEN, 700)
    s.card(661, 1023, 384, 100, "API Pod :8000", "/items CRUD · /metrics", fill="#ffffff", stroke=GREEN,
           title_size=27, detail_size=20)
    s.card(1180, 1023, 383, 100, "API Pod :8000", "Read/Write DB 풀 분리", fill="#ffffff", stroke=GREEN,
           title_size=27, detail_size=20)
    s.path("M970 898 V934 H853 V1020", GREEN, 5)
    s.path("M1210 898 V934 H1370 V1020", GREEN, 5)

    # HTTP is a separate route through Traefik, not ingress-nginx.
    s.card(1680, 835, 440, 275, "HTTP :80 별도 경로", "Traefik → redirect-308 Service\n→ nginx Pod :8080\n→ 308 HTTPS 응답\n(nginx Ingress Controller 아님)", fill="#fff5e9", stroke=ORANGE,
           title_size=27, detail_size=20)
    s.path("M1562 629 H1640 V870 H1677", ORANGE, 5)

    # Read and write paths; AWS chooses the RDS AZ placement.
    s.card(76, 1201, 428, 189, "데이터 접근", "Redis 적중 시 DB 생략\n캐시 미스 → 보통 Replica\n최근 쓰기/캐시 오류 → Primary", fill="#f2f6fb", stroke=LINE,
           title_size=25, detail_size=19, shadow=False)
    s.card(635, 1241, 336, 151, "Redis Pod + PVC", "Cache Aside · standalone\n장애 시 DB 직접 조회", fill="#fff4e7", stroke=ORANGE,
           title_size=26, detail_size=20)
    s.card(990, 1241, 336, 151, "RDS Primary", "쓰기 · 최근 쓰기 후 읽기\n캐시 오류 시 읽기 가능", fill="#fff4e7", stroke=ORANGE,
           title_size=26, detail_size=20)
    s.card(1345, 1241, 336, 151, "Multi-AZ Standby", "자동 장애조치 전용\n일반 읽기 처리 안 함", fill="#fff4e7", stroke=ORANGE,
           title_size=26, detail_size=20)
    s.card(1700, 1241, 336, 151, "RDS Read Replica", "일반 캐시 미스 읽기\nPrimary 비동기 복제", fill="#fff4e7", stroke=ORANGE,
           title_size=26, detail_size=20)
    s.path("M851 1123 V1202 H802 V1238", GREEN, 4)
    s.path("M946 1123 V1180 H1158 V1238", GREEN, 4)
    s.path("M1371 1123 V1198 H1868 V1238", GREEN, 4)
    s.path("M1328 1316 H1342", ORANGE, 3, dash="7 7")
    s.text(1336, 1435, "Standby: 자동 장애조치   ·   Read Replica: Primary에서 비동기 복제", 20, ORANGE, 600, "middle")

    # Side operational context and caveat.
    s.card(76, 951, 428, 190, "확장·관측 (별도 제어 경로)", "Metrics Server → HPA\nKarpenter → 노드 증설\nServiceMonitor → Prometheus\nAlertmanager → Slack", fill="#f2f6fb", stroke=LINE,
           title_size=25, detail_size=19, shadow=False)
    s.rect(61, 1481, 2077, 123, "#eaf0f8", "#d0dbea", 20)
    s.text(88, 1524, "주의", 24, INK, 700)
    s.text(171, 1524, "AWS LBC·Ingress 규칙·HPA·Prometheus는 고객 요청의 경유지가 아닙니다.", 22, MUTED, 500)
    s.text(171, 1565, "RDS 인스턴스의 정확한 AZ, 실제 Pod 수/상태는 런타임에서 확인해야 합니다. 앱은 현재 /items CRUD 실습 API입니다.", 21, MUTED)

    s.save(
        "traffic-flow-current.svg",
        "현재 API 요청 및 제어 흐름 아키텍처 (운영 코드 선언 기준)",
        "사용자 요청은 Route 53과 인터넷 공개 NLB의 IP target을 거쳐 Traefik Pod에 직접 전달되고, Traefik은 Ingress 규칙에 따라 API Service 및 Pod로 보낸다. 제어 흐름과 HTTP 308 경로를 구분한다.",
    )


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    platform_network()
    traffic_flow()
    print(OUT / "platform-network-current.svg")
    print(OUT / "traffic-flow-current.svg")
