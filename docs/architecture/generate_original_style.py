"""Recreate the supplied AWS poster layout using the repository's declared topology.

The SVG is intentionally editable.  Render it to PNG with Sharp or any SVG renderer.
"""

from __future__ import annotations

from generate_diagrams import SVG, OUT


NAVY = "#142033"
ORANGE = "#db7b22"
ORANGE_DARK = "#af5c16"
GREEN = "#298d4b"
GREEN_DARK = "#176c39"
BLUE = "#2374a8"
MUTED = "#707988"
PURPLE = "#7658b5"

s = SVG(2400, 1350)
s.rect(0, 0, 2400, 1350, "#ffffff", r=0)
s.parts.append("""<defs>
<marker id="arrow-poster-green" markerWidth="7" markerHeight="7" refX="6" refY="3.5" orient="auto"><path d="M0 0 L7 3.5 L0 7 Z" fill="#298d4b"/></marker>
<marker id="arrow-poster-orange" markerWidth="7" markerHeight="7" refX="6" refY="3.5" orient="auto"><path d="M0 0 L7 3.5 L0 7 Z" fill="#db7b22"/></marker>
<marker id="arrow-poster-purple" markerWidth="7" markerHeight="7" refX="6" refY="3.5" orient="auto"><path d="M0 0 L7 3.5 L0 7 Z" fill="#7658b5"/></marker>
<marker id="arrow-poster-navy" markerWidth="7" markerHeight="7" refX="6" refY="3.5" orient="auto"><path d="M0 0 L7 3.5 L0 7 Z" fill="#142033"/></marker>
<marker id="arrow-poster-muted" markerWidth="7" markerHeight="7" refX="6" refY="3.5" orient="auto"><path d="M0 0 L7 3.5 L0 7 Z" fill="#707988"/></marker>
</defs>""")
s._marker_name = lambda color: {
    GREEN: "poster-green", ORANGE: "poster-orange", PURPLE: "poster-purple",
    NAVY: "poster-navy", MUTED: "poster-muted",
}.get(color, "poster-muted")


def circle(x, y, r, fill, stroke="none", sw=2):
    s.parts.append(
        f'<circle cx="{x}" cy="{y}" r="{r}" fill="{fill}" '
        f'stroke="{stroke}" stroke-width="{sw}"/>'
    )


def label(x, y, title, subtitle="", *, size=25, color=NAVY, anchor="middle"):
    s.text(x, y, title, size, color, 650, anchor)
    if subtitle:
        s.text(x, y + 27, subtitle, 19, color, 400, anchor)


def icon(x, y, r, fill, glyph, glyph_size=29):
    circle(x, y, r, fill, "#ffffff", 3)
    s.text(x, y + glyph_size * .32, glyph, glyph_size, "#ffffff", 750, "middle")


def tiny_box(x, y, w, h, title, subtitle="", *, fill="#ffffff", stroke=BLUE,
             title_size=22, subtitle_size=17):
    s.rect(x, y, w, h, fill, stroke, 3, 2)
    s.text(x + w / 2, y + 29, title, title_size, NAVY, 700, "middle")
    if subtitle:
        s.text(x + w / 2, y + 54, subtitle, subtitle_size, NAVY, 450, "middle")


# Poster title and infrastructure boundaries follow the supplied composition.
s.text(60, 112, "AWS EKS 기반 URL Shortener 플랫폼 네트워크 아키텍처", 68, NAVY, 800)
s.rect(530, 160, 1815, 940, "#ffffff", NAVY, 0, 3)
s.rect(530, 160, 61, 61, NAVY, NAVY, 0, 1)
s.text(560, 198, "aws", 25, "#ffffff", 700, "middle")
s.text(610, 200, "AWS Cloud - ap-northeast-2", 28, NAVY)
s.rect(690, 215, 1610, 850, "#fffdfa", ORANGE, 0, 3)
s.rect(690, 215, 62, 58, ORANGE, ORANGE, 0, 1)
s.text(721, 253, "V", 28, "#ffffff", 800, "middle")
s.text(770, 253, "VPC 10.0.0.0/16", 29, ORANGE_DARK)

