#!/usr/bin/env python3
"""
Render markdown to HTML in ~/.claude/reports/, then open in browser.
Usage:
  cat report.md | python3 render-report.py "Report Title"
  python3 render-report.py "Report Title" < report.md

Uses marked.js CDN for rendering (network must be reachable).
File auto-named: <YYYYMMDD-HHMMSS>-<slug>.html
"""
import sys
import os
import re
import time
import subprocess
import html as html_lib
from pathlib import Path

REPORTS_DIR = Path.home() / ".claude" / "reports"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)


def slugify(text: str) -> str:
    text = re.sub(r"[^\w一-鿿\-]+", "-", text.strip())
    return text.strip("-")[:40] or "report"


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{title}</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/github-markdown-css@5.5.1/github-markdown-light.min.css">
<script src="https://cdn.jsdelivr.net/npm/marked@12/marked.min.js"></script>
<style>
  :root {{ color-scheme: light; }}
  body {{
    box-sizing: border-box;
    min-width: 200px;
    max-width: 980px;
    margin: 0 auto;
    padding: 45px 30px 80px 30px;
    font-family: -apple-system, BlinkMacSystemFont, "Helvetica Neue", sans-serif;
    background: #fafbfc;
  }}
  .topbar {{
    position: sticky; top: 0; z-index: 100;
    background: rgba(250,251,252,0.95);
    backdrop-filter: blur(8px);
    padding: 12px 0;
    border-bottom: 1px solid #e1e4e8;
    margin-bottom: 24px;
    display: flex; justify-content: space-between; align-items: center;
  }}
  .meta {{ font-size: 13px; color: #586069; }}
  .meta code {{ background: #f3f4f6; padding: 2px 6px; border-radius: 3px; font-size: 12px; }}
  .actions {{ display: flex; gap: 8px; }}
  .btn {{
    padding: 6px 14px; border: 1px solid #d1d5da; border-radius: 6px;
    background: white; color: #24292e; cursor: pointer; font-size: 13px;
  }}
  .btn:hover {{ background: #f3f4f6; }}
  .btn-danger {{ background: #d73a49; color: white; border-color: #d73a49; }}
  .btn-danger:hover {{ background: #cb2431; }}
  .markdown-body {{
    padding: 24px 32px;
    background: white;
    border: 1px solid #e1e4e8;
    border-radius: 8px;
  }}
  .markdown-body table {{ display: table; width: 100%; }}
  .markdown-body pre {{ background: #f6f8fa; }}
  .recycle-hint {{
    margin-top: 24px; padding: 16px;
    background: #fffbdd; border-left: 4px solid #f9c513;
    color: #735c0f; font-size: 13px; border-radius: 4px;
  }}
  .recycle-hint code {{ background: #f3f4f6; padding: 2px 6px; border-radius: 3px; }}
</style>
</head>
<body>
<div class="topbar">
  <div class="meta">
    <strong>{title}</strong> &nbsp;·&nbsp;
    <span>{ts}</span> &nbsp;·&nbsp;
    <code>{filename}</code>
  </div>
  <div class="actions">
    <button class="btn" onclick="window.print()">Print / Save PDF</button>
    <button class="btn btn-danger" onclick="if(confirm('Close this report?')){{window.close();}}">Close</button>
  </div>
</div>

<article id="content" class="markdown-body"></article>

<div class="recycle-hint">
  <strong>Done reading?</strong> Tell Claude Code: <code>cleanup report</code> or <code>cleanup all reports</code>.
</div>

<textarea id="md" style="display:none">{md_escaped}</textarea>
<script>
  marked.use({{ gfm: true, breaks: false }});
  document.getElementById('content').innerHTML = marked.parse(document.getElementById('md').value);
  document.title = "{title}";
</script>
</body>
</html>
"""


def main():
    title = sys.argv[1] if len(sys.argv) > 1 else "Report"
    md_content = sys.stdin.read()
    if not md_content.strip():
        print("Error: empty stdin", file=sys.stderr)
        sys.exit(1)

    ts = time.strftime("%Y%m%d-%H%M%S")
    slug = slugify(title)
    filename = f"{ts}-{slug}.html"
    fp = REPORTS_DIR / filename

    md_escaped = html_lib.escape(md_content)
    html = HTML_TEMPLATE.format(
        title=html_lib.escape(title),
        ts=time.strftime("%Y-%m-%d %H:%M:%S"),
        filename=filename,
        md_escaped=md_escaped,
    )

    fp.write_text(html, encoding="utf-8")

    # Open in default browser (macOS: open, Linux: xdg-open)
    opener = "/usr/bin/open" if sys.platform == "darwin" else "xdg-open"
    try:
        subprocess.run([opener, str(fp)], check=False, timeout=3)
    except Exception:
        pass

    print(str(fp))


if __name__ == "__main__":
    main()
