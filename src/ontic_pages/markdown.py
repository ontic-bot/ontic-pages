"""Markdown to safe HTML, for updates people write in Markdown instead of HTML.

markdown-it-py (CommonMark) with tables, ~~strike~~, bare links, task lists (`- [ ]`),
footnotes, heading ids, and math: `$...$` inline and `$$...$$` as a block, turned into MathML on
the server (latex2mathml), so the page stays static HTML that browsers draw without script.

Raw HTML in the source is shown as text, never passed through, and markdown-it refuses
javascript:, vbscript:, file: and data: addresses (but data: images). The MathML is checked
too: only MathML elements and plain attributes are kept. An image whose address ends in a video
extension becomes a <video> with controls.
"""

from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from urllib.parse import unquote

from latex2mathml.converter import convert
from markdown_it import MarkdownIt
from mdit_py_plugins.anchors import anchors_plugin
from mdit_py_plugins.dollarmath import dollarmath_plugin
from mdit_py_plugins.footnote import footnote_plugin
from mdit_py_plugins.tasklists import tasklists_plugin

VIDEO_EXTENSIONS = (".mp4", ".webm", ".mov", ".m4v", ".ogv")
SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
MATHML = "http://www.w3.org/1998/Math/MathML"
MATH_TAGS = frozenset(
    "math mrow mi mn mo ms mtext mspace msub msup msubsup munder mover munderover mfrac msqrt "
    "mroot mstyle merror mpadded mphantom mfenced menclose mtable mtr mtd mlabeledtr "
    "mmultiscripts mprescripts none semantics annotation".split()
)
MATH_ATTRIBUTES = frozenset(
    "display mathvariant mathcolor mathbackground mathsize accent accentunder stretchy fence "
    "separator separators lspace rspace linethickness columnalign rowalign columnspacing "
    "rowspacing columnlines rowlines frame framespacing displaystyle scriptlevel width height "
    "depth notation movablelimits largeop symmetric minsize maxsize form voffset open close "
    "columnspan rowspan align equalrows equalcolumns".split()
)
STRAY_AMPERSAND = re.compile(r"&(?!#x?[0-9a-fA-F]+;|[a-zA-Z]+;)")
TABLE = '<div class="table">'
ET.register_namespace("", MATHML)


def escape(text: str) -> str:
    return html.escape(text, quote=True)


def math(latex: str, block: bool) -> str:
    """LaTeX as MathML, only MathML elements and plain attributes kept; the source as code when
    it does not convert."""
    try:
        source = latex.replace("<", r"\lt ").replace(">", r"\gt ")
        out = convert(source, display="block" if block else "inline")
        root = ET.fromstring(STRAY_AMPERSAND.sub("&amp;", out))
        for node in root.iter():
            namespace, _, tag = node.tag[1:].partition("}")
            if namespace != MATHML or tag not in MATH_TAGS:
                raise ValueError(f"not MathML: {node.tag}")
            for name in list(node.attrib):
                if name not in MATH_ATTRIBUTES:
                    del node.attrib[name]
        return ET.tostring(root, encoding="unicode")
    except Exception:  # noqa: BLE001 (any LaTeX it cannot read shows as written)
        code = f'<code class="math-error">{escape(latex)}</code>'
        return f"<p>{code}</p>" if block else code


def is_video(url: str) -> bool:
    path = re.split(r"[?#]", url, maxsplit=1)[0].lower()
    return path.endswith(VIDEO_EXTENSIONS)


def _image(self, tokens, idx, options, env):
    token = tokens[idx]
    src = token.attrGet("src") or ""
    alt = self.renderInlineAsText(token.children or [], options, env)
    # Local media can follow the gateway's direct-delivery redirects without tainting canvas.
    # External media keeps its existing request mode; its server may not support CORS.
    cors = (
        ' crossorigin="anonymous"'
        if src and not SCHEME.match(src) and not src.startswith("//")
        else ""
    )
    if is_video(src):
        return (
            f'<video controls preload="metadata" playsinline src="{escape(src)}"{cors}>'
            f'<a href="{escape(src)}">{escape(alt or src)}</a></video>'
        )
    title = token.attrGet("title")
    extra = f' title="{escape(title)}"' if title else ""
    return f'<img src="{escape(src)}" alt="{escape(alt)}" loading="lazy"{extra}{cors}>'


def _parser() -> MarkdownIt:
    md = MarkdownIt("commonmark", {"html": False, "linkify": True, "typographer": False})
    md.enable(["table", "strikethrough", "linkify"])
    md.use(dollarmath_plugin, allow_space=True, allow_digits=False, double_inline=True,
           renderer=lambda content, info: math(content, info["display_mode"]))  # fmt: skip
    md.use(tasklists_plugin)
    md.use(footnote_plugin)
    md.use(anchors_plugin, min_level=1, max_level=3)
    md.add_render_rule("image", _image)
    # Tables scroll sideways on a phone.
    md.add_render_rule("table_open", lambda r, t, i, o, e: TABLE + r.renderToken(t, i, o, e))
    md.add_render_rule("table_close", lambda r, t, i, o, e: r.renderToken(t, i, o, e) + "</div>")
    return md


