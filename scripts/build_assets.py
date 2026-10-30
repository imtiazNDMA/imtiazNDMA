"""Generate the manga-page SVG artwork used by the profile README.

GitHub serves SVGs as images, which cannot load external fonts, so every file
embeds its own subset of the fonts it draws with. If you add copy containing
characters the cached fonts lack, they are re-downloaded from Google Fonts.

    python scripts/build_assets.py      (needs fonttools and brotli)
"""

from __future__ import annotations

import base64
import io
import math
import random
import re
import urllib.parse
import urllib.request
from pathlib import Path

from fontTools import subset
from fontTools.ttLib import TTFont

ROOT = Path(__file__).resolve().parent.parent
FONTS = ROOT / "scripts" / "fonts"
OUT = ROOT / "assets"

PAPER = "#F6F6F1"
INK = "#151515"
GREY = "#5E5E5A"
RED = "#D62828"  # the one spot colour, as on a manga colour page

FONT_SOURCES = {
    "display": ("DelaGothicOne", "Dela Gothic One"),
    "body": ("ZenKaku500", "Zen Kaku Gothic New:wght@500"),
    "bold": ("ZenKaku900", "Zen Kaku Gothic New:wght@900"),
}
FAMILY = {"display": "DG", "body": "ZK", "bold": "ZB"}

# ---------------------------------------------------------------- fonts

_fonts: dict[str, TTFont] = {}


def _font(key: str) -> TTFont:
    if key not in _fonts:
        _fonts[key] = TTFont(FONTS / f"{FONT_SOURCES[key][0]}.woff2")
    return _fonts[key]


def ensure_fonts(chars: str):
    """Re-download the font subsets when copy needs glyphs they lack."""
    missing = any(
        ord(c) not in _font(k).getBestCmap() for k in FONT_SOURCES for c in set(chars) if c != "\n"
    )
    if not missing:
        return
    ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130 Safari/537.36"
    text = "".join(sorted(set(chars) | {chr(c) for c in range(32, 127)}))
    for key, (name, query) in FONT_SOURCES.items():
        url = "https://fonts.googleapis.com/css2?family=" + urllib.parse.quote(query)
        url += "&text=" + urllib.parse.quote(text)
        css = urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": ua})).read().decode()
        src = re.search(r"url\((.*?)\)", css).group(1)
        data = urllib.request.urlopen(urllib.request.Request(src, headers={"User-Agent": ua})).read()
        (FONTS / f"{name}.woff2").write_bytes(data)
        _fonts.pop(key, None)


def measure(text: str, key: str, size: float, tracking: float = 0) -> float:
    font = _font(key)
    cmap, hmtx, upm = font.getBestCmap(), font["hmtx"].metrics, font["head"].unitsPerEm
    width = sum(hmtx[cmap[ord(c)]][0] for c in text) * size / upm
    return width + tracking * size * max(len(text) - 1, 0)


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
        font = TTFont(FONTS / f"{FONT_SOURCES[key][0]}.woff2")
        opts = subset.Options()
        opts.flavor = "woff2"
        opts.name_IDs = []
        opts.notdef_outline = False
        sub = subset.Subsetter(opts)
        sub.populate(text="".join(sorted(set(text + " "))))
        sub.subset(font)
        buf = io.BytesIO()
        font.save(buf)
        b64 = base64.b64encode(buf.getvalue()).decode()
        rules.append(f"@font-face{{font-family:{FAMILY[key]};src:url(data:font/woff2;base64,{b64}) format('woff2');}}")
    return "".join(rules)


# ---------------------------------------------------------------- svg builder