# Dashed AZs and the two original public/private subnet bands.
for x, az, pub, priv in (
    (735, "AZ-1: ap-northeast-2a", "Public subnet 10.0.0.0/24", "Private subnet 10.0.10.0/24"),
    (1515, "AZ-2: ap-northeast-2c", "Public subnet 10.0.1.0/24", "Private subnet 10.0.11.0/24"),
):
    s.rect(x, 278, 735, 758, "none", "#d69a5d", 0, 2, dash="12 9")
    s.text(x + 367, 306, az, 26, ORANGE_DARK, 500, "middle")
    s.rect(x + 13, 320, 709, 183, "#eaf3e9", "none", 0)
    s.rect(x + 13, 523, 709, 501, "#e5f2fb", "none", 0)
    s.rect(x + 26, 339, 30, 30, GREEN_DARK, GREEN_DARK, 0)
    s.text(x + 41, 362, "○", 21, "#ffffff", 700, "middle")
    s.text(x + 70, 364, pub, 24, GREEN_DARK, 600)
    s.rect(x + 26, 540, 30, 30, BLUE, BLUE, 0)
    s.text(x + 41, 563, "○", 21, "#ffffff", 700, "middle")
    s.text(x + 70, 565, priv, 24, BLUE, 600)

icon(1490, 218, 39, ORANGE, "IGW", 18)
s.text(1490, 280, "Internet gateway", 20, NAVY, 500, "middle")

# Public entry. The DNS path crosses above NAT; NAT is outbound only.
s.path("M184 431 H531", GREEN, 6)
s.path("M619 430 H670 V270 H1391 V431 H1442", GREEN, 6)
s.path("M1200 583 V512 H937 V483", ORANGE, 4, dash="9 7")

# NLB targets two ready Traefik Pod IPs directly. No Service ClusterIP hop.
s.path("M1490 476 V584 H1064 V664", GREEN, 6)
s.path("M1490 584 H1846 V664", GREEN, 6)

# Incoming user icon (globe + person) and access policy annotation.
circle(106, 410, 48, "#ffffff", NAVY, 4)
s.path("M60 410 H151 M106 362 V459 M72 386 H139 M72 434 H139", NAVY, 3, marker=False)
circle(152, 424, 17, "#ffffff", NAVY, 3)
s.path("M124 471 C124 446 180 446 180 471", NAVY, 3, marker=False)
label(114, 509, "인터넷 사용자", size=26)
s.rect(255, 275, 225, 134, "#ffffff", NAVY, 0, 2, dash="3 6")
s.text(367, 312, "HTTPS API", 22, NAVY, 600, "middle")
s.text(367, 343, "허용 IP만", 25, NAVY, 700, "middle")
s.text(367, 374, "Traefik 정책", 19, GREEN_DARK, 600, "middle")
s.text(367, 397, "(트래픽 홉 아님)", 16, MUTED, 450, "middle")
icon(575, 430, 46, ORANGE, "53", 31)
label(575, 518, "Route 53", "api.bidservice.store", size=25)

icon(937, 433, 47, ORANGE, "NAT", 22)
label(937, 514, "NAT Gateway ×1", "사설 서브넷 outbound", size=22)
icon(1490, 431, 45, ORANGE, "NLB", 22)
s.text(1570, 433, "Traefik NLB · 80/443", 23, NAVY, 650)
s.text(1570, 461, "인터넷 공개 · IP target", 19, NAVY, 400)

# Private EKS nodes and their paths. Control paths are dashed; requests are solid.
s.rect(969, 607, 360, 307, "#ffffff", BLUE, 0, 3)
s.rect(1757, 607, 360, 307, "#ffffff", BLUE, 0, 3)
for x in (969, 1757):
    s.text(x + 18, 640, "EKS worker node", 23, BLUE, 550)

# Traefik to Ingress policy / ClusterIP service; API Pod selection after Service.
s.path("M1110 707 H1350 V782 H1370", GREEN, 5)
s.path("M1800 707 H1710 V782 H1690", GREEN, 5)
s.path("M1370 823 H1329 V852 H1113", GREEN, 5)
s.path("M1690 823 H1730 V852 H1800", GREEN, 5)

