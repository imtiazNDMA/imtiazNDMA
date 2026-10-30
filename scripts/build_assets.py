"""Generate the SVG artwork used by the profile README.

The banner shows the last 12 months of weekly contributions, fetched through
the `gh` CLI, so it needs `gh` authenticated (locally) or GH_TOKEN set (in
Actions). Fonts are subset and embedded because GitHub serves SVGs as images,
which cannot load external fonts. Icons are Phosphor (MIT), in scripts/icons.

    python scripts/build_assets.py [--banner-only]   (needs fonttools, brotli, numpy, matplotlib)
"""

from __future__ import annotations

import base64
import io
import json
import math
import re
import subprocess
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from fontTools import subset  # noqa: E402
from fontTools.ttLib import TTFont  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
FONTS = ROOT / "scripts" / "fonts"
ICONS = ROOT / "scripts" / "icons"
OUT = ROOT / "assets"
USER = "imtiazNDMA"

NAME = "Imtiaz Nabi"
ROLE = "AI Engineer, National Disaster Management Authority, Pakistan"
FOCUS = "Machine learning, agentic AI and geospatial analytics for disaster risk reduction"

BG_A, BG_B = "#0B2440", "#123A63"
CARD = "#0F2D4D"
EDGE = "#1E4772"
TILE = "#17406A"
TEXT = "#F3F6F9"
MUTED = "#A9BCD0"
ACCENT = "#8CC8FF"
CHIP_TEXT = "#D5E5F5"

FAMILY = {"semibold": "G6", "medium": "G5", "regular": "G4"}
FONT_FILE = {"semibold": "Geist600", "medium": "Geist500", "regular": "Geist400"}

# ---------------------------------------------------------------- text and fonts

_metrics: dict[str, tuple] = {}


def measure(text: str, key: str, size: float, tracking: float = 0) -> float:
    if key not in _metrics:
        f = TTFont(FONTS / f"{FONT_FILE[key]}.woff2")
        _metrics[key] = (f.getBestCmap(), f["hmtx"].metrics, f["head"].unitsPerEm)
    cmap, hmtx, upm = _metrics[key]
    return sum(hmtx[cmap[ord(c)]][0] for c in text) * size / upm + tracking * max(len(text) - 1, 0)


def wrap(text: str, key: str, size: float, max_width: float) -> list[str]:
    lines, line = [], ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if line and measure(trial, key, size) > max_width:
            lines.append(line)
            line = word
        else:
            line = trial
    return lines + [line]


def font_css(used: dict[str, str]) -> str:
    rules = []
    for key, text in used.items():
        if not text:
            continue
        font = TTFont(FONTS / f"{FONT_FILE[key]}.woff2")
        opts = subset.Options()
        opts.flavor = "woff2"
        opts.name_IDs = []
        sub = subset.Subsetter(opts)
        sub.populate(text="".join(sorted(set(text + " "))))
        sub.subset(font)
        buf = io.BytesIO()
        font.save(buf)
        b64 = base64.b64encode(buf.getvalue()).decode()
        rules.append(f"@font-face{{font-family:{FAMILY[key]};src:url(data:font/woff2;base64,{b64}) format('woff2');}}")
    return "".join(rules)


def icon(name: str, x: float, y: float, size: float, fill: str) -> str:
    svg = (ICONS / f"{name}.svg").read_text(encoding="utf-8")
    inner = re.search(r"<svg[^>]*>(.*)</svg>", svg, re.S).group(1)
    return f'<g transform="translate({x:.1f} {y:.1f}) scale({size / 256:.4f})" fill="{fill}">{inner}</g>'


