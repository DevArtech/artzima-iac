"""The designed layout of `make dr-pdf` (used by scripts/dr_pdf.py).

reportlab draws the pages, markdown-it-py parses the Markdown, the fonts are IBM Plex Sans and Plex Mono
(SIL OFL) from $ARTZIMA_TOOLS/fonts; `make deps` installs all three. Nothing is fetched at render time.

Layout rules, so that doc edits flow in without touching this file:
  - the first `#` heading is the title (cover); later `#` and every `##` open a section on a new page;
    "X. Title" (X = 0, A-F) gets a badge, A-F also the phase strip;
  - `###` "B4. Title" is a step (tag + tick box); "P1. Title" is a scenario card;
  - ordered lists get a tick box per item, "- [ ]" items a tick box, other lists bullets;
  - "**Expect:**", "**If this fails:**", "**STOP ...**" and "**... must not ...**" start callouts (also
    mid-paragraph); "**The one rule ...**", "**Not recoverable:**" paragraphs are cautions, a paragraph
    ending in "You need:" puts the next list in a box;
  - in-document links get the target's page number; code lines that do not fit continue on the next
    row after a "↪".
"""

from __future__ import annotations

import copy
import io
import os
import pathlib
import re
from xml.sax.saxutils import escape

from markdown_it import MarkdownIt
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.platypus import (BaseDocTemplate, CondPageBreak, Flowable, Frame, KeepInFrame, NextPageTemplate,
                                PageBreak, PageTemplate, Paragraph, Spacer, Table, TableStyle)
from reportlab.platypus.tableofcontents import TableOfContents

TOOLS = pathlib.Path(os.environ.get("ARTZIMA_TOOLS") or pathlib.Path.home() / ".local/share/artzima-tools")
FONT_DIR = TOOLS / "fonts"
FONT_FILES = {
    "Sans": "IBMPlexSans-Regular", "Sans-Italic": "IBMPlexSans-Italic", "Sans-Light": "IBMPlexSans-Light",
    "Sans-Medium": "IBMPlexSans-Medium", "Sans-Semi": "IBMPlexSans-SemiBold",
    "Sans-SemiItalic": "IBMPlexSans-SemiBoldItalic", "Sans-Bold": "IBMPlexSans-Bold",
    "Mono": "IBMPlexMono-Regular", "Mono-Italic": "IBMPlexMono-Italic", "Mono-Semi": "IBMPlexMono-SemiBold",
}
CHAR_FIX = {"✔": "✓"}   # glyphs Plex lacks

# One accent; everything else is ink and greys, so it prints well in black and white.
ACCENT = colors.HexColor("#1F5C8A")
ACCENT_SOFT = colors.HexColor("#C9DBEA")
INK = colors.HexColor("#1C2530")
MUTED = colors.HexColor("#5E6A75")
RULE = colors.HexColor("#CDD5DC")
TINT = colors.HexColor("#EEF3F8")
CODE_BG = colors.HexColor("#F3F5F7")
ZEBRA = colors.HexColor("#F6F8FA")
WHITE = colors.white
HEX = {"accent": "#1F5C8A", "muted": "#5E6A75", "code_bg": "#ECEFF2"}

PW, PH = letter
ML = MR = 0.85 * inch
MT, MB = 0.95 * inch, 0.85 * inch
TW = PW - ML - MR


def available() -> str | None:
    """None if the designed renderer can run, else the reason."""
    missing = [f for f in FONT_FILES.values() if not (FONT_DIR / f"{f}.ttf").exists()]
    return f"fonts missing in {FONT_DIR} (run make deps)" if missing else None


def register_fonts() -> None:
    for name, f in FONT_FILES.items():
        pdfmetrics.registerFont(TTFont(name, str(FONT_DIR / f"{f}.ttf")))
    pdfmetrics.registerFontFamily("Sans", normal="Sans", bold="Sans-Semi", italic="Sans-Italic",
                                  boldItalic="Sans-SemiItalic")
    pdfmetrics.registerFontFamily("Mono", normal="Mono", bold="Mono-Semi", italic="Mono-Italic",
                                  boldItalic="Mono-Semi")


def style(name: str, **kw) -> ParagraphStyle:
    base = dict(fontName="Sans", fontSize=9.6, leading=13.6, textColor=INK, allowWidows=0, allowOrphans=0,
                splitLongWords=1)
    base.update(kw)
    return ParagraphStyle(name, **base)


BODY = style("body", spaceAfter=6)
ITEM = style("item", spaceAfter=3)
CELL = style("cell", fontSize=8.6, leading=11.8)
CELL_TAG = style("celltag", fontName="Sans-Semi", fontSize=8.6, leading=11.8, textColor=ACCENT)
CELL_HEAD = style("cellhead", fontName="Sans-Semi", fontSize=7.4, leading=10, textColor=MUTED,
                  textTransform="uppercase")
CALLOUT = style("callout", fontSize=9.2, leading=13.2, spaceAfter=3)
H2 = style("h2", fontName="Sans-Semi", fontSize=21, leading=25)
H3 = style("h3", fontName="Sans-Semi", fontSize=12.5, leading=15.5)
H3_CARD = style("h3card", fontName="Sans-Semi", fontSize=14, leading=17)
TOC0 = style("toc0", fontName="Sans-Semi", fontSize=10.2, leading=13.5, spaceBefore=8)
TOC1 = style("toc1", fontSize=8.6, leading=11.4, leftIndent=10)
TOC0_NUM = style("toc0n", fontName="Sans-Semi", fontSize=10.2, leading=13.5, alignment=2)
TOC1_NUM = style("toc1n", fontSize=8.6, leading=11.4, textColor=MUTED, alignment=2)
COVER_ITEM = style("coveritem", fontSize=9.4, leading=13.4)
SMALL = style("small", fontSize=7.6, leading=10, textColor=MUTED)


def fix(s: str) -> str:
    return "".join(CHAR_FIX.get(c, c) for c in s)


def esc(s: str) -> str:
    return escape(fix(s))


def slug(text: str) -> str:
    """GitHub's heading anchor."""
    return re.sub(r"[^\w\- ]", "", text.strip().lower()).replace(" ", "-")