MD = _parser()


def render(text: str, title: str = "") -> str:
    """Markdown to an HTML fragment (the body of a page). When the text opens with a level-1
    heading that is `title`, the page shows it already, so it is left out here."""
    text = text or ""
    if title:
        tokens = MD.parse(text)
        first = tokens[0] if tokens else None
        if first and first.type == "heading_open" and first.tag == "h1" and first.map:
            if _text(tokens[1]).strip() == title.strip():
                lines = text.splitlines(keepends=True)
                text = "".join(lines[: first.map[0]] + lines[first.map[1] :])
    return MD.render(text)


def _text(token) -> str:
    return "".join(c.content for c in (token.children or []) if c.type in ("text", "code_inline"))


def title_of(text: str) -> str:
    """The text of the first level-1 heading, else of the first heading, else ""."""
    tokens = MD.parse(text or "")
    first = ""
    for i, token in enumerate(tokens):
        if token.type == "heading_open" and i + 1 < len(tokens):
            words = _text(tokens[i + 1]).strip()
            if token.tag == "h1" and words:
                return words
            first = first or words
    return first


def _walk(tokens):
    for token in tokens:
        yield token
        if token.children:
            yield from _walk(token.children)


def media_refs(text: str) -> list[str]:
    """The relative paths that images and links point to, in order and once each: the files
    next to a Markdown file that belong with it. Addresses with a scheme, absolute paths,
    anchors and paths that leave the folder are left out."""
    refs: list[str] = []
    for token in _walk(MD.parse(text or "")):
        url = token.attrGet("src") if token.type == "image" else None
        if token.type == "link_open":
            url = token.attrGet("href")
        if not url or SCHEME.match(url) or url.startswith(("/", "#")):
            continue
        path = unquote(re.split(r"[?#]", url, maxsplit=1)[0])
        parts = [p for p in path.split("/") if p not in ("", ".")]
        if not parts or ".." in parts or "\\" in path:
            continue
        path = "/".join(parts)
        if path not in refs:
            refs.append(path)
    return refs


PAGE_CSS = """
:root { color-scheme: light dark; --bg: #fbfbfa; --text: #1f1f1e; --muted: #6b6b68;
  --line: #e4e4e1; --code: #f1f1ee; --accent: #2f5bd3; }
@media (prefers-color-scheme: dark) {
  :root { --bg: #1d1d1c; --text: #ececea; --muted: #a3a39f; --line: #353533; --code: #2a2a28;
    --accent: #8aa8ff; }
}
* { box-sizing: border-box; }
body { margin: 0; padding: 32px 16px 64px; background: var(--bg); color: var(--text);
  font: 16px/1.6 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
main { max-width: 760px; margin: 0 auto; overflow-wrap: anywhere; }
h1 { font-size: 28px; line-height: 1.25; margin: 0 0 6px; }
h2 { font-size: 21px; line-height: 1.3; margin: 32px 0 10px; }
h3 { font-size: 17px; margin: 24px 0 8px; }
h4, h5, h6 { font-size: 15px; margin: 20px 0 6px; }
.byline { color: var(--muted); font-size: 14px; margin: 0 0 28px; }
p, ul, ol, blockquote, pre, .table { margin: 0 0 16px; }
li > ul, li > ol { margin: 4px 0; }
li.task-list-item { list-style: none; }
li.task-list-item input { margin: 0 6px 0 -22px; }
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
img, video { display: block; max-width: 100%; height: auto; margin: 8px 0; border-radius: 8px; }
video { background: #000; }
code { font: 0.88em/1.5 ui-monospace, SFMono-Regular, Menlo, monospace; background: var(--code);
  padding: 0.1em 0.35em; border-radius: 4px; }
pre { background: var(--code); padding: 12px 14px; border-radius: 8px; overflow-x: auto; }
pre code { background: none; padding: 0; overflow-wrap: normal; }
blockquote { margin-left: 0; padding: 2px 0 2px 14px; border-left: 3px solid var(--line);
  color: var(--muted); }
.table { overflow-x: auto; }
table { border-collapse: collapse; font-size: 15px; }
th, td { border: 1px solid var(--line); padding: 6px 10px; vertical-align: top;
  overflow-wrap: normal; }
th { background: var(--code); font-weight: 600; }
hr { border: 0; border-top: 1px solid var(--line); margin: 28px 0; }
del { color: var(--muted); }
math[display="block"] { display: block; margin: 12px 0; overflow-x: auto; }
math { font-size: 1.05em; }
code.math-error { color: #b3261e; }
.footnotes { font-size: 14px; color: var(--muted); }
"""


def page_html(title: str, body: str, byline: str = "") -> str:
    """A whole page for a rendered update: the title, a byline when given, then the body (HTML
    from render()). Styles inline, no script, nothing loaded from elsewhere."""
    e = escape
    by = f'<p class="byline">{e(byline)}</p>\n' if byline else ""
    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<meta name="generator" content="ontic-pages markdown">
<title>{e(title)}</title>
<style>{PAGE_CSS}</style>
</head><body><main>
<h1>{e(title)}</h1>
{by}{body}
</main></body></html>
"""