class Doc:
    def __init__(self, w: int, h: int, title: str):
        self.w, self.h, self.title = w, h, title
        self.defs: list[str] = []
        self.body: list[str] = []
        self.used = {k: "" for k in FAMILY}

    def add(self, *parts: str):
        self.body.extend(parts)

    def text(self, x, y, s, key, size, fill=INK, anchor="start", extra=""):
        self.used[key] += s
        esc = s.replace("&", "&amp;").replace("<", "&lt;")
        self.body.append(
            f'<text x="{x:.1f}" y="{y:.1f}" font-family="{FAMILY[key]}" font-size="{size}" '
            f'fill="{fill}" text-anchor="{anchor}"{extra}>{esc}</text>'
        )

    def sfx(self, x, y, s, size, fill=INK, rotate=0, outline=PAPER):
        """Sound-effect lettering: heavy type with a keyline that cuts through the art."""
        rot = f' transform="rotate({rotate} {x:.1f} {y:.1f})"' if rotate else ""
        extra = (f' stroke="{outline}" stroke-width="{size * 0.16:.1f}" stroke-linejoin="round" '
                 f'paint-order="stroke"{rot}')
        self.text(x, y, s, "display", size, fill, anchor="middle", extra=extra)

    def render(self) -> str:
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.w}" height="{self.h}" '
            f'viewBox="0 0 {self.w} {self.h}" role="img" aria-label="{self.title}">'
            f"<title>{self.title}</title><style>{font_css(self.used)}</style>"
            f'<defs>{"".join(self.defs)}</defs>{"".join(self.body)}</svg>'
        )

    def save(self, name: str):
        OUT.mkdir(exist_ok=True)
        svg = self.render()
        (OUT / name).write_text(svg, encoding="utf-8")
        print(f"{name:24s} {len(svg) / 1024:6.1f} KB")


# ---------------------------------------------------------------- manga devices


def pts(points) -> str:
    return " ".join(f"{x:.1f},{y:.1f}" for x, y in points)


def halftone(x0, y0, w, h, spacing, radius) -> str:
    """Screentone: a 45-degree dot grid whose dot size follows radius(x, y).

    Dots are zero-length round-capped strokes, bucketed by size, which keeps
    a dense tone to a few compact paths.
    """
    buckets: dict[float, list[str]] = {}
    row, y = 0, y0
    while y < y0 + h + spacing:
        x = x0 + (spacing / 2 if row % 2 else 0)
        while x < x0 + w + spacing:
            r = round(radius(x, y) * 4) / 4
            if r > 0.35:
                buckets.setdefault(r, []).append(f"M{x:.0f} {y:.0f}h0")
            x += spacing
        y += spacing * 0.5
        row += 1
    return "".join(
        f'<path d="{"".join(d)}" stroke="{INK}" stroke-width="{2 * r}" stroke-linecap="round"/>'
        for r, d in buckets.items()
    )


def focus_lines(cx, cy, rx, ry, n, seed, reach=1600, width=(1.2, 5.0), jitter=0.45) -> str:
    """Shuchusen: ink wedges converging on (cx, cy), stopping short of an ellipse."""
    rnd = random.Random(seed)
    wedges = []
    for i in range(n):
        a = (i + rnd.uniform(-0.4, 0.4)) * 2 * math.pi / n
        stop = 1 + rnd.uniform(0, jitter)
        tip = (cx + rx * stop * math.cos(a), cy + ry * stop * math.sin(a))
        half = rnd.uniform(*width) / reach
        b1 = (cx + reach * math.cos(a - half), cy + reach * math.sin(a - half))
        b2 = (cx + reach * math.cos(a + half), cy + reach * math.sin(a + half))
        wedges.append(f"M{b1[0]:.1f} {b1[1]:.1f}L{tip[0]:.1f} {tip[1]:.1f}L{b2[0]:.1f} {b2[1]:.1f}Z")
    return f'<path d="{"".join(wedges)}" fill="{INK}"/>'