def plain(children) -> str:
    return "".join(t.content for t in children if t.type in ("text", "code_inline"))


# ---------------------------------------------------------------- flowables
class Indented(Flowable):
    """Shift a flowable right (lists); optionally draw a marker (bullet, tick box) in the gutter."""

    def __init__(self, inner, indent, marker=None):
        super().__init__()
        self.inner, self.indent, self.marker = inner, indent, marker
        self.keepWithNext = inner.getKeepWithNext()

    def wrap(self, aw, ah):
        w, h = self.inner.wrap(aw - self.indent, ah)
        self.width, self.height = aw, h
        return aw, h

    def split(self, aw, ah):
        parts = self.inner.split(aw - self.indent, ah)
        return [Indented(p, self.indent, self.marker if i == 0 else None) for i, p in enumerate(parts)]

    def getSpaceBefore(self):
        return self.inner.getSpaceBefore()

    def getSpaceAfter(self):
        return self.inner.getSpaceAfter()

    def draw(self):
        self.inner.drawOn(self.canv, self.indent, 0)
        if self.marker:
            size = getattr(getattr(self.inner, "style", None), "fontSize", 9.6)
            self.marker(self.canv, self.indent, self.height - size + 0.6)   # first baseline


def spaced(c, x, y, text, font, size, space, color):
    """Letter-spaced small caps label (the character spacing is reset afterwards: it is PDF state)."""
    t = c.beginText(x, y)
    t.setFont(font, size)
    t.setFillColor(color)
    t.setCharSpace(space)
    t.textOut(text.upper())
    t.setCharSpace(0)
    c.drawText(t)


def tick_box(c, x, y, size=8.6):
    c.saveState()
    c.setStrokeColor(INK)
    c.setLineWidth(0.85)
    c.roundRect(x, y, size, size, 1.2, stroke=1, fill=0)
    c.restoreState()


def marker_bullet(depth):
    def draw(c, indent, base):
        c.saveState()
        c.setFillColor(ACCENT if depth == 0 else MUTED)
        if depth == 0:
            c.rect(indent - 9.5, base + 2.2, 3.4, 3.4, stroke=0, fill=1)
        else:
            c.rect(indent - 9.5, base + 3.2, 4.2, 1.1, stroke=0, fill=1)
        c.restoreState()
    return draw


def marker_task(c, indent, base):
    tick_box(c, indent - 14.5, base - 1.2)


def marker_number(n):
    def draw(c, indent, base):
        tick_box(c, indent - 29, base - 1.2)
        c.saveState()
        c.setFillColor(ACCENT)
        c.setFont("Sans-Semi", 9.6)
        c.drawRightString(indent - 4.5, base, f"{n}.")
        c.restoreState()
    return draw


