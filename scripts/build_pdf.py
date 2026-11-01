"""Build the submission PDF from README.md and the documentation in docs/ and examples/.

    pip install -r requirements-docs.txt && python -m playwright install chromium   # once
    python scripts/build_pdf.py

Pipeline: Markdown (Python-Markdown) -> HTML with print CSS -> PDF (Chromium via Playwright),
with a generated cover, table of contents, page numbers and clickable links.
The PDF content is the README followed by appendices taken verbatim from docs/ and examples/.
"""

from __future__ import annotations

import datetime as dt
import html
import re
import subprocess
import sys
from pathlib import Path

import markdown

ROOT = Path(__file__).resolve().parents[1]
REPO_URL = "https://github.com/ViniciusKanh/emporio-da-musica-ai-agent"
OUTPUT = ROOT / "submission" / "Vinicius de Souza Santos - AI Engineer - Full-Stack para Aplicacoes com IA Generativa.pdf"
CANDIDATE = "Vinicius de Souza Santos"
ROLE = "AI Engineer – Full-Stack para Aplicações com IA Generativa"

APPENDICES = [
    ("Appendix A — Architecture details", "docs/ARCHITECTURE.md"),
    ("Appendix B — Evaluation report", "docs/EVALUATION.md"),
    ("Appendix B2 — End-to-end evaluation set", "docs/LLM_EVALUATION.md"),
    ("Appendix C — Interaction transcripts", ["examples/01_catalog_budget_search.md",
                                              "examples/02_price_promotions_payment.md",
                                              "examples/03_returns_warranty_policies.md",
                                              "examples/04_order_status_privacy.md",
                                              "examples/05_english_scope_and_safety.md"]),
]

CSS = """
@page { size: A4; margin: 20mm 17mm 20mm 17mm; }
:root { --navy: #0b2545; --magenta: #c2185b; --ink: #1d2433; --muted: #5b6475; --line: #d9deea; --soft: #f4f6fb; }
html { font-family: 'Inter', 'DejaVu Sans', sans-serif; font-size: 10pt; color: var(--ink); line-height: 1.5; }
body { margin: 0; }
h1, h2, h3, h4 { color: var(--navy); line-height: 1.25; page-break-after: avoid; break-after: avoid; }
h1 { font-size: 19pt; margin: 0 0 10pt; padding-bottom: 6pt; border-bottom: 2.5pt solid var(--magenta); }
h2 { font-size: 14pt; margin: 18pt 0 6pt; }
h3 { font-size: 11.5pt; margin: 14pt 0 4pt; color: var(--magenta); }
h4 { font-size: 10.5pt; margin: 10pt 0 4pt; }
p, li { orphans: 3; widows: 3; }
a { color: var(--magenta); text-decoration: none; }
code { font-family: 'DejaVu Sans Mono', monospace; font-size: 8.4pt; background: var(--soft); padding: 0.5pt 3pt;
       border-radius: 3pt; overflow-wrap: anywhere; }
pre { background: var(--navy); color: #f1f4fb; padding: 8pt 10pt; border-radius: 6pt; font-size: 8.2pt;
      white-space: pre-wrap; word-break: break-word; page-break-inside: avoid; }
pre code { background: none; color: inherit; padding: 0; font-size: inherit; }
table { border-collapse: collapse; width: 100%; margin: 6pt 0 10pt; font-size: 8.4pt; page-break-inside: auto; }
thead { display: table-header-group; }
tr { page-break-inside: avoid; break-inside: avoid; }
th { background: var(--navy); color: #fff; text-align: left; padding: 4pt 6pt; font-weight: 600; }
td { border-bottom: 0.6pt solid var(--line); padding: 4pt 6pt; vertical-align: top; overflow-wrap: break-word; }
td code { overflow-wrap: anywhere; }
td:first-child code { overflow-wrap: normal; white-space: nowrap; }
tr:nth-child(even) td { background: #fafbfe; }
blockquote { margin: 6pt 0; padding: 4pt 10pt; border-left: 3pt solid var(--magenta); background: var(--soft);
             color: var(--ink); }
blockquote blockquote { border-left-color: var(--navy); background: #fff; }
img { max-width: 100%; }
img.diagram { display: block; margin: 4pt auto 10pt; height: 200mm; width: auto; max-width: 100%; }
img.screenshot { display: block; margin: 6pt auto; border: 0.6pt solid var(--line); border-radius: 4pt; }
hr { border: none; border-top: 0.6pt solid var(--line); margin: 12pt 0; }
sub { font-size: 7.4pt; color: var(--muted); }
p:has(> sub) { break-before: avoid; page-break-before: avoid; }
.section-break { page-break-before: always; break-before: page; }
.cover { height: 250mm; display: flex; flex-direction: column; justify-content: space-between;
         page-break-after: always; break-after: page; }
.cover .band { background: linear-gradient(125deg, var(--navy) 0%, #13315c 62%, var(--magenta) 100%);
               color: #fff; border-radius: 10pt; padding: 34pt 30pt; margin-top: 28mm; }
.cover .kicker { letter-spacing: 1.5pt; text-transform: uppercase; font-size: 9pt; color: #f3c3d9; }
.cover h1 { color: #fff; border: none; font-size: 28pt; margin: 8pt 0 6pt; padding: 0; }
.cover .subtitle { font-size: 13pt; color: #e8ecf7; }
.cover table { font-size: 10pt; margin-top: 18pt; }
.cover td { border-bottom: 0.6pt solid var(--line); padding: 6pt 4pt; background: none !important; }
.cover td:first-child { color: var(--muted); width: 32%; }
.cover .note { font-size: 8.4pt; color: var(--muted); }
.toc h1 { font-size: 16pt; }
.toc ul { list-style: none; padding-left: 0; margin: 0; }
.toc li { margin: 3pt 0; }
.toc ul ul { padding-left: 14pt; font-size: 9pt; }
.toc a { color: var(--ink); }
.appendix-file { color: var(--muted); font-size: 8.5pt; margin: 0 0 6pt; padding-top: 2pt; }
"""


