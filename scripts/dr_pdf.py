#!/usr/bin/env python3
"""Render the disaster-recovery runbook + the emergency-kit checklist into ONE printable PDF.

    make dr-pdf                          -> dist/artzima-disaster-recovery.pdf
    make dr-pdf DR_PDF_ARGS="--engine python"     force the built-in renderer

Every page's footer carries the print date and the git commit (plus "UNCOMMITTED CHANGES" if
the runbook or kit differ from that commit), so a stale printout is obvious: compare it with
`git log -1 --format='%h %cs' -- docs/disaster-recovery.md emergency-kit.yaml`.

Engines: `pandoc` with a LaTeX engine (xelatex/lualatex/pdflatex) or weasyprint, if installed;
otherwise (or with --engine python) a dependency-free renderer: plain monospace text, US Letter,
no images. Code lines that are too long continue on the next line, marked with "+ ".
"""

from __future__ import annotations

import argparse
import datetime
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parent.parent
RUNBOOK = REPO / "docs/disaster-recovery.md"
KIT = REPO / "emergency-kit.yaml"
OUT = REPO / "dist/artzima-disaster-recovery.pdf"


def git(*args: str) -> str:
    r = subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else ""


def stamp() -> str:
    sha = git("rev-parse", "--short", "HEAD") or "no commit yet"
    dirty = git("status", "--porcelain", "--", str(RUNBOOK), str(KIT))
    when = datetime.date.today().isoformat()
    return f"printed {when} | commit {sha}{' + UNCOMMITTED CHANGES' if dirty else ''}"


def source() -> str:
    kit = subprocess.run([sys.executable, str(REPO / "scripts/emergency_kit.py"), "--markdown"],
                         capture_output=True, text=True, check=True).stdout
    return RUNBOOK.read_text() + "\n\f\n" + kit


# ---------------------------------------------------------------- pandoc
def with_pandoc(md: str, footer: str, out: pathlib.Path) -> bool:
    if not shutil.which("pandoc"):
        return False
    md = md.replace("\n\f\n", "\n\n\\newpage\n\n")
    with tempfile.TemporaryDirectory() as tmp:
        src = pathlib.Path(tmp, "dr.md")
        for engine in ("xelatex", "lualatex", "pdflatex"):
            if not shutil.which(engine):
                continue
            src.write_text(md)
            hdr = pathlib.Path(tmp, "h.tex")
            safe = footer.replace("_", r"\_").replace("#", r"\#").replace("&", r"\&")
            hdr.write_text("\\usepackage{fancyhdr}\\pagestyle{fancy}\\fancyhf{}\n"
                           f"\\cfoot{{\\scriptsize artzima-iac disaster recovery | {safe} | page \\thepage}}\n"
                           "\\renewcommand{\\headrulewidth}{0pt}\n")
            r = subprocess.run(["pandoc", str(src), "-o", str(out), f"--pdf-engine={engine}", "-H", str(hdr),
                                "-V", "geometry:margin=2cm", "-V", "papersize=letter", "--toc"],
                               capture_output=True, text=True)
            if r.returncode == 0:
                return True
            print(f"pandoc/{engine} failed, trying the next engine:\n{r.stderr[-400:]}", file=sys.stderr)
        if shutil.which("weasyprint"):
            src.write_text(md.replace("\\newpage", '<div style="page-break-after: always"></div>'))
            css = pathlib.Path(tmp, "p.css")
            css.write_text("@page { size: letter; margin: 2cm; @bottom-center { font-size: 8pt; content: "
                           f'"artzima-iac disaster recovery | {footer} | page " counter(page) " of " counter(pages); }} }}'
                           " body { font-family: sans-serif; font-size: 10pt } pre, code { font-size: 8.5pt }")
            r = subprocess.run(["pandoc", str(src), "-o", str(out), "--pdf-engine=weasyprint", "--css", str(css),
                                "-s", "--metadata", "title=artzima disaster recovery"], capture_output=True, text=True)
            if r.returncode == 0:
                return True
            print(f"pandoc/weasyprint failed:\n{r.stderr[-400:]}", file=sys.stderr)
    return False


# ---------------------------------------------------------------- built-in renderer
ASCII = {"→": "->", "←": "<-", "↔": "<->", "≈": "~", "≥": ">=", "≤": "<=", "…": "...", "—": "--", "–": "-",
         "“": '"', "”": '"', "‘": "'", "’": "'", "✓": "v", "✗": "x", "•": "*", "−": "-", "⚠": "!"}
W, H, MARGIN, SIZE, LEAD = 612, 792, 42, 8.5, 10.4
COLS = int((W - 2 * MARGIN) / (SIZE * 0.6))


def plain(text: str) -> str:
    text = re.sub(r"\[([^\]]+)\]\((#[^)]*)\)", r"\1", text)          # in-document links: text only
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1 (\2)", text)      # other links: text (url)
    text = re.sub(r"<(https?://[^>]+)>", r"\1", text)
    text = text.replace("**", "").replace("`", "")
    text = re.sub(r"<a id=\"[^\"]*\"></a>", "", text)
    return "".join(ASCII.get(ch, ch) for ch in text)