class Doc:
    def __init__(self, w: int, h: int, title: str):
        self.w, self.h, self.title = w, h, title
        self.defs: list[str] = []
        self.body: list[str] = []
        self.used = {k: "" for k in FAMILY}

    def add(self, *parts: str):
        self.body.extend(parts)

    def text(self, x, y, s, key, size, fill, anchor="start", tracking=0.0):
        self.used[key] += s
        ls = f' letter-spacing="{tracking}"' if tracking else ""
        esc = s.replace("&", "&amp;").replace("<", "&lt;")
        self.body.append(f'<text x="{x:.1f}" y="{y:.1f}" font-family="{FAMILY[key]}" font-size="{size}" '
                         f'fill="{fill}" text-anchor="{anchor}"{ls}>{esc}</text>')

    def save(self, name: str):
        svg = (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.w}" height="{self.h}" '
            f'viewBox="0 0 {self.w} {self.h}" role="img" aria-label="{self.title}">'
            f"<title>{self.title}</title><style>{font_css(self.used)}</style>"
            f'<defs>{"".join(self.defs)}</defs>{"".join(self.body)}</svg>'
        )
        OUT.mkdir(exist_ok=True)
        (OUT / name).write_text(svg, encoding="utf-8")
        print(f"{name:26s} {len(svg) / 1024:6.1f} KB")


# ---------------------------------------------------------------- shared surfaces