class Code(Flowable):
    """A shaded code block. Long lines wrap; continuation rows start with an accent "↪"."""
    FS, LEAD, PADX, PADY = 7.9, 10.3, 9, 6

    def __init__(self, text=None, lang="", rows=None, cont=False, more=False):
        super().__init__()
        self.lang, self.cont, self.more = lang, cont, more
        self.lines = fix(text).rstrip("\n").expandtabs(4).split("\n") if text is not None else None
        self.rows = rows
        self.top = self.PADY + (6 if lang and not cont else 0)

    def _wrap_rows(self, aw):
        n = int((aw - 2 * self.PADX - 3) / stringWidth("M", "Mono", self.FS))
        rows = []
        for line in self.lines:
            ind = min(len(line) - len(line.lstrip(" ")), n // 3)
            cont, rest = None, line           # cont: None = a source line, else the "↪" column
            while True:
                lim = n if cont is None else n - ind - 3
                if cont is not None and not rest:
                    break
                if len(rest) <= lim:
                    rows.append((rest, cont))
                    break
                cut = next((k for k in range(lim, int(lim * 0.45), -1)
                            if rest[k - 1] in " ,;|&=/" or rest[k] == "\\"), lim)
                rows.append((rest[:cut], cont))
                rest, cont = rest[cut:].lstrip(" ") if rest[cut - 1] == " " else rest[cut:], ind
        return rows

    def wrap(self, aw, ah):
        if self.rows is None:
            self.rows = self._wrap_rows(aw)
        self.width, self.height = aw, self.top + len(self.rows) * self.LEAD + self.PADY
        return self.width, self.height

    def split(self, aw, ah):
        self.wrap(aw, ah)
        fit = int((ah - self.top - self.PADY) / self.LEAD)
        if fit < 3 or fit >= len(self.rows) or len(self.rows) - fit < 2:
            return []
        return [Code(lang=self.lang, rows=self.rows[:fit], cont=self.cont, more=True),
                Code(lang=self.lang, rows=self.rows[fit:], cont=True, more=self.more)]

    def getSpaceBefore(self):
        return 2

    def getSpaceAfter(self):
        return 8

    def draw(self):
        c = self.canv
        output = not self.lang
        c.saveState()
        if output:   # expected output: outlined, not shaded
            c.setStrokeColor(RULE)
            c.setDash(2, 1.6)
            c.setLineWidth(0.8)
            c.roundRect(0, 0, self.width, self.height, 2.5, stroke=1, fill=0)
            c.setDash()
        else:
            c.setFillColor(CODE_BG)
            c.roundRect(0, 0, self.width, self.height, 2.5, stroke=0, fill=1)
            c.setFillColor(ACCENT)
            c.rect(0, 0, 2.2, self.height, stroke=0, fill=1)
        label = "expected output" if output else self.lang
        if not self.cont and label:
            c.setFillColor(MUTED)
            c.setFont("Sans-Medium", 6.4)
            c.drawRightString(self.width - 7, self.height - 9, label.upper())
        if self.cont or self.more:
            c.setFillColor(MUTED)
            c.setFont("Sans-Italic", 6.4)
            if self.more:
                c.drawRightString(self.width - 7, 2.6, "continues on the next page")
        y = self.height - self.top - self.FS
        cw = stringWidth("M", "Mono", self.FS)
        for text, cont in self.rows:
            x = self.PADX
            if cont is not None:
                x += cont * cw
                c.setFillColor(ACCENT)
                c.setFont("Mono-Semi", self.FS)
                c.drawString(x, y, "↪")
                x += 3 * cw
            c.setFillColor(INK)
            c.setFont("Mono", self.FS)
            c.drawString(x, y, text)
            y -= self.LEAD
        c.restoreState()


CALLOUTS = {   # kind: label, icon, look
    "expect": ("EXPECT", "check", "bar"),
    "fails": ("IF THIS FAILS", "cross", "dashbar"),
    "warn": ("CAUTION", "warn", "frame"),
    "note": ("NOTE", "info", "tint"),
    "first": ("DO THIS FIRST", "arrow", "tintframe"),
    "need": ("YOU NEED", "list", "box"),
}


def draw_icon(c, kind, x, y, r=5.2, color=ACCENT):
    """Small vector icons (no glyph dependencies). (x, y) = centre."""
    c.saveState()
    c.setLineWidth(0.9)
    c.setStrokeColor(color)
    c.setFillColor(color)
    if kind == "warn":
        p = c.beginPath()
        p.moveTo(x - r * 1.1, y - r * 0.85)
        p.lineTo(x + r * 1.1, y - r * 0.85)
        p.lineTo(x, y + r * 1.05)
        p.close()
        c.drawPath(p, stroke=1, fill=1)
        c.setFillColor(WHITE)
        c.setFont("Sans-Bold", r * 1.35)
        c.drawCentredString(x, y - r * 0.68, "!")
    elif kind == "check":
        c.circle(x, y, r, stroke=0, fill=1)
        c.setStrokeColor(WHITE)
        c.setLineWidth(1.2)
        p = c.beginPath()
        p.moveTo(x - r * 0.48, y + r * 0.02)
        p.lineTo(x - r * 0.12, y - r * 0.38)
        p.lineTo(x + r * 0.5, y + r * 0.38)
        c.drawPath(p, stroke=1, fill=0)
    elif kind == "cross":
        c.circle(x, y, r, stroke=1, fill=0)
        d = r * 0.4
        c.line(x - d, y - d, x + d, y + d)
        c.line(x - d, y + d, x + d, y - d)
    elif kind == "info":
        c.circle(x, y, r, stroke=1, fill=0)
        c.setFont("Sans-Bold", r * 1.3)
        c.drawCentredString(x, y - r * 0.45, "i")
    elif kind == "arrow":
        c.circle(x, y, r, stroke=0, fill=1)
        c.setStrokeColor(WHITE)
        c.setLineWidth(1.1)
        c.line(x - r * 0.5, y, x + r * 0.45, y)
        c.line(x + r * 0.05, y + r * 0.4, x + r * 0.5, y)
        c.line(x + r * 0.05, y - r * 0.4, x + r * 0.5, y)
    elif kind == "list":
        c.roundRect(x - r, y - r, 2 * r, 2 * r, 1.2, stroke=1, fill=0)
        for dy in (0.42, 0, -0.42):
            c.line(x - r * 0.5, y + r * dy, x + r * 0.5, y + r * dy)
    c.restoreState()


class Contents(TableOfContents):
    """Contents as a two-column table: entry, page number right-aligned; a rule above each section."""

    def wrap(self, aw, ah):
        entries = self._lastEntries or [(0, "(contents are filled in on the next pass)", 0, None)]
        rows = []
        cmds = [("VALIGN", (0, 0), (-1, -1), "BOTTOM"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 1.3), ("BOTTOMPADDING", (0, 0), (-1, -1), 1.3)]
        for i, (level, text, page, key) in enumerate(e[:4] for e in entries):
            a, z = (f'<a href="#{key}">', "</a>") if key else ("", "")
            rows.append([Paragraph(a + text + z, TOC0 if level == 0 else TOC1),
                         Paragraph(f"{a}{page}{z}", TOC0_NUM if level == 0 else TOC1_NUM)])
            if level == 0:
                cmds += [("TOPPADDING", (0, i), (-1, i), 9 if i else 0), ("BOTTOMPADDING", (0, i), (-1, i), 3)]
                if i:
                    cmds.append(("LINEABOVE", (0, i), (-1, i), 0.5, RULE))
        self._table = Table(rows, colWidths=[aw - 26, 26], style=TableStyle(cmds), hAlign="LEFT")
        return self._table.wrapOn(self.canv, aw, ah)


class Callout(Flowable):
    LW, PADX, PADY = 84, 9, 5.5

    def __init__(self, kind, content):
        super().__init__()
        self.kind, self.content = kind, content

    def wrap(self, aw, ah):
        cw = aw - self.LW - self.PADX
        self.sizes, h = [], 0
        for i, f in enumerate(self.content):
            _, fh = f.wrap(cw, 10 ** 6)
            gap = 0 if i == 0 else max(self.content[i - 1].getSpaceAfter(), f.getSpaceBefore())
            self.sizes.append((gap, fh))
            h += gap + fh
        self.width, self.height = aw, max(h, 14) + 2 * self.PADY
        return self.width, self.height

    def split(self, aw, ah):
        return []

    def getSpaceBefore(self):
        return 3

    def getSpaceAfter(self):
        return 9

    def draw(self):
        c, (label, icon, look) = self.canv, CALLOUTS[self.kind]
        w, h = self.width, self.height
        tone = INK if self.kind == "warn" else (MUTED if self.kind == "fails" else ACCENT)
        c.saveState()
        if look in ("tint", "tintframe"):
            c.setFillColor(TINT)
            c.roundRect(0, 0, w, h, 2.5, stroke=0, fill=1)
        if look in ("frame", "tintframe", "box"):
            c.setStrokeColor(RULE if look == "box" else tone)
            c.setLineWidth(0.8)
            c.roundRect(0, 0, w, h, 2.5, stroke=1, fill=0)
        bar = {"frame": 4.2, "tintframe": 4.2, "box": 0}.get(look, 2.4)
        if bar:
            c.setFillColor(tone)
            if look == "dashbar":
                for y0 in range(0, int(h), 5):
                    c.rect(0, y0, bar, min(3, h - y0), stroke=0, fill=1)
            else:
                c.rect(0, 0, bar, h, stroke=0, fill=1)
        top = h - self.PADY
        draw_icon(c, icon, 15.5, top - 5.4, color=tone)
        c.setFillColor(tone)
        c.setFont("Sans-Semi", 6.6)
        words = label.split(" ")
        lines = [label] if stringWidth(label, "Sans-Semi", 6.6) < self.LW - 28 else \
            [" ".join(words[:len(words) // 2 + 1]), " ".join(words[len(words) // 2 + 1:])]
        for i, line in enumerate(lines):
            c.drawString(24, top - 7.6 - i * 8, line)
        c.restoreState()
        y = top
        for f, (gap, fh) in zip(self.content, self.sizes):
            y -= gap + fh
            f.drawOn(c, self.LW, y)


class SectionOpener(Flowable):
    """Section start: kicker, badge with the phase letter, big title, phase strip for A-F."""
    BADGE = 46

    def __init__(self, tag, kicker, title, key, plain_title, phase=False):
        super().__init__()
        self.tag, self.kicker, self.key, self.plain, self.phase = tag, kicker, key, plain_title, phase
        self.para = Paragraph(title, H2)
        self.toc_text = (f'<font name="Sans-Semi" color="{HEX["accent"]}">{esc(tag)}</font> ' if tag
                         else "") + esc(plain_title)
        self.header = f"{tag} · {plain_title}" if tag else plain_title
        self.card = False

    def wrap(self, aw, ah):
        x0 = self.BADGE + 14 if self.tag else 0
        _, th = self.para.wrap(aw - x0, ah)
        self.th, self.x0 = th, x0
        self.width, self.height = aw, max(self.BADGE if self.tag else 0, 17 + th) + 12
        return self.width, self.height

    def getSpaceAfter(self):
        return 14

    def draw(self):
        c, w, h = self.canv, self.width, self.height
        c.bookmarkPage(self.key)
        c.addOutlineEntry(self.plain if not self.tag else f"{self.tag}. {self.plain}", self.key, level=0)
        c.saveState()
        if self.tag:
            c.setFillColor(ACCENT)
            c.roundRect(0, h - self.BADGE, self.BADGE, self.BADGE, 4, stroke=0, fill=1)
            c.setFillColor(WHITE)
            c.setFont("Sans-Bold", 26)
            c.drawCentredString(self.BADGE / 2, h - self.BADGE + 13.5, self.tag)
        spaced(c, self.x0, h - 9, self.kicker, "Sans-Semi", 7.6, 1.4, ACCENT)
        if self.phase:
            box, gap = 15, 3.5
            x = w - 6 * box - 5 * gap
            for letter in "ABCDEF":
                cur = letter == self.tag
                c.setLineWidth(0.7)
                c.setStrokeColor(ACCENT if cur else RULE)
                c.setFillColor(ACCENT if cur else WHITE)
                c.roundRect(x, h - box - 1, box, box, 2, stroke=1, fill=1)
                c.setFillColor(WHITE if cur else MUTED)
                c.setFont("Sans-Semi", 8)
                c.drawCentredString(x + box / 2, h - box + 3.2, letter)
                x += box + gap
        c.restoreState()
        self.para.drawOn(c, self.x0, h - 17 - self.th)
        c.saveState()
        c.setStrokeColor(RULE)
        c.setLineWidth(0.6)
        c.line(0, 1, w, 1)
        c.setStrokeColor(ACCENT)
        c.setLineWidth(2.4)
        c.line(0, 1, self.BADGE if self.tag else 60, 1)
        c.restoreState()


class StepHeader(Flowable):
    """A step: accent tag, title, and a box to tick when done. P1-P5: a scenario card."""

    def __init__(self, tag, title, key, plain_title, card=False):
        super().__init__()
        self.tag, self.key, self.plain, self.card = tag, key, plain_title, card
        self.para = Paragraph(title, H3_CARD if card else H3)
        self.header = f"{tag} · {plain_title}"
        self.toc_text = (f'<font name="Sans-Semi" color="{HEX["accent"]}">{esc(tag)}</font> ' if tag
                         else "") + esc(plain_title)
        self.keepWithNext = 1

    def wrap(self, aw, ah):
        if self.card:
            self.tagw = stringWidth(self.tag, "Sans-Bold", 22)
            self.x0 = 16 + self.tagw + 14
            _, th = self.para.wrap(aw - self.x0 - 12, ah)
            self.th = th
            self.height = max(28, 11 + th) + 22
        else:
            self.pill = stringWidth(self.tag, "Sans-Semi", 9) + 12 if self.tag else 0
            self.x0 = self.pill + 8 if self.tag else 0
            _, th = self.para.wrap(aw - self.x0 - (24 if self.tag else 0), ah)
            self.th = th
            self.height = max(th, 16) + 7
        self.width = aw
        return self.width, self.height

    def getSpaceBefore(self):
        return 18 if self.card else 13

    def getSpaceAfter(self):
        return 10 if self.card else 7

    def draw(self):
        c, w, h = self.canv, self.width, self.height
        c.bookmarkPage(self.key)
        c.addOutlineEntry(f"{self.tag}. {self.plain}" if self.tag else self.plain, self.key, level=1)
        c.saveState()
        if self.card:
            c.setStrokeColor(ACCENT)
            c.setLineWidth(0.9)
            c.roundRect(0, 0, w, h, 3, stroke=1, fill=0)
            c.setFillColor(ACCENT)
            c.rect(0, 0, 5, h, stroke=0, fill=1)
            c.setFont("Sans-Bold", 22)
            c.drawString(16, h - 11 - 21, self.tag)
            spaced(c, self.x0, h - 11 - 6.5, "Scenario", "Sans-Semi", 6.8, 1.3, ACCENT)
            c.restoreState()
            self.para.drawOn(c, self.x0, h - 11 - 10 - self.th)
            return
        if self.tag:
            c.setFillColor(ACCENT)
            c.roundRect(0, h - 15.5, self.pill, 15.5, 3, stroke=0, fill=1)
            c.setFillColor(WHITE)
            c.setFont("Sans-Semi", 9)
            c.drawCentredString(self.pill / 2, h - 11.2, self.tag)
            tick_box(c, w - 10, h - 13.2, 10)
        c.setStrokeColor(RULE)
        c.setLineWidth(0.5)
        c.line(0, 0.5, w, 0.5)
        c.restoreState()
        self.para.drawOn(c, self.x0, h - self.th - 0.5)


# ---------------------------------------------------------------- Markdown -> flowables
H2_KICKERS = {"0": "Start here", "P": "Scenarios"}
TITLE_KICKERS = {"Partial loss": ("P", "Scenarios"), "Emergency kit": ("", "Reference"),
                 "Drills": ("", "Practice"), "Emergency kit checklist": ("", "Checklist: tick each item")}


def tree(tokens):
    root, stack = [], []
    cur = root
    for t in tokens:
        if t.nesting == 1:
            node = {"type": t.type[:-5], "tok": t, "children": []}
            cur.append(node)
            stack.append(cur)
            cur = node["children"]
        elif t.nesting == -1:
            cur = stack.pop()
        else:
            cur.append({"type": t.type, "tok": t, "children": []})
    return root


def split_kind(strong: str, after: str) -> str | None:
    if strong == "If this fails:":
        return "fails"
    if strong.rstrip(":,") == "Expect" and not after.lstrip().startswith("says"):
        return "expect"
    if strong.startswith("STOP") or " must not " in f" {strong} ":
        return "warn"
    return None


class Builder:
    def __init__(self, pages: dict[str, int]):
        self.pages = pages           # anchor -> page, from the previous build
        self.md = MarkdownIt("commonmark").enable("table")
        self.titled = False

    # ---- inline
    def inline(self, children, size=9.6) -> str:
        out, links = [], []
        for t in children:
            if t.type == "text":
                out.append(esc(t.content))
            elif t.type == "code_inline":
                out.append(f'<font face="Mono" size="{size * 0.9:.1f}" backColor="{HEX["code_bg"]}">'
                           f'{esc(t.content)}</font>')
            elif t.type == "strong_open":
                out.append("<b>")
            elif t.type == "strong_close":
                out.append("</b>")
            elif t.type == "em_open":
                out.append("<i>")
            elif t.type == "em_close":
                out.append("</i>")
            elif t.type == "softbreak":
                out.append(" ")
            elif t.type == "hardbreak":
                out.append("<br/>")
            elif t.type == "link_open":
                href = t.attrGet("href") or ""
                links.append(href)
                if href.startswith("#"):
                    out.append(f'<a href="{escape(href)}"><font color="{HEX["accent"]}">')
                elif href.startswith(("http://", "https://")):
                    out.append(f'<a href="{escape(href)}">')
                else:
                    out.append("")
            elif t.type == "link_close":
                href = links.pop() if links else ""
                if href.startswith("#"):
                    out.append("</font></a>")
                    page = self.pages.get(href[1:])
                    if page:
                        out.append(f'<font size="{size * 0.78:.1f}" color="{HEX["muted"]}"> (p. {page})</font>')
                elif href.startswith(("http://", "https://")):
                    out.append("</a>")
                else:
                    path = href.split("#")[0]
                    path = path[3:] if path.startswith("../") else f"docs/{path}"
                    out.append(f'<font face="Mono" size="{size * 0.76:.1f}" color="{HEX["muted"]}">'
                               f' ({esc(path)})</font>')
        return "".join(out)

    # ---- paragraphs, split into callouts
    def segments(self, children):
        segs = [["body", []]]
        kids = list(children)
        i = 0
        while i < len(kids):
            t = kids[i]
            if (t.type == "strong_open" and i + 2 < len(kids) and kids[i + 1].type == "text"
                    and kids[i + 2].type == "strong_close"):
                strong = kids[i + 1].content
                after = kids[i + 3].content if i + 3 < len(kids) and kids[i + 3].type == "text" else ""
                kind = split_kind(strong, after)
                if kind:
                    segs.append([kind, []])
                    if kind in ("expect", "fails"):
                        i += 3
                        if i < len(kids) and kids[i].type == "text":
                            kids[i] = copy.copy(kids[i])
                            kids[i].content = kids[i].content.lstrip(" ,:")
                        continue
            segs[-1][1].append(t)
            i += 1
        out = []
        for kind, toks in segs:
            while toks and (toks[0].type in ("softbreak", "hardbreak") or (toks[0].type == "text" and not toks[0].content.strip())):
                toks.pop(0)
            while toks and toks[-1].type in ("softbreak", "hardbreak"):
                toks.pop()
            if toks:
                out.append((kind, toks))
        if out and out[0][0] == "body":
            first = out[0][1]
            lead = first[1].content if first[0].type == "strong_open" and len(first) > 1 else ""
            text = plain(first)
            if lead.startswith(("The one rule", "Not recoverable")):
                out[0] = ("warn", first)
            elif lead.startswith(("Public reference", "Afterwards")):
                out[0] = ("note", first)
            elif text.startswith("Prevention (done"):
                out[0] = ("first", first)
        return out

    def paragraph(self, node, st):
        inl = node["children"][0]["tok"]
        children = list(inl.children or [])
        need = False
        if children and children[-1].type == "text" and children[-1].content.rstrip().endswith("You need:"):
            last = copy.copy(children[-1])
            last.content = last.content.rstrip()[:-len("You need:")].rstrip()
            children[-1] = last
            need = True
        flows = []
        for kind, toks in self.segments(children):
            if kind == "body":
                p = Paragraph(self.inline(toks, st.fontSize), st)
                if plain(toks).rstrip().endswith(":"):
                    p.keepWithNext = 1
                flows.append(p)
            else:
                flows.append(Callout(kind, [Paragraph(self.inline(toks, CALLOUT.fontSize), CALLOUT)]))
        return flows, need

    # ---- lists
    def lst(self, node, depth):
        ordered = node["type"] == "ordered_list"
        start = int(node["tok"].attrGet("start") or 1)
        out = []
        for n, item in enumerate(node["children"], start):
            kids = item["children"]
            task = False
            if kids and kids[0]["type"] == "paragraph":
                inl = kids[0]["children"][0]["tok"]
                first = inl.children[0] if inl.children else None
                if first is not None and first.type == "text" and first.content.startswith(("[ ] ", "[x] ")):
                    inl = copy.copy(inl)
                    inl.children = list(inl.children)
                    t0 = copy.copy(first)
                    t0.content = first.content[4:]
                    inl.children[0] = t0
                    kids = [{"type": "paragraph", "tok": kids[0]["tok"],
                             "children": [{"type": "inline", "tok": inl, "children": []}]}] + kids[1:]
                    task = True
            flows = self.blocks(kids, depth + 1, ITEM)
            if ordered:
                gutter, marker = 31, marker_number(n)
            elif task:
                gutter, marker = 19, marker_task
            else:
                gutter, marker = 14, marker_bullet(depth)
            out += [Indented(f, gutter, marker if i == 0 else None) for i, f in enumerate(flows)]
        if out:
            out.append(Spacer(1, 3))
        return out

    # ---- tables
    def table(self, node):
        rows, heads = [], []
        for part in node["children"]:
            for tr in part["children"]:
                rows.append([cell["children"][0]["tok"].children or [] for cell in tr["children"]])
                heads.append(part["type"] == "thead")
        ncol = max(len(r) for r in rows)
        data, mins, maxs = [], [0.0] * ncol, [0.0] * ncol
        for r, head in zip(rows, heads):
            line = []
            for j in range(ncol):
                kids = r[j] if j < len(r) else []
                text = plain(kids)
                st = CELL_HEAD if head else (CELL_TAG if re.fullmatch(r"[A-FP]\d|\d\.\d", text) else CELL)
                line.append(Paragraph(self.inline(kids, st.fontSize), st))
                fnt = "Sans-Semi" if head else "Sans"
                words = (text.upper() if head else text).split() or [""]
                mins[j] = max(mins[j], min(max(stringWidth(w, fnt, st.fontSize) for w in words), TW * 0.3) + 12)
                maxs[j] = max(maxs[j], stringWidth(text.upper() if head else text, fnt, st.fontSize) * 1.04 + 12)
            data.append(line)
        if sum(maxs) <= TW:
            widths = maxs if sum(maxs) < TW * 0.6 else [m * TW / sum(maxs) for m in maxs]
        else:
            extra = TW - sum(mins)
            if extra <= 0:
                widths = [m * TW / sum(mins) for m in mins]
            else:
                span = sum(mx - mn for mx, mn in zip(maxs, mins)) or 1
                widths = [mn + extra * (mx - mn) / span for mx, mn in zip(maxs, mins)]
        nhead = sum(heads)
        t = Table(data, colWidths=widths, repeatRows=nhead, hAlign="LEFT")
        t.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 3.6), ("BOTTOMPADDING", (0, 0), (-1, -1), 4.2),
            ("LINEBELOW", (0, nhead - 1), (-1, nhead - 1), 1.1, ACCENT),
            ("LINEBELOW", (0, nhead), (-1, -1), 0.4, RULE),
            ("ROWBACKGROUNDS", (0, nhead), (-1, -1), [WHITE, ZEBRA]),
        ]))
        t.spaceBefore, t.spaceAfter = 4, 12
        return [t]

    # ---- headings
    def heading(self, node):
        level = int(node["tok"].tag[1])
        inl = node["children"][0]["tok"]
        text = plain(inl.children)
        key = slug(text)
        if level == 1 and not self.titled:
            self.titled = True
            return []
        if level <= 2:
            m = re.match(r"^([0-9A-Z])\.\s+(.*)$", text)
            tag, title = (m.group(1), m.group(2)) if m else ("", text)
            if text in TITLE_KICKERS:
                tag, kicker = TITLE_KICKERS[text]
            else:
                kicker = H2_KICKERS.get(tag, "Phase" if tag else "")
            return [PageBreak(), SectionOpener(tag, kicker, esc(title), key, title, phase=tag in "ABCDEF" and tag != "")]
        m = re.match(r"^((?:[A-Z]\d+)|(?:\d+\.\d+))\.?\s+(.*)$", text)
        tag, title = (m.group(1), m.group(2)) if m else ("", text)
        markup = self.inline(inl.children, H3.fontSize)
        if tag:   # drop the tag from the rendered title
            markup = re.sub(r"^" + re.escape(tag) + r"\.?\s+", "", markup)
        card = bool(re.fullmatch(r"P\d+", tag))
        return [CondPageBreak(1.6 * inch if card else 1.3 * inch), StepHeader(tag, markup, key, title, card)]

    # ---- blocks
    def blocks(self, nodes, depth=0, st=BODY):
        out, need = [], False
        for node in nodes:
            typ = node["type"]
            if typ == "heading":
                out += self.heading(node)
            elif typ == "paragraph":
                flows, need = self.paragraph(node, st)
                out += flows
                continue
            elif typ in ("bullet_list", "ordered_list"):
                items = self.lst(node, depth)
                out += [Callout("need", items)] if need else items
            elif typ in ("fence", "code_block"):
                out.append(Code(node["tok"].content, (node["tok"].info or "").split(" ")[0]))
            elif typ == "table":
                out += self.table(node)
            elif typ == "blockquote":
                out.append(Callout("note", self.blocks(node["children"], depth, CALLOUT)))
            need = False
        return out

    def story(self, parts: list[str]) -> list:
        story = [Spacer(1, 1), NextPageTemplate("toc"), PageBreak()]     # page 1: the cover, drawn by the canvas
        toc = Contents(levelStyles=[TOC0, TOC1])
        story += [toc, NextPageTemplate("body")]
        for i, part in enumerate(parts):
            flows = self.blocks(tree(self.md.parse(part)))
            if i == 0:   # the text above the first section, on one page (shrunk a little if needed)
                cut = next((k for k, f in enumerate(flows) if isinstance(f, PageBreak)), len(flows))
                intro = [SectionOpener("", "Read first", "Before you start", "before-you-start",
                                       "Before you start")] + flows[:cut]
                height = sum(f.wrap(TW, PH)[1] + f.getSpaceBefore() + f.getSpaceAfter() for f in intro)
                if height <= 1.15 * (PH - MT - MB):
                    intro = [KeepInFrame(TW, PH - MT - MB - 2, intro, mode="shrink")]
                flows = [PageBreak()] + intro + flows[cut:]
            story += flows
        return story