def wrap(line: str, code: bool) -> list[str]:
    if len(line) <= COLS:
        return [line]
    if code:
        out = [line[:COLS]]
        rest = line[COLS:]
        while rest:
            out.append("+ " + rest[:COLS - 2])
            rest = rest[COLS - 2:]
        return out
    indent = len(line) - len(line.lstrip())
    hang = " " * (indent + (2 if line.lstrip()[:2] in ("- ", "* ", "[ ") or re.match(r"\d+\. ", line.lstrip()) else 0))
    words, out, cur = line.split(" "), [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > COLS:
            out.append(cur)
            cur = hang + w
        else:
            cur = f"{cur} {w}" if cur else w
    out.append(cur)
    return [o[:COLS] for o in out]


def layout(md: str) -> list[list[tuple[str, str]]]:
    """Markdown -> pages of (font, text) lines. font: R regular, B bold."""
    rows: list[tuple[str, str]] = []
    code = False
    for raw in md.split("\n"):
        if raw == "\f":
            rows.append(("PAGE", ""))
            continue
        if raw.strip().startswith("```"):
            code = not code
            continue
        if code:
            rows += [("R", "    " + x) for x in wrap("".join(ASCII.get(c, c) for c in raw), True)]
            continue
        line = plain(raw)
        if re.match(r"^\s*\|?\s*:?-{3,}", line) and set(line.strip()) <= set("|-: "):
            continue                                              # table separator row
        if line.lstrip().startswith("|"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            line = "  " + " | ".join(cells)
        m = re.match(r"^(#{1,4})\s+(.*)", line)
        if m:
            title = m.group(2).upper() if len(m.group(1)) <= 2 else m.group(2)
            rows += [("R", "")] + [("B", x) for x in wrap(title, False)]
            if len(m.group(1)) <= 2:
                rows.append(("B", "=" * min(len(title), COLS)))
            continue
        if line.strip() == "---":
            rows.append(("R", "-" * COLS))
            continue
        line = re.sub(r"^(\s*)- \[ \]", r"\1[ ]", line)
        rows += [("R", x) for x in wrap(line, False)]
    per_page = int((H - 2 * MARGIN - 20) / LEAD)
    pages, cur = [], []
    for font, text in rows:
        if font == "PAGE" or len(cur) >= per_page:
            if cur:
                pages.append(cur)
            cur = []
            if font == "PAGE":
                continue
        if not cur and not text:
            continue
        cur.append((font, text))
    if cur:
        pages.append(cur)
    return pages


def esc(s: str) -> bytes:
    s = s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    return s.encode("cp1252", "replace")


def with_python(md: str, footer: str, out: pathlib.Path) -> None:
    pages = layout(md)
    objs: list[bytes] = []                     # object n is objs[n-1]
    objs.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objs.append(b"")                           # pages, filled below
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier /Encoding /WinAnsiEncoding >>")
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier-Bold /Encoding /WinAnsiEncoding >>")
    kids = []
    for n, page in enumerate(pages, 1):
        y = H - MARGIN
        stream = [b"BT"]
        for font, text in page:
            stream.append(b"/F%d %.1f Tf 1 0 0 1 %d %.1f Tm (" % (1 if font == "R" else 2, SIZE, MARGIN, y)
                          + esc(text) + b") Tj")
            y -= LEAD
        foot = f"artzima-iac disaster recovery | {footer} | page {n}/{len(pages)}"
        stream.append(b"/F1 7 Tf 1 0 0 1 %d %d Tm (" % (MARGIN, MARGIN - 18) + esc(foot) + b") Tj")
        stream.append(b"ET")
        body = b"\n".join(stream)
        objs.append(b"<< /Length %d >>\nstream\n" % len(body) + body + b"\nendstream")
        content = len(objs)
        objs.append(b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %d %d] /Contents %d 0 R "
                    b"/Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> >>" % (W, H, content))
        kids.append(len(objs))
    objs[1] = b"<< /Type /Pages /Count %d /Kids [%s] >>" % (len(kids), b" ".join(b"%d 0 R" % k for k in kids))
    pdf = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for i, o in enumerate(objs, 1):
        offsets.append(len(pdf))
        pdf += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(pdf)
    pdf += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    pdf += b"".join(b"%010d 00000 n \n" % off for off in offsets)
    pdf += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    out.write_bytes(bytes(pdf))
    print(f"{len(pages)} pages (built-in renderer)")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--engine", choices=["auto", "pandoc", "python"], default="auto")
    ap.add_argument("--out", type=pathlib.Path, default=OUT)
    args = ap.parse_args()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    md, footer = source(), stamp()
    if args.engine in ("auto", "pandoc") and with_pandoc(md, footer, args.out):
        print("rendered with pandoc")
    elif args.engine == "pandoc":
        sys.exit("pandoc (with xelatex/lualatex/pdflatex or weasyprint) not available")
    else:
        with_python(md, footer, args.out)
    print(f"wrote {args.out.relative_to(REPO) if args.out.is_relative_to(REPO) else args.out}  ({footer})")
    print("Print it double-sided and put it in the emergency kit; replace older printouts.")
    os.chmod(args.out, 0o644)


if __name__ == "__main__":
    main()
