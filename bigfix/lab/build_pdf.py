# /// script
# requires-python = ">=3.11"
# dependencies = ["markdown"]
# ///
"""Build servermon-lab.pdf from the lab Markdown files and images.

Not part of the plugin: needs the third-party ``markdown`` package and a
Chromium-based browser (Chrome, Edge or Brave) for headless print-to-PDF.

    uv run bigfix/lab/build_pdf.py
    python bigfix/lab/build_pdf.py --out lab.pdf --browser /path/to/chrome
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import markdown

LAB_DIR = Path(__file__).resolve().parent
LAB_FILES = [
    "01-install.md",
    "02-find-devices.md",
    "03-console.md",
    "04-edit-toml.md",
    "05-pick-one.md",
    "troubleshooting.md",
]
# Relative repo links mean nothing inside a PDF, so point them at GitHub.
REPO_URL = "https://github.com/jgstew/bigfix-proxyagent-servermon/blob/master/"
BROWSERS = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    "google-chrome",
    "chromium",
    "chromium-browser",
    "msedge",
]
CSS = """
body{font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;font-size:10.5pt;
  line-height:1.45;color:#1f2328;margin:0}
h1{font-size:20pt;border-bottom:1px solid #d0d7de;padding-bottom:4px}
h2{font-size:14pt;margin-top:1.4em}
code{font-family:Consolas,Menlo,monospace;font-size:9pt;background:#f6f8fa;
  padding:1px 4px;border-radius:4px}
pre{background:#f6f8fa;padding:8px 10px;border-radius:6px;white-space:pre-wrap;
  page-break-inside:avoid}
pre code{padding:0;background:none}
table{border-collapse:collapse;margin:8px 0;page-break-inside:avoid}
th,td{border:1px solid #d0d7de;padding:4px 8px;vertical-align:top}
th{background:#f6f8fa}
blockquote{border-left:4px solid #0969da;margin:8px 0;padding:4px 12px;background:#f3f8ff}
img{max-width:100%;max-height:9cm;border:1px solid #d0d7de;page-break-inside:avoid;
  display:block;margin:6px 0}
a{color:#0969da;text-decoration:none}
.page{page-break-before:always}
@page{size:Letter;margin:1.8cm}
"""


def anchor(name):
    """Return the in-document anchor id for a lab file."""
    return "lab-" + name.removesuffix(".md")


def title(name):
    """Return the first H1 of a lab file, without the leading '# '."""
    return re.search(r"(?m)^# (.+)$", (LAB_DIR / name).read_text("utf-8")).group(1)


def clean(text):
    """Adapt one lab file's Markdown for a single combined document."""
    text = "\n".join(
        line for line in text.split("\n") if "[Lab index](README.md)" not in line
    ).strip()
    text = re.sub(r"\n---\s*$", "", text)
    # Links between lab files become jumps within the PDF.
    text = re.sub(
        r"\]\((0\d-[a-z-]+\.md|troubleshooting\.md)(#[^)]*)?\)",
        lambda m: "](" + (m.group(2) or "#" + anchor(m.group(1))) + ")",
        text,
    )
    text = text.replace("](README.md)", "](#top)")
    text = re.sub(r"\]\(\.\./\.\./([^)]*)\)", rf"]({REPO_URL}\1)", text)
    text = re.sub(r"\]\(\.\./content/([^)]*)\)", rf"]({REPO_URL}bigfix/content/\1)", text)
    text = text.replace("](images/", "](" + (LAB_DIR / "images").as_uri() + "/")
    # Python-Markdown needs 4-space list continuation; the labs use 3 (GitHub style).
    text = re.sub(r"(?m)^   (?=\S|  )", "    ", text)
    return text.replace("This file does not repeat", "This lab does not repeat")


def build_html():
    """Return the whole lab as one standalone HTML document."""
    intro = clean((LAB_DIR / "README.md").read_text("utf-8").split("**Labs**")[0])
    toc = "\n".join(f"1. [{title(f)}](#{anchor(f)})" for f in LAB_FILES)
    parts = [f'<a id="top"></a>\n\n{intro}\n\n**Contents**\n\n{toc}']
    for name in LAB_FILES:
        body = clean((LAB_DIR / name).read_text("utf-8"))
        parts.append(f'<div class="page" id="{anchor(name)}"></div>\n\n{body}')
    html = markdown.markdown(
        "\n\n".join(parts), extensions=["tables", "fenced_code", "md_in_html"]
    )
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>ServerMon BigFix Lab</title><style>{CSS}</style></head>"
        f"<body>{html}</body></html>"
    )


def find_browser(explicit):
    """Return a Chromium-based browser executable, or exit with an error."""
    for candidate in [explicit] if explicit else BROWSERS:
        if os.path.isfile(candidate) or shutil.which(candidate):
            return shutil.which(candidate) or candidate
    sys.exit("No Chrome/Edge/Brave found; pass --browser /path/to/browser")


def main():
    """Parse arguments, render HTML, and print it to PDF."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=LAB_DIR / "servermon-lab.pdf")
    parser.add_argument("--browser", help="path to a Chromium-based browser")
    args = parser.parse_args()
    browser = find_browser(args.browser)
    with tempfile.TemporaryDirectory() as tmp:
        page = Path(tmp) / "lab.html"
        page.write_text(build_html(), "utf-8")
        subprocess.run(
            [
                browser,
                "--headless",
                "--disable-gpu",
                "--no-pdf-header-footer",
                "--allow-file-access-from-files",
                f"--print-to-pdf={args.out.resolve()}",
                page.as_uri(),
            ],
            check=True,
            capture_output=True,
        )
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