def speed_lines(x0, y0, w, h, n, seed, angle=0.0, length=(120, 420), weight=(1, 3.2)) -> str:
    rnd = random.Random(seed)
    ca, sa = math.cos(math.radians(angle)), math.sin(math.radians(angle))
    out = []
    for _ in range(n):
        x, y, L = rnd.uniform(x0, x0 + w), rnd.uniform(y0, y0 + h), rnd.uniform(*length)
        out.append(f'<path d="M{x:.1f} {y:.1f}l{L * ca:.1f} {L * sa:.1f}" stroke="{INK}" '
                   f'stroke-width="{rnd.uniform(*weight):.1f}" stroke-linecap="round"/>')
    return "".join(out)


def bubble(d: Doc, cx, cy, rx, ry, tail, lines, size, key="bold", sw=4.5):
    """Speech balloon. Stroke both shapes wide, then fill both, leaving one outer outline."""
    tx, ty = tail
    ang = math.atan2(ty - cy, tx - cx)
    nx, ny = -math.sin(ang), math.cos(ang)
    bx, by = cx + rx * 0.72 * math.cos(ang), cy + ry * 0.72 * math.sin(ang)
    w = min(rx, ry) * 0.22
    tail_pts = pts([(bx + nx * w, by + ny * w), (tx, ty), (bx - nx * w, by - ny * w)])
    shapes = f'<ellipse cx="{cx}" cy="{cy}" rx="{rx}" ry="{ry}"/><polygon points="{tail_pts}"/>'
    d.add(f'<g fill="{INK}" stroke="{INK}" stroke-width="{sw * 2}" stroke-linejoin="round">{shapes}</g>',
          f'<g fill="#FFFFFF">{shapes}</g>')
    lead = size * 1.4
    y = cy - (len(lines) - 1) * lead / 2 + size * 0.36
    for i, line in enumerate(lines):
        d.text(cx, y + i * lead, line, key, size, INK, anchor="middle")


def caption(d: Doc, x, y, lines, size, key="display", pad=(18, 14), lead=1.18):
    """Narration box: a sharp black rectangle with paper lettering."""
    w = max(measure(line, key, size) for line in lines) + pad[0] * 2
    h = size * lead * len(lines) + pad[1] * 2 - size * (lead - 1) + size * 0.12
    d.add(f'<rect x="{x}" y="{y}" width="{w:.1f}" height="{h:.1f}" fill="{INK}" stroke="{INK}" stroke-width="4"/>')
    for i, line in enumerate(lines):
        d.text(x + pad[0], y + pad[1] + size * 0.86 + i * size * lead, line, key, size, PAPER)
    return w, h


def panel(d: Doc, poly, art, cid="p"):
    """An inked panel: paper fill with the art clipped to the panel shape."""
    d.defs.append(f'<clipPath id="{cid}"><polygon points="{pts(poly)}"/></clipPath>')
    d.add(f'<polygon points="{pts(poly)}" fill="{PAPER}"/>', f'<g clip-path="url(#{cid})">{art}</g>')


def border(d: Doc, poly, sw=6):
    d.add(f'<polygon points="{pts(poly)}" fill="none" stroke="{INK}" stroke-width="{sw}" stroke-linejoin="miter"/>')


# ---------------------------------------------------------------- hero


def hero():
    W, H = 1280, 640
    d = Doc(W, H, "Imtiaz Nabi. AI Engineer at NDMA, Pakistan. "
                  "I build early-warning systems for floods, cloudbursts and earthquakes.")
    poly = [(4, 4), (W - 4, 4), (W - 4, H - 4), (4, H - 4)]
    art = focus_lines(470, 300, 430, 175, 260, seed=4)
    art += halftone(0, 470, 760, 180, 13, lambda x, y: max(0, (y - 470) / 180 * 5.2 - x / 760 * 2.2))
    panel(d, poly, art)

    # Vertical title strip: "disaster-prevention engineer".
    col_x = 1170
    d.add(f'<rect x="{col_x - 64}" y="4" width="132" height="{H - 8}" fill="{INK}"/>')
    for i, ch in enumerate("防災エンジニア"):
        d.text(col_x, 108 + i * 76, ch, "display", 66, RED if i < 2 else PAPER, anchor="middle")

    caption(d, 92, 120, ["AI ENGINEER, NDMA PAKISTAN"], 24)
    keyline = (f' stroke="{PAPER}" stroke-width="22" stroke-linejoin="round" paint-order="stroke" '
               f'transform="rotate(-4 470 300)"')
    d.text(470, 268, "IMTIAZ", "display", 132, INK, anchor="middle", extra=keyline)
    d.text(500, 400, "NABI", "display", 132, RED, anchor="middle", extra=keyline)
    bubble(d, 870, 500, 205, 92, (1060, 620),
           ["I build early-warning", "systems for floods,", "cloudbursts and earthquakes."], 23)
    border(d, poly, 8)
    d.save("hero.svg")