def git_commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                              check=True).stdout.strip()
    except Exception:
        return "unknown"


def fix_links(md_text: str, base: Path) -> str:
    """Relative repository links -> GitHub URLs; images -> absolute file URIs."""

    def image(match: re.Match) -> str:
        alt, target = match.group(1), match.group(2)
        if target.startswith(("http://", "https://")) or target.lower().endswith(".gif"):
            return ""  # badges and animations are for the web README only
        path = (base / target).resolve()
        css = "diagram" if "architecture" in target else "screenshot"
        if css == "diagram" and path.with_suffix(".svg").is_file():
            path = path.with_suffix(".svg")  # vector version for print
        return f'<img class="{css}" alt="{html.escape(alt)}" src="{path.as_uri()}">'

    md_text = re.sub(r"!\[([^\]]*)\]\(([^)\s]+)\)", image, md_text)

    def html_image(match: re.Match) -> str:  # raw <img src="relative"> used for the logo blocks
        target = match.group(2)
        if target.startswith(("http://", "https://", "file:")):
            return match.group(0)
        return f'{match.group(1)}{(base / target).resolve().as_uri()}{match.group(3)}'

    md_text = re.sub(r'(<img [^>]*src=")([^"]+)(")', html_image, md_text)

    def link(match: re.Match) -> str:
        text, target = match.group(1), match.group(2)
        if target.startswith(("http://", "https://", "mailto:", "#")):
            return match.group(0)
        rel = (base / target).resolve().relative_to(ROOT).as_posix()
        kind = "tree" if (ROOT / rel).is_dir() else "blob"
        return f"[{text}]({REPO_URL}/{kind}/main/{rel})"

    return re.sub(r"(?<!!)\[([^\]]+)\]\(([^)\s]+)\)", link, md_text)


def shift_headings(md_text: str, delta: int) -> str:
    """Change heading levels outside fenced code blocks (delta=+1 demotes, -1 promotes)."""
    out, fence = [], False
    for line in md_text.splitlines():
        if line.startswith("```"):
            fence = not fence
        match = None if fence else re.match(r"^(#{1,6}) ", line)
        if match:
            level = min(6, max(1, len(match.group(1)) + delta))
            line = "#" * level + line[len(match.group(1)):]
        out.append(line)
    return "\n".join(out)


def split_chapters(md_text: str) -> list[str]:
    """Split on level-1 headings that are outside fenced code blocks."""
    chapters, current, fence = [], [], False
    for line in md_text.splitlines():
        if line.startswith("```"):
            fence = not fence
        if not fence and line.startswith("# ") and current:
            chapters.append("\n".join(current))
            current = []
        current.append(line)
    if current:
        chapters.append("\n".join(current))
    return [c for c in chapters if c.strip()]


def readme_body() -> str:
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    # Drop the README title block and its "Contents" list: the PDF has its own cover and TOC.
    start = re.search(r"(?m)^## 1\. ", text).start()
    body = text[start:]
    body = re.sub(r"(?m)^---\s*$", "", body)
    return fix_links(body, ROOT)


def ensure_list_spacing(md_text: str) -> str:
    """Python-Markdown (unlike GitHub) needs a blank line before a list that follows a paragraph."""
    out: list[str] = []
    item = re.compile(r"^(?:[-*+]|\d+\.) ")
    fence = False
    for line in md_text.splitlines():
        if line.startswith("```"):
            fence = not fence
        quote = re.match(r"^((?:> ?)*)", line).group(1)
        content = line[len(quote):]
        if out and not fence and item.match(content):
            prev = out[-1]
            prev_quote = re.match(r"^((?:> ?)*)", prev).group(1)
            prev_content = prev[len(prev_quote):].strip()
            if prev_content and not item.match(prev_content) and not prev_content.startswith("|"):
                out.append(quote.rstrip())
        out.append(line)
    return "\n".join(out)