# HTTP :80 goes through a separate redirect Service and an nginx Pod, not nginx Ingress.
s.path("M1016 707 H832 V782", ORANGE, 4)
tiny_box(767, 787, 143, 56, "redirect-308", fill="#fff6ec", stroke=ORANGE, title_size=16)
s.path("M839 844 V863", ORANGE, 4)
tiny_box(767, 868, 143, 63, "nginx Pod", ":8080 → 308", fill="#fff6ec", stroke=ORANGE,
         title_size=18, subtitle_size=16)
s.text(839, 949, "HTTP :80 분기", 16, ORANGE_DARK, 600, "middle")

# Ingress CRD applies to both Traefik Pods; it is not a traffic hop.
s.rect(1377, 614, 305, 118, "#fffdfc", PURPLE, 0, 2)
s.text(1529, 642, "Ingress + Middleware", 21, PURPLE, 700, "middle")
s.text(1529, 671, "IP AllowList · rate limit", 17, PURPLE, 550, "middle")
s.text(1529, 695, "avg 5/s · burst 15", 17, PURPLE, 550, "middle")
s.text(1529, 717, "TLS: cert-manager", 16, PURPLE, 450, "middle")
s.path("M1377 672 H1337 V658 H1110", PURPLE, 3, dash="8 7")
s.path("M1682 672 H1728 V658 H1800", PURPLE, 3, dash="8 7")

icon(1064, 707, 43, GREEN, "T", 31)
s.text(1121, 700, "Traefik Ingress", 22, NAVY, 700)
s.text(1121, 728, "Pod 1 · Ready", 17, NAVY)
icon(1846, 707, 43, GREEN, "T", 31)
s.text(1903, 700, "Traefik Ingress", 22, NAVY, 700)
s.text(1903, 728, "Pod 2 · Ready", 17, NAVY)

tiny_box(1370, 766, 320, 65, "url-shortener-svc :80", "ClusterIP · API Pod 선택",
         fill="#e9f6ee", stroke=GREEN, title_size=21, subtitle_size=16)

icon(1064, 852, 42, BLUE, "API", 20)
s.text(1121, 843, "FastAPI Pod :8000", 21, NAVY, 700)
s.text(1121, 872, "HPA 2–10 · CPU 50%", 17, NAVY)
icon(1846, 852, 42, BLUE, "API", 20)
s.text(1903, 843, "FastAPI Pod :8000", 21, NAVY, 700)
s.text(1903, 872, "AZ 분산 선호", 17, NAVY)

# One logical data bus indicates either API Pod may access any live store.
s.path("M1111 914 V942", MUTED, 3, marker=False)
s.path("M1893 914 V942", MUTED, 3, marker=False)
s.path("M950 942 H2160", MUTED, 3, marker=False)
s.path("M950 942 V966", MUTED, 3)
s.path("M1850 942 V966", MUTED, 3)
s.path("M2150 942 V966", MUTED, 3)
icon(950, 984, 26, ORANGE, "DB", 16)
s.text(950, 1023, "RDS Primary", 17, NAVY, 700, "middle")
icon(1580, 984, 26, ORANGE, "HA", 15)
s.text(1580, 1023, "Multi-AZ Standby", 17, NAVY, 700, "middle")
icon(1850, 984, 26, ORANGE, "RR", 15)
s.text(1850, 1023, "Read Replica · async", 17, NAVY, 700, "middle")
icon(2150, 984, 26, "#c8472d", "R", 21)
s.text(2150, 1023, "Redis Pod + PVC", 17, NAVY, 700, "middle")
s.path("M978 984 H1548", MUTED, 3, dash="7 7")
s.text(1260, 976, "HA replication", 15, MUTED, 600, "middle")
s.text(1480, 1054, "데이터 스토어 위치는 논리 표시 · RDS AZ는 코드에서 고정하지 않음 · Primary→Replica 비동기 · Standby는 조회 대상 아님", 17, MUTED, 500, "middle")

# Original poster's lower-left CI/CD block, disconnected from customer traffic.
icon(149, 884, 48, NAVY, "GH", 22)
s.text(149, 964, "GitHub Actions", 22, NAVY, 700, "middle")
s.text(149, 993, "CI/CD · Git tag 갱신", 18, NAVY, 500, "middle")
s.path("M200 884 H317", NAVY, 4)
s.text(261, 873, "image push", 16, NAVY, 500, "middle")
s.rect(320, 839, 90, 90, ORANGE, ORANGE, 0, 2)
s.text(365, 895, "ECR", 27, "#ffffff", 750, "middle")
s.text(365, 964, "AWS ECR", 22, NAVY, 650, "middle")
s.text(365, 992, "image registry", 18, NAVY, 450, "middle")