def contours(w, h, peaks, seed, levels=18) -> str:
    """Topographic contour lines over a smooth synthetic terrain."""
    rng = np.random.default_rng(seed)
    x, y = np.meshgrid(np.linspace(0, 1, 200), np.linspace(0, 1, 80))
    z = np.zeros_like(x)
    for px, py, amp, sx, sy in peaks:
        z += amp * np.exp(-(((x - px) / sx) ** 2 + ((y - py) / sy) ** 2))
    for _ in range(5):
        fx, fy, ph = rng.uniform(2, 8), rng.uniform(2, 8), rng.uniform(0, 6.28)
        z += 0.06 * np.sin(fx * x + ph) * np.cos(fy * y - ph)
    fig = plt.figure()
    cs = plt.contour(np.linspace(0, w, 200), np.linspace(0, h, 80), z, levels=levels)
    paths = []
    for segs in cs.allsegs:
        for seg in segs:
            if len(seg) > 3:
                seg = seg[:: max(1, len(seg) // 120)]
                paths.append("M" + "L".join(f"{a:.0f} {b:.0f}" for a, b in seg))
    plt.close(fig)
    return f'<path d="{"".join(paths)}" fill="none" stroke="{ACCENT}" stroke-width="1"/>'


def surface(d: Doc, w, h, gradient=True, glow=(1.0, 0.0)):
    """Rounded card background: blue gradient (or flat card) with a soft corner glow."""
    fill = "url(#bg)" if gradient else CARD
    d.defs.append(
        f'<linearGradient id="bg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{BG_A}"/>'
        f'<stop offset="1" stop-color="{BG_B}"/></linearGradient>'
        f'<radialGradient id="glow" cx="{glow[0]}" cy="{glow[1]}" r="0.7">'
        f'<stop offset="0" stop-color="{ACCENT}" stop-opacity="0.16"/>'
        f'<stop offset="1" stop-color="{ACCENT}" stop-opacity="0"/></radialGradient>'
        f'<linearGradient id="fade" x1="0" x2="1"><stop offset="0.3" stop-color="#fff" stop-opacity="0"/>'
        f'<stop offset="0.85" stop-color="#fff" stop-opacity="1"/></linearGradient>'
        f'<mask id="m-fade"><rect width="{w}" height="{h}" fill="url(#fade)"/></mask>'
        f'<clipPath id="clip"><rect width="{w}" height="{h}" rx="18"/></clipPath>'
    )
    d.add(f'<rect width="{w}" height="{h}" rx="18" fill="{fill}"/>',
          f'<rect width="{w}" height="{h}" rx="18" fill="url(#glow)"/>')


def outline(d: Doc, w, h):
    d.add(f'<rect x="0.5" y="0.5" width="{w - 1}" height="{h - 1}" rx="17.5" fill="none" stroke="{EDGE}"/>')


# ---------------------------------------------------------------- banner


def weekly_contributions() -> tuple[int, list[int]]:
    query = ("{user(login:\"%s\"){contributionsCollection{contributionCalendar"
             "{totalContributions weeks{contributionDays{contributionCount}}}}}}" % USER)
    raw = subprocess.run(["gh", "api", "graphql", "-f", f"query={query}"],
                         check=True, capture_output=True, text=True).stdout
    cal = json.loads(raw)["data"]["user"]["contributionsCollection"]["contributionCalendar"]
    return cal["totalContributions"], [sum(d["contributionCount"] for d in w["contributionDays"]) for w in cal["weeks"]]


def banner():
    total, weeks = weekly_contributions()
    W, H, x0 = 1280, 400, 72
    d = Doc(W, H, f"{NAME}. {ROLE}. {total:,} contributions in the last 12 months.")
    surface(d, W, H, glow=(0.85, 0.1))
    terrain = contours(W, H, [(0.8, 0.35, 1, 0.16, 0.35), (0.6, 0.9, 0.6, 0.12, 0.25),
                              (0.97, 0.05, 0.5, 0.1, 0.2)], seed=7, levels=20)
    d.add(f'<g clip-path="url(#clip)"><g mask="url(#m-fade)" opacity="0.28">{terrain}</g></g>')
    d.text(x0, 146, NAME, "semibold", 80, TEXT, tracking=-2.4)
    d.text(x0, 198, ROLE, "regular", 27, TEXT)
    d.text(x0, 236, FOCUS, "regular", 22, MUTED)

    # Weekly bars on a square-root scale, so quiet weeks stay visible next to busy ones.
    base, tallest, gap = 330, 52, 4
    bw = (W - 2 * x0 - gap * (len(weeks) - 1)) / len(weeks)
    peak = math.sqrt(max(weeks) or 1)
    for i, n in enumerate(weeks):
        h = max(2.5, tallest * math.sqrt(n) / peak)
        op = 1 if i == len(weeks) - 1 else 0.5
        d.add(f'<rect x="{x0 + i * (bw + gap):.1f}" y="{base - h:.1f}" width="{bw:.1f}" height="{h:.1f}" '
              f'rx="1.5" fill="{ACCENT}" fill-opacity="{op}"/>')
    d.text(x0, 362, "Contributions, last 12 months", "regular", 18, MUTED)
    d.text(W - x0, 362, f"{total:,} total", "medium", 18, TEXT, anchor="end")
    outline(d, W, H)
    d.save("banner.svg")


# ---------------------------------------------------------------- project cards


def card(name, w, h, icon_name, title, body, stack, title_size=30, text_width=None, terrain=None):
    d = Doc(w, h, f"{title}. {body} Built with {', '.join(stack)}.")
    surface(d, w, h, gradient=False)
    if terrain:
        d.add(f'<g clip-path="url(#clip)"><g mask="url(#m-fade)" opacity="0.22">{terrain}</g></g>')
    pad = 40
    d.add(f'<rect x="{pad}" y="{pad}" width="64" height="64" rx="16" fill="{TILE}"/>',
          icon(icon_name, pad + 14, pad + 14, 36, ACCENT),
          icon("arrow-up-right", w - pad - 26, pad + 2, 26, MUTED))
    y = pad + 64 + 22 + title_size
    for line in wrap(title, "semibold", title_size, text_width or w - 2 * pad):
        d.text(pad, y, line, "semibold", title_size, TEXT, tracking=-0.6)
        y += title_size * 1.15
    y += 6
    for line in wrap(body, "regular", 20, text_width or w - 2 * pad):
        d.text(pad, y, line, "regular", 20, MUTED)
        y += 30
    cx, cy = pad, h - pad - 36
    for item in stack:
        cw = measure(item, "medium", 16) + 32
        d.add(f'<rect x="{cx:.1f}" y="{cy}" width="{cw:.1f}" height="36" rx="18" fill="{TILE}"/>')
        d.text(cx + cw / 2, cy + 23.5, item, "medium", 16, CHIP_TEXT, anchor="middle")
        cx += cw + 10
    outline(d, w, h)
    d.save(name)


def cards():
    card("card-ews.svg", 1280, 370, "siren", "Pakistan Early Warning System",
         "District-level weather monitoring with lifecycle-managed hazard alerts. Forecasts are screened "
         "against thresholds, and the evidence behind every alert is kept.",
         ["FastAPI", "PostgreSQL", "React", "MapLibre"], title_size=36, text_width=700,
         terrain=contours(1280, 370, [(0.85, 0.45, 1, 0.14, 0.4), (0.66, 0.95, 0.5, 0.1, 0.3)], seed=3))
    half = dict(w=632, h=360)
    card("card-quake.svg", icon_name="pulse", title="Earthquake Intensity Platform",
         body="Site-amplified MMI intensity maps from an event's magnitude, depth and location, "
              "across Pakistan and its neighbours.",
         stack=["NumPy", "Uvicorn", "Leaflet"], **half)
    card("card-cloudburst.svg", icon_name="cloud-rain", title="Cloudburst Early Warning",
         body="Nowcasting that gates cloudburst risk on convective predictors: CAPE, precipitable "
              "water, lifting index and wind shear.",
         stack=["Python", "uv", "Nowcast CLI"], **half)
    card("card-rag.svg", icon_name="chats-circle", title="ChatbotARK",
         body="A disaster-management assistant with cited RAG answers, local Llama 3.1 and "
              "Whisper voice input.",
         stack=["Ollama", "ChromaDB", "Whisper"], **half)
    card("card-simex.svg", icon_name="users-three", title="SimEx AI",
         body="An AI assistant for NDMA simulation exercises, the drills that test coordination "
              "across ten disaster-response wings.",
         stack=["FastAPI", "LangChain", "Ollama"], **half)


# ---------------------------------------------------------------- small pieces


def icon_tile(name):
    d = Doc(88, 88, "")
    d.add(f'<rect width="88" height="88" rx="22" fill="{CARD}"/>',
          f'<rect x="0.5" y="0.5" width="87" height="87" rx="21.5" fill="none" stroke="{EDGE}"/>',
          icon(name, 22, 22, 44, ACCENT))
    d.save(f"icon-{name}.svg")


def button(slug, icon_name, label):
    size = 19
    w = int(measure(label, "medium", size)) + 24 + 26 + 12 + 28
    d = Doc(w, 56, label)
    d.add(f'<rect width="{w}" height="56" rx="28" fill="{CARD}"/>',
          f'<rect x="0.5" y="0.5" width="{w - 1}" height="55" rx="27.5" fill="none" stroke="{EDGE}"/>',
          icon(icon_name, 24, 15, 26, ACCENT))
    d.text(24 + 26 + 12, 35, label, "medium", size, TEXT)
    d.save(f"btn-{slug}.svg")


def footer():
    W, H = 1280, 150
    d = Doc(W, H, "Coffee, code, crisis, commit.")
    surface(d, W, H, glow=(0.5, 1.2))
    terrain = contours(W, H, [(0.5, 1.2, 1.2, 0.45, 0.6), (0.1, 0.8, 0.4, 0.12, 0.3)], seed=21, levels=12)
    d.add(f'<g clip-path="url(#clip)" opacity="0.18">{terrain}</g>')
    words, size, gap = ["Coffee", "Code", "Crisis", "Commit"], 30, 70
    widths = [measure(w_, "medium", size) for w_ in words]
    x = (W - sum(widths) - gap * 3) / 2
    for i, (word, wid) in enumerate(zip(words, widths)):
        d.text(x, 86, word, "medium", size, TEXT)
        x += wid
        if i < 3:
            d.text(x + gap / 2, 85, "→", "medium", size, ACCENT, anchor="middle")
            x += gap
    outline(d, W, H)
    d.save("footer.svg")


if __name__ == "__main__":
    import sys

    banner()
    if "--banner-only" in sys.argv:  # the weekly workflow only refreshes the contribution data
        sys.exit()
    cards()
    for n in ["robot", "globe-hemisphere-east", "bell-ringing", "files", "sparkle", "brain"]:
        icon_tile(n)
    for slug, ic, label in [("linkedin", "linkedin-logo", "LinkedIn"), ("youtube", "youtube-logo", "YouTube"),
                            ("substack", "newspaper", "Substack"), ("github", "github-logo", "GitHub / Imtiaz08")]:
        button(slug, ic, label)
    footer()