# ---------------------------------------------------------------- project panels


def project_panel(name, w, h, poly, art, title, body, stack, sfx, title_size, body_width, x, indent=22):
    d = Doc(w, h, f"{title}. {body} Built with {', '.join(stack)}.")
    panel(d, poly, art)
    for sx, sy, s, size, opts in sfx:
        d.sfx(sx, sy, s, size, **opts)
    titles = wrap(title, "display", title_size, body_width)
    lines = wrap(body, "body", 21, body_width)
    lead = 21 * 1.55
    th = title_size * 1.18 * len(titles) + 28 - title_size * 0.18 + title_size * 0.12
    bh = lead * len(lines) + 30
    by = h - 64 - bh - th + 6
    caption(d, x, by, titles, title_size)
    nx, ny = x + indent, by + th - 6
    bw = max(measure(line, "body", 21) for line in lines) + 40
    d.add(f'<rect x="{nx}" y="{ny:.1f}" width="{bw:.1f}" height="{bh:.1f}" fill="#FFFFFF" stroke="{INK}" stroke-width="4"/>')
    for i, line in enumerate(lines):
        d.text(nx + 20, ny + 15 + 21 * 1.05 + i * lead, line, "body", 21, INK)
    # The stack is the panel's footnote, marked with the manga reference mark.
    keyline = f' stroke="{PAPER}" stroke-width="7" stroke-linejoin="round" paint-order="stroke"'
    d.text(nx + 2, ny + bh + 34, "※ " + " / ".join(stack), "bold", 17, INK, extra=keyline)
    border(d, poly)
    d.save(name)