# Operations row retains the original icon/box rhythm. It uses the same NLB.
s.rect(52, 1135, 720, 174, "#ffffff", NAVY, 0, 2)
s.path("M74 1168 H150", GREEN, 5)
s.text(166, 1175, "실선: 실제 요청/데이터 경로", 20, NAVY)
s.path("M74 1209 H150", PURPLE, 4, dash="8 7")
s.text(166, 1216, "점선: 설정 동기화/정책", 20, NAVY)
s.rect(74, 1242, 39, 27, ORANGE, ORANGE, 0)
s.text(166, 1263, "주황: AWS 네트워크/데이터", 20, NAVY)
s.rect(468, 1242, 39, 27, BLUE, BLUE, 0)
s.text(522, 1263, "파랑: Kubernetes", 20, NAVY)
s.text(75, 1294, "코드 선언 기준 · 실제 실행 상태는 별도 확인", 17, MUTED)

icon(904, 1190, 33, "#2c86b9", "TLS", 17)
s.text(904, 1242, "cert-manager", 19, NAVY, 650, "middle")
s.text(904, 1268, "Let's Encrypt → Secret", 15, NAVY, 450, "middle")
icon(1038, 1190, 34, ORANGE, "NLB", 15)
s.text(1038, 1242, "상단 동일 NLB", 17, NAVY, 600, "middle")
s.path("M1074 1190 H1108", MUTED, 3)
tiny_box(1112, 1154, 115, 75, "Traefik", "TLS", fill="#ffffff", stroke=GREEN,
         title_size=19, subtitle_size=17)
s.path("M1230 1190 H1251", MUTED, 3)
tiny_box(1255, 1154, 140, 75, "Argo CD", "UI / Application", fill="#ffffff", stroke=BLUE,
         title_size=21, subtitle_size=15)
s.text(1325, 1254, "argocd.bidservice.store\nbackend: argocd-server:80", 13, MUTED, 450, "middle")
s.path("M1448 1190 H1399", PURPLE, 3, dash="8 7")
icon(1483, 1190, 32, ORANGE, "Git", 16)
s.text(1483, 1242, "GitOps · Helm", 18, NAVY, 650, "middle")
s.text(1483, 1268, "Argo CD main 추적", 15, NAVY, 450, "middle")

s.path("M1638 1155 V1120 H1916 V1155", MUTED, 3)
icon(1638, 1190, 34, ORANGE, "P", 26)
s.text(1638, 1242, "Prometheus", 18, NAVY, 650, "middle")
s.text(1638, 1268, "API/Traefik metrics", 14, NAVY, 450, "middle")
s.path("M1720 1190 H1674", MUTED, 3)
icon(1755, 1190, 34, ORANGE, "G", 25)
s.text(1755, 1242, "Grafana 조회", 18, NAVY, 650, "middle")
icon(1916, 1190, 34, ORANGE, "A", 25)
s.text(1916, 1242, "Alertmanager", 18, NAVY, 650, "middle")
s.path("M1952 1190 H1996", MUTED, 3)
icon(2030, 1190, 34, "#7955b3", "S", 24)
s.text(2030, 1242, "Slack 알림", 18, NAVY, 650, "middle")
icon(2190, 1190, 34, ORANGE, "K", 25)
s.text(2190, 1242, "Karpenter", 18, NAVY, 650, "middle")
s.text(2190, 1268, "노드 확장", 15, NAVY, 450, "middle")

s.text(802, 1320, "운영 선언: Traefik 2 Pods · API HPA 2–10 · Redis Cache Aside · Primary 쓰기/최근 읽기 · Replica 일반 읽기 · 현재 API는 /items CRUD", 18, MUTED)
s.save(
    "platform-network-original-style.svg",
    "AWS EKS URL Shortener platform network architecture — corrected original style",
    "Original poster composition corrected to the repository's declared Traefik NLB, private EKS, redirect, data, CI/CD and observability flows.",
)
print(OUT / "platform-network-original-style.svg")