# ---------------------------------------------------------------- pages
class Doc(BaseDocTemplate):
    def __init__(self, f, meta):
        super().__init__(f, pagesize=letter, leftMargin=ML, rightMargin=MR, topMargin=MT, bottomMargin=MB,
                         title="artzima disaster recovery", author="artzima-iac",
                         subject="Disaster-recovery runbook and emergency-kit checklist")
        self.meta = meta

        def frame(fid, x, w, top):
            return Frame(x, MB, w, top - MB, id=fid, leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
        gap = 26
        cw = (TW - gap) / 2
        toc_top = PH - MT - 58
        self.addPageTemplates([
            PageTemplate("cover", [frame("cover", ML, TW, PH - MT)], onPage=self.mark("cover")),
            PageTemplate("toc", [frame("t1", ML, cw, toc_top), frame("t2", ML + cw + gap, cw, toc_top)],
                         onPage=self.mark("toc")),
            PageTemplate("body", [frame("body", ML, TW, PH - MT)], onPage=self.mark("body")),
        ])
        self.beforeDocument()

    def beforeDocument(self):
        self.kinds, self.sections, self.anchors, self.openers = {}, {}, {}, []

    def mark(self, kind):
        def on_page(canv, doc):
            self.kinds[doc.page] = kind
        return on_page

    def afterFlowable(self, fl):
        if isinstance(fl, KeepInFrame):
            for inner in fl._content:
                self.afterFlowable(inner)
        elif isinstance(fl, (SectionOpener, StepHeader)):
            self.anchors[fl.key] = self.page
            level = 0 if isinstance(fl, SectionOpener) else 1
            self.notify("TOCEntry", (level, fl.toc_text, self.page, fl.key))
            if level == 0 or fl.card:     # the running header: section, or the scenario P1-P5
                self.sections.setdefault(self.page, fl.header)
            self.openers.append((level, fl.tag, fl.plain, self.page))


def fit(text, font, size, width):
    if stringWidth(text, font, size) <= width:
        return text
    while text and stringWidth(text + "…", font, size) > width:
        text = text[:-1]
    return text.rstrip() + "…"


def draw_chrome(c, doc, page, total):
    kind = doc.kinds.get(page, "body")
    if kind == "cover":
        draw_cover(c, doc, total)
        return
    meta = doc.meta
    section = "Contents" if kind == "toc" else next(
        (doc.sections[p] for p in sorted(doc.sections, reverse=True) if p <= page), "")
    c.saveState()
    y = PH - MT + 30
    c.setFont("Sans-Semi", 7.4)
    c.setFillColor(ACCENT)
    c.drawString(ML, y, "ARTZIMA")
    c.setFont("Sans", 7.4)
    c.setFillColor(MUTED)
    c.drawString(ML + stringWidth("ARTZIMA", "Sans-Semi", 7.4) + 4, y, "Disaster recovery")
    c.setFont("Sans-Medium", 7.6)
    c.setFillColor(INK)
    c.drawRightString(PW - MR, y, fit(section, "Sans-Medium", 7.6, TW * 0.62))
    c.setStrokeColor(RULE)
    c.setLineWidth(0.5)
    c.line(ML, y - 7, PW - MR, y - 7)
    fy = MB - 30
    c.line(ML, fy + 11, PW - MR, fy + 11)
    c.setFont("Sans", 7.2)
    c.setFillColor(MUTED)
    stamp = f"Printed {meta['date']}  ·  commit {meta['sha']}"
    c.drawString(ML, fy, stamp)
    if meta["dirty"]:
        c.setFont("Sans-Bold", 7.2)
        c.setFillColor(INK)
        c.drawString(ML + stringWidth(stamp, "Sans", 7.2) + 8, fy, "UNCOMMITTED CHANGES")
    c.setFont("Sans", 7.2)
    c.setFillColor(MUTED)
    tail = f" of {total}"
    c.drawRightString(PW - MR, fy, tail)
    c.setFont("Sans-Semi", 8.2)
    c.setFillColor(INK)
    c.drawRightString(PW - MR - stringWidth(tail, "Sans", 7.2), fy, f"Page {page}")
    if kind == "toc":
        c.setFont("Sans-Semi", 24)
        c.drawString(ML, PH - MT - 26, "Contents")
        c.setFont("Sans", 8.6)
        c.setFillColor(MUTED)
        c.drawString(ML, PH - MT - 42, "Total loss: phases 0 and A to F in order.  Smaller loss: start at 0.1, "
                                       "then the scenario P1 to P5.")
    c.restoreState()


def draw_cover(c, doc, total):
    meta = doc.meta
    pages = doc.anchors
    band = 4.05 * inch
    c.saveState()
    c.setFillColor(ACCENT)
    c.rect(0, PH - band, PW, band, stroke=0, fill=1)
    spaced(c, ML, PH - 1.15 * inch, "artzima homelab", "Sans-Semi", 9, 2.2, ACCENT_SOFT)
    c.setFillColor(WHITE)
    c.setFont("Sans-Bold", 50)
    c.drawString(ML - 2, PH - 1.95 * inch, "Disaster")
    c.drawString(ML - 2, PH - 2.65 * inch, "recovery")
    c.setFont("Sans-Light", 15)
    c.setFillColor(colors.HexColor("#E2ECF5"))
    c.drawString(ML, PH - 3.08 * inch, "Runbook and emergency-kit checklist, for the operator alone")
    c.setFont("Sans", 8.8)
    c.setFillColor(ACCENT_SOFT)
    info = f"Printed {meta['date']}   ·   commit {meta['sha']}   ·   {total} pages   ·   US Letter, print double-sided"
    c.drawString(ML, PH - band + 0.42 * inch, info)
    if meta["dirty"]:
        w = stringWidth("UNCOMMITTED CHANGES", "Sans-Bold", 8.4) + 14
        x = PW - MR - w
        c.setFillColor(WHITE)
        c.roundRect(x, PH - band + 0.36 * inch, w, 15, 3, stroke=0, fill=1)
        c.setFillColor(ACCENT)
        c.setFont("Sans-Bold", 8.4)
        c.drawCentredString(x + w / 2, PH - band + 0.36 * inch + 4.4, "UNCOMMITTED CHANGES")
    c.restoreState()

    # left: how to use
    top = PH - band - 0.5 * inch
    lw = 3.95 * inch
    c.saveState()
    c.setFillColor(INK)
    c.setFont("Sans-Semi", 13.5)
    c.drawString(ML, top, "How to use this guide")
    c.restoreState()

    def ref(key):
        return f'<font color="{HEX["muted"]}" size="8"> (p. {pages[key]})</font>' if key in pages else ""
    a = HEX["accent"]
    steps = [
        f"Start at <b>0. Decide</b>{ref('0-decide')}: what was lost decides the path. Everything: phases "
        f"<b>A to F</b> in order. Less: the scenario <b>P1 to P5</b> it names.",
        "Read a step to the end before you type. Tick its box when it is done and write the time next to it.",
        "Copy commands exactly; replace only <font face=\"Mono\">&lt;angle brackets&gt;</font>. A row that "
        f"starts with <font color=\"{a}\"><b>↪</b></font> continues the line above it: type both as one line.",
        "<b>Expect</b> says what you should see; <b>If this fails</b> says what to try. Stop at every "
        "<b>Caution</b>.",
        f"Compare the commit in the footer with the repository (B2{ref('b2-the-repository')}). If the "
        "repository is newer, follow it instead of the paper.",
    ]
    y = top - 16
    for n, s in enumerate(steps, 1):
        p = Paragraph(s, COVER_ITEM)
        _, h = p.wrap(lw - 26, 400)
        c.saveState()
        c.setFillColor(ACCENT)
        c.circle(ML + 8.5, y - 8, 8.5, stroke=0, fill=1)
        c.setFillColor(WHITE)
        c.setFont("Sans-Semi", 9.5)
        c.drawCentredString(ML + 8.5, y - 11.3, str(n))
        c.restoreState()
        p.drawOn(c, ML + 26, y - h + 1)
        y -= max(h, 18) + 10

    # right: where to start, with page numbers
    rx = ML + lw + 0.32 * inch
    rw = PW - MR - rx
    phases = [(tag, title, page) for lvl, tag, title, page in doc.openers if lvl == 0 and tag and tag in "0ABCDEF"]
    scen = [(tag, title, page) for lvl, tag, title, page in doc.openers if lvl == 1 and re.fullmatch(r"P\d+", tag)]
    extra = [(tag, title, page) for lvl, tag, title, page in doc.openers
             if lvl == 0 and not tag and title in ("Emergency kit", "Emergency kit checklist")]
    rows = [("h", "Total loss, in order")] + [("r", *x) for x in phases] + \
           [("h", "Partial loss")] + [("r", *x) for x in scen] + [("h", "Reference")] + [("r", "", *x[1:]) for x in extra]
    rh, hh = 15.5, 22
    box_h = sum(rh if r[0] == "r" else hh for r in rows) + 16
    c.saveState()
    c.setStrokeColor(RULE)
    c.setLineWidth(0.8)
    c.roundRect(rx, top + 14 - box_h - 10, rw, box_h + 10, 4, stroke=1, fill=0)
    c.setFillColor(INK)
    c.setFont("Sans-Semi", 13.5)
    c.drawString(rx + 12, top, "Where to start")
    y = top - 10
    for r in rows:
        if r[0] == "h":
            y -= hh
            spaced(c, rx + 12, y + 5, r[1], "Sans-Semi", 6.8, 1.2, ACCENT)
            continue
        _, tag, title, page = r
        y -= rh
        x = rx + 12
        if tag:
            c.setFillColor(ACCENT)
            c.setFont("Sans-Semi", 8.6)
            c.drawString(x, y + 4, tag)
        c.setFillColor(INK)
        c.setFont("Sans", 8.6)
        avail = rw - 12 - 22 - 30
        short = title if stringWidth(title, "Sans", 8.6) <= avail else title.split(":")[0]
        c.drawString(x + 22, y + 4, fit(short, "Sans", 8.6, avail))
        c.setFillColor(MUTED)
        c.drawRightString(rx + rw - 12, y + 4, str(page))
        c.setStrokeColor(RULE)
        c.setLineWidth(0.3)
        c.setDash(0.6, 2)
        c.line(x + 22 + stringWidth(fit(short, "Sans", 8.6, avail), "Sans", 8.6) + 4, y + 4.5,
               rx + rw - 14 - stringWidth(str(page), "Sans", 8.6), y + 4.5)
        c.setDash()
    c.restoreState()

    # the one rule, and where this paper belongs
    rule = Callout("warn", [Paragraph(
        "<b>The one rule:</b> a rebuilt, still-empty system must never back up over the last good snapshots. "
        f"The pause switches (D2{ref('d2-rebuild-switches--postgres-recovery-in-git-before-anything-runs')}) "
        f"stay on until F4{ref('f4-unpause-the-backups-only-after-f2-and-f3')}.", CALLOUT)])
    _, h = rule.wrap(TW, 200)
    rule.drawOn(c, ML, 1.3 * inch)
    p = Paragraph("<b>Private.</b> This printout holds the lab's real addresses. Keep it in the emergency kit, "
                  "away from home, with the recovery codes. Never commit, upload or e-mail it. Replace it after "
                  "every change to the runbook or the kit (<font face=\"Mono\">make dr-pdf</font>).", SMALL)
    _, ph = p.wrap(TW, 100)
    p.drawOn(c, ML, 0.62 * inch)


def canvas_class(doc):
    class Numbered(rl_canvas.Canvas):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self._pages = []

        def showPage(self):
            self._pages.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            total = len(self._pages)
            for n, state in enumerate(self._pages, 1):
                self.__dict__.update(state)
                draw_chrome(self, doc, n, total)
                super().showPage()
            super().save()
    return Numbered


def render(parts: list[str], meta: dict, out: pathlib.Path) -> int:
    """parts: Markdown documents (runbook, kit checklist). Returns the page count."""
    register_fonts()
    pages: dict[str, int] = {}
    for _ in range(4):    # page references in the text can move pages: rebuild until stable
        buf = io.BytesIO()
        doc = Doc(buf, meta)
        doc.multiBuild(Builder(pages).story(parts), canvasmaker=canvas_class(doc))
        if doc.anchors == pages:
            break
        pages = dict(doc.anchors)
    out.write_bytes(buf.getvalue())
    return max(doc.kinds) if doc.kinds else 0
