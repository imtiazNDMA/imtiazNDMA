"""Generate assets/banner.svg: name, role, and the last 12 months of contributions.

Contribution data comes from the GitHub GraphQL API through the `gh` CLI, so
it needs `gh` authenticated (locally) or GH_TOKEN set (in Actions). Fonts are
subset and embedded because GitHub serves SVGs as images, which cannot load
external fonts.

    python scripts/build_banner.py      (needs fonttools and brotli)
"""

from __future__ import annotations

import base64
import io
import json
import math
import re
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path

from fontTools import subset
from fontTools.ttLib import TTFont

ROOT = Path(__file__).resolve().parent.parent
FONTS = ROOT / "scripts" / "fonts"
OUT = ROOT / "assets" / "banner.svg"
USER = "imtiazNDMA"

NAME = "Imtiaz Nabi"
ROLE = "AI Engineer, National Disaster Management Authority, Pakistan"
FOCUS = "Machine learning, agentic AI and geospatial analytics for disaster risk reduction"

BG = "#0E2A47"
TEXT = "#F3F6F9"
MUTED = "#A9BCD0"
BAR = "#5B86B3"
BAR_LATEST = "#C9DAEC"

FONT_SOURCES = {"semibold": ("Geist600", "Geist:wght@600"), "regular": ("Geist400", "Geist:wght@400")}
FAMILY = {"semibold": "GS", "regular": "GR"}


def fetch_fonts():
    ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130 Safari/537.36"
    text = "".join(chr(c) for c in range(32, 127))
    FONTS.mkdir(parents=True, exist_ok=True)
    for name, query in FONT_SOURCES.values():
        path = FONTS / f"{name}.woff2"
        if path.exists():
            continue
        url = "https://fonts.googleapis.com/css2?family=" + urllib.parse.quote(query)
        url += "&text=" + urllib.parse.quote(text)
        css = urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": ua})).read().decode()
        src = re.search(r"url\((.*?)\)", css).group(1)
        path.write_bytes(urllib.request.urlopen(urllib.request.Request(src, headers={"User-Agent": ua})).read())


def measure(text: str, key: str, size: float) -> float:
    font = TTFont(FONTS / f"{FONT_SOURCES[key][0]}.woff2")
    cmap, hmtx, upm = font.getBestCmap(), font["hmtx"].metrics, font["head"].unitsPerEm
    return sum(hmtx[cmap[ord(c)]][0] for c in text) * size / upm


def font_css(used: dict[str, str]) -> str:
    rules = []
    for key, text in used.items():
        font = TTFont(FONTS / f"{FONT_SOURCES[key][0]}.woff2")
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


def weekly_contributions() -> tuple[int, list[int]]:
    query = ("{user(login:\"%s\"){contributionsCollection{contributionCalendar"
             "{totalContributions weeks{contributionDays{contributionCount}}}}}}" % USER)
    raw = subprocess.run(["gh", "api", "graphql", "-f", f"query={query}"],
                         check=True, capture_output=True, text=True).stdout
    cal = json.loads(raw)["data"]["user"]["contributionsCollection"]["contributionCalendar"]
    weeks = [sum(d["contributionCount"] for d in w["contributionDays"]) for w in cal["weeks"]]
    return cal["totalContributions"], weeks


def build():
    fetch_fonts()
    total, weeks = weekly_contributions()
    W, H, x0 = 1280, 340, 64
    used = {k: "" for k in FAMILY}
    body = []

    def text(x, y, s, key, size, fill, anchor="start", tracking=0.0):
        used[key] += s
        ls = f' letter-spacing="{tracking}"' if tracking else ""
        body.append(f'<text x="{x:.1f}" y="{y:.1f}" font-family="{FAMILY[key]}" font-size="{size}" '
                    f'fill="{fill}" text-anchor="{anchor}"{ls}>{s}</text>')

    text(x0, 112, NAME, "semibold", 64, TEXT, tracking=-1.6)
    text(x0, 160, ROLE, "regular", 24, TEXT)
    text(x0, 194, FOCUS, "regular", 20, MUTED)

    # Weekly bars on a square-root scale, so quiet weeks stay visible next to busy ones.
    base, tallest, gap = 296, 56, 4
    bw = (W - 2 * x0 - gap * (len(weeks) - 1)) / len(weeks)
    peak = math.sqrt(max(weeks) or 1)
    for i, n in enumerate(weeks):
        h = max(2, tallest * math.sqrt(n) / peak)
        fill = BAR_LATEST if i == len(weeks) - 1 else BAR
        body.append(f'<rect x="{x0 + i * (bw + gap):.1f}" y="{base - h:.1f}" width="{bw:.1f}" '
                    f'height="{h:.1f}" rx="1.5" fill="{fill}"/>')
    text(x0, 322, "Contributions, last 12 months", "regular", 18, MUTED)
    text(W - x0, 322, f"{total:,} total", "regular", 18, MUTED, anchor="end")

    title = f"{NAME}. {ROLE}. {total:,} contributions in the last 12 months."
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
        f'role="img" aria-label="{title}"><title>{title}</title><style>{font_css(used)}</style>'
        f'<rect width="{W}" height="{H}" rx="16" fill="{BG}"/>{"".join(body)}</svg>'
    )
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(svg, encoding="utf-8")
    print(f"{OUT.name}: {len(svg) / 1024:.1f} KB, {total} contributions over {len(weeks)} weeks")


if __name__ == "__main__":
    build()