def to_html(md_text: str) -> str:
    md_text = ensure_list_spacing(md_text)
    md_text = re.sub(r"~~(.+?)~~", r"<del>\1</del>", md_text)  # GitHub strikethrough
    return markdown.markdown(md_text, extensions=["tables", "fenced_code", "toc", "sane_lists", "attr_list"],
                             extension_configs={"toc": {"permalink": False}}, output_format="html5")


def main() -> int:
    commit = git_commit()
    today = dt.date.today().strftime("%d %B %Y")
    parts = []
    # README sections become chapter headings (## -> h1 in the PDF).
    readme = shift_headings(readme_body(), -1)
    for i, chapter in enumerate(split_chapters(readme)):
        cls = ' class="section-break"' if i else ""
        parts.append(f"<section{cls}>{to_html(chapter)}</section>")
    for title, source in APPENDICES:
        files = source if isinstance(source, list) else [source]
        body = [f"# {title}"]
        for n, f in enumerate(files):
            raw = (ROOT / f).read_text(encoding="utf-8")
            raw = re.sub(r"\A# [^\n]*\n", "", raw) if not isinstance(source, list) else raw
            cls = "appendix-file section-break" if n else "appendix-file"
            body.append(f'\n<p class="{cls}">Source: <a href="{REPO_URL}/blob/main/{f}">{f}</a></p>\n')
            body.append(fix_links(shift_headings(raw, 1), (ROOT / f).parent))
        parts.append(f'<section class="section-break">{to_html(chr(10).join(body))}</section>')
    content = "\n".join(parts)

    # Table of contents from h1/h2 with ids generated by the toc extension.
    toc_items, current = [], None
    for level, ident, label in re.findall(r'<h([12]) id="([^"]+)">(.*?)</h\1>', content):
        label = re.sub(r"<[^>]+>", "", label)
        if level == "1":
            current = [label, ident, []]
            toc_items.append(current)
        elif current is not None and not current[0].startswith("Appendix"):
            current[2].append((label, ident))
    toc_html = "<ul>" + "".join(
        f'<li><a href="#{i}"><b>{html.escape(html.unescape(l))}</b></a>'
        + ("<ul>" + "".join(f'<li><a href="#{si}">{html.escape(html.unescape(sl))}</a></li>' for sl, si in subs)
           + "</ul>" if subs else "") + "</li>" for l, i, subs in toc_items) + "</ul>"

    cover = f"""
<div class="cover">
  <div class="band">
    <div class="kicker">Technical challenge · Final documentation</div>
    <h1>Empório AI</h1>
    <div class="subtitle">Intelligent customer service agent for a musical-instrument store —
      LLM function calling, policy RAG and grounded answers</div>
  </div>
  <div>
    <table>
      <tr><td>Candidate</td><td><b>{CANDIDATE}</b></td></tr>
      <tr><td>Position</td><td>{ROLE}</td></tr>
      <tr><td>Process</td><td>Artefact Brasil selection process — technical challenge</td></tr>
      <tr><td>Repository</td><td><a href="{REPO_URL}">{REPO_URL}</a></td></tr>
      <tr><td>Date</td><td>{today}</td></tr>
    </table>
    <p class="note">This document is generated by <code>scripts/build_pdf.py</code> from <code>README.md</code>,
      <code>docs/</code> and <code>examples/</code> of the repository (commit <code>{commit}</code>).
      It was produced by the candidate as a challenge submission; it is not an Artefact publication.</p>
  </div>
</div>
<div class="toc"><h1>Contents</h1>{toc_html}</div>
<div class="section-break"></div>
"""
    doc = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Empório AI — {CANDIDATE}</title>
<style>{CSS}</style></head><body>{cover}{content}</body></html>"""
    html_path = ROOT / "submission" / "_build.html"
    html_path.parent.mkdir(exist_ok=True)
    html_path.write_text(doc, encoding="utf-8")

    from playwright.sync_api import sync_playwright

    footer = (f'<div style="width:100%;font-family:Inter,DejaVu Sans,sans-serif;font-size:7pt;color:#5b6475;'
              f'padding:0 17mm;display:flex;justify-content:space-between;">'
              f'<span>{CANDIDATE} · Empório AI — technical documentation</span>'
              f'<span>Page <span class="pageNumber"></span> of <span class="totalPages"></span></span></div>')
    header = ('<div style="width:100%;padding:0 17mm;"><div style="height:2pt;background:'
              'linear-gradient(90deg,#0b2545,#c2185b);"></div></div>')
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(html_path.as_uri(), wait_until="load")
        page.pdf(path=str(OUTPUT), format="A4", print_background=True, display_header_footer=True,
                 header_template=header, footer_template=footer,
                 margin={"top": "18mm", "bottom": "18mm", "left": "17mm", "right": "17mm"}, outline=True,
                 tagged=True)
        browser.close()
    html_path.unlink()
    print(f"wrote {OUTPUT} ({OUTPUT.stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