def panels():
    # Full-width opener: the alert system, with speed lines and beeping.
    W, H = 1280, 430
    poly = [(4, 4), (W - 4, 4), (W - 4, H - 4), (4, H - 4)]
    art = speed_lines(560, 10, 720, 410, 90, seed=2, length=(160, 520))
    art += halftone(700, 0, 580, 430, 14, lambda x, y: max(0, (x - 700) / 580 * 5.6 - 0.6))
    project_panel(
        "panel-ews.svg", W, H, poly, art, "Pakistan Early Warning System",
        "District-level weather monitoring with lifecycle-managed hazard alerts. Forecasts are "
        "screened against thresholds, and the evidence behind every alert is kept.",
        ["FastAPI", "PostgreSQL", "React", "MapLibre"],
        [(1010, 200, "ピピピ", 120, {"fill": RED, "rotate": -8}), (1130, 340, "！", 120, {"rotate": 10})],
        title_size=42, body_width=600, x=36,
    )

    # Pair one: a slanted gutter between rain and rumble.
    w, h = 632, 520
    left = [(4, 4), (w - 6, 4), (w - 52, h - 4), (4, h - 4)]
    right = [(48, 4), (w - 4, 4), (w - 4, h - 4), (4, h - 4)]
    rain = halftone(0, 0, w, 260, 12, lambda x, y: max(0, 5.4 - y / 260 * 5.4))
    rain += speed_lines(-80, -40, w + 80, h, 150, seed=9, angle=72, length=(40, 140), weight=(1, 2))
    project_panel(
        "panel-cloudburst.svg", w, h, left, rain, "Cloudburst Early Warning",
        "A nowcasting pipeline that gates cloudburst risk on convective predictors: CAPE, "
        "precipitable water, lifting index and wind shear.",
        ["Python", "uv", "Nowcast CLI"],
        [(450, 140, "ザーッ", 92, {"rotate": 8})],
        title_size=30, body_width=400, x=30,
    )
    rnd = random.Random(6)
    shake = "".join(
        f'<polyline points="{pts([(x, y + (9 if k % 2 else -9) * rnd.uniform(.5, 1)) for k, x in enumerate(range(-20, w + 40, 26))])}" '
        f'fill="none" stroke="{INK}" stroke-width="{rnd.uniform(1, 2.6):.1f}"/>'
        for y in range(40, h, 34)
    )
    quake = f'<g opacity="0.22">{shake}</g>' + focus_lines(430, 130, 150, 80, 120, seed=8, width=(1, 4))
    project_panel(
        "panel-quake.svg", w, h, right, quake, "Earthquake Intensity Platform",
        "Enter magnitude, depth and epicentre. Get site-amplified MMI contours across "
        "Pakistan and its neighbours, with no ArcGIS dependency.",
        ["NumPy", "Uvicorn", "Leaflet"],
        [(390, 112, "ゴゴゴ", 70, {"rotate": -12}), (500, 186, "ゴゴゴ", 52, {"fill": RED, "rotate": -12})],
        title_size=30, body_width=420, x=56,
    )

    # Pair two: the chatbot talks in balloons, the drill buzzes with commotion.
    left = [(4, 4), (w - 52, 4), (w - 6, h - 4), (4, h - 4)]
    right = [(4, 4), (w - 4, 4), (w - 4, h - 4), (48, h - 4)]
    scratch = Doc(1, 1, "")  # draw background balloons, then lift their markup into the art
    bubble(scratch, 420, 100, 120, 58, (340, 176), [], 10)
    bubble(scratch, 210, 76, 92, 46, (260, 136), [], 10)
    chat = halftone(0, 0, w, h, 12, lambda x, y: 1.5) + "".join(scratch.body)
    project_panel(
        "panel-rag.svg", w, h, left, chat, "ChatbotARK",
        "A disaster-management assistant with cited RAG answers, local Llama 3.1, Whisper "
        "voice input and Stable Diffusion visuals.",
        ["Ollama", "ChromaDB", "Whisper"],
        [(420, 116, "ピコン", 46, {"fill": RED, "outline": "#FFFFFF"})],
        title_size=34, body_width=420, x=30,
    )
    drill = focus_lines(330, 120, 200, 70, 150, seed=12, width=(1, 3.2))
    project_panel(
        "panel-simex.svg", w, h, right, drill, "SimEx AI",
        "An AI assistant for NDMA simulation exercises, the drills that test coordination "
        "across ten disaster-response wings.",
        ["FastAPI", "LangChain", "Ollama"],
        [(200, 92, "ザワ", 54, {"rotate": -10}), (330, 138, "ザワ", 64, {"fill": RED}),
         (468, 96, "ザワ", 50, {"rotate": 12})],
        title_size=34, body_width=440, x=36,
    )


# ---------------------------------------------------------------- headings, links, yonkoma


def heading(slug, text, jp):
    size, jsize = 34, 24
    tw, jw = measure(text, "display", size), measure(jp, "display", jsize)
    w, h = int(28 + jw + 18 + tw + 34), 76
    d = Doc(w + 8, h + 8, text)
    d.add(f'<rect x="8" y="8" width="{w - 4}" height="{h - 4}" fill="{INK}"/>',
          f'<rect x="2" y="2" width="{w - 4}" height="{h - 4}" fill="{PAPER}" stroke="{INK}" stroke-width="4"/>')
    d.text(26, 48, jp, "display", jsize, RED)
    d.text(26 + jw + 16, 51, text, "display", size, INK)
    d.save(f"h-{slug}.svg")


def link(slug, text):
    size = 20
    w = int(measure(text, "display", size)) + 52
    d = Doc(w + 6, 62, text)
    d.add(f'<rect x="6" y="6" width="{w - 4}" height="52" fill="{INK}"/>',
          f'<rect x="2" y="2" width="{w - 4}" height="52" fill="#FFFFFF" stroke="{INK}" stroke-width="4"/>')
    d.text(2 + (w - 4) / 2, 36, text, "display", size, INK, anchor="middle")
    d.save(f"link-{slug}.svg")


def yonkoma():
    """The bio, 'Coffee, Code, Crisis, Commit', as a four-panel strip."""
    W, H = 1280, 330
    d = Doc(W, H, "Four-panel strip: Coffee, Code, Crisis, Commit. To be continued.")
    gut, top, bottom = 18, 4, 262
    pw = (W - 8 - gut * 3) / 4
    beats = [("COFFEE", "ズズッ", INK), ("CODE", "カタカタ", INK), ("CRISIS", "ドドド", RED), ("COMMIT", "ドン！", INK)]
    for i, (word, fx, colour) in enumerate(beats):
        x0 = 4 + i * (pw + gut)
        poly = [(x0, top), (x0 + pw, top), (x0 + pw, bottom), (x0, bottom)]
        mx, my = x0 + pw / 2, (top + bottom) / 2 + 20
        if i == 0:  # steam rising off the cup
            art = "".join(
                f'<path d="M{mx - 40 + k * 40} {my + 40} q-16 -26 0 -52 t0 -52" fill="none" stroke="{INK}" '
                f'stroke-width="3" stroke-linecap="round" opacity="0.5"/>' for k in range(3)
            )
            art += halftone(x0, top, pw, bottom, 12, lambda x, y: max(0, (y - 120) / 140 * 3.6))
        elif i == 1:
            art = speed_lines(x0 - 40, top, pw + 40, bottom, 40, seed=31, length=(60, 200))
        elif i == 2:
            art = focus_lines(mx, my - 10, 70, 46, 140, seed=33, width=(1.2, 5))
        else:
            art = halftone(x0, top, pw, bottom, 12, lambda x, y: 2.2) + focus_lines(mx, my - 10, 92, 62, 70, seed=35)
        panel(d, poly, art, cid=f"k{i}")
        d.sfx(mx, my + 22, fx, 50 if len(fx) > 3 else 60, colour, rotate=(-6, 4, -8, 0)[i])
        caption(d, x0 + 4, top + 4, [word], 18, pad=(12, 9))
        border(d, poly, 5)
    label = "TO BE CONTINUED  つづく →"
    w = measure(label, "display", 20) + 40
    d.add(f'<rect x="{W - 4 - w:.1f}" y="{bottom + 20}" width="{w:.1f}" height="44" fill="{INK}"/>')
    d.text(W - 4 - w / 2, bottom + 50, label, "display", 20, PAPER, anchor="middle")
    d.save("yonkoma.svg")


if __name__ == "__main__":
    ensure_fonts("防災エンジニアピザーッゴコンワプロジェクトつくるもの道具いま連絡ズカタドづ！※→")
    for old in OUT.glob("*.svg"):
        old.unlink()
    hero()
    panels()
    for slug, text, jp in [("projects", "Projects", "プロジェクト"), ("build", "What I build", "つくるもの"),
                           ("stack", "Toolkit", "道具"), ("now", "Right now", "いま"),
                           ("contact", "Contact", "連絡")]:
        heading(slug, text, jp)
    for slug, text in [("linkedin", "LinkedIn"), ("youtube", "YouTube"),
                       ("substack", "Substack"), ("github", "GitHub / Imtiaz08")]:
        link(slug, text)
    yonkoma()
