"""Markdown updates: markdown-it with math as MathML, raw HTML shown as text, safe addresses,
videos, and the helpers the publish path uses."""

import time

import pytest

from ontic_pages.markdown import math, media_refs, page_html, render, title_of


def test_the_usual_markdown():
    out = render(
        "# Title\n\nSome *em*, **strong**, ~~gone~~ and `code`.\n\n"
        "- one\n  - nested\n- [x] done\n\n1. first\n2. second\n\n> quoted\n\n"
        "```python\nprint(1)\n```\n\n| a | b |\n|:--|--:|\n| 1 | 2 |\n\n---\n"
        'See https://example.org and [docs](notes/a.html "t").\n\nText[^1]\n\n[^1]: A note.\n'
    )
    for part in (
        '<h1 id="title">Title</h1>', "<em>em</em>", "<strong>strong</strong>", "<s>gone</s>",
        "<code>code</code>", "<ul>", "<ol>", "<blockquote>", 'class="language-python"',
        '<div class="table"><table>', 'style="text-align:right"', "<hr />",
        '<a href="https://example.org">https://example.org</a>',
        '<a href="notes/a.html" title="t">docs</a>', 'class="task-list-item-checkbox" checked',
        'class="footnotes',
    ):  # fmt: skip
        assert part in out, part


def test_math_becomes_mathml():
    out = render("Energy $E = mc^2$ here.\n\n$$\n\\frac{a}{b} \\sum_{i=1}^N x_i\n$$\n")
    assert '<math xmlns="http://www.w3.org/1998/Math/MathML" display="inline">' in out
    assert "<msup><mi>c</mi><mn>2</mn></msup>" in out
    assert 'display="block"' in out and "<mfrac>" in out and "<munderover>" in out
    # Prices are not math; LaTeX it cannot read is shown as written.
    assert "<math" not in render("It costs $5 and $10.")
    assert math("\\frac{", block=True) == '<p><code class="math-error">\\frac{</code></p>'
    assert "<mo>&lt;</mo>" in render("$a < b$")  # a less-than sign, not markup


@pytest.mark.parametrize(
    "source",
    [
        "$\\text{<script>alert(1)</script>}$",
        "$\\href{javascript:alert(1)}{x}$",
        '$\\color{red" onmouseover="alert(1)}x$',
        "$\\style{background:url(x)}{y}$",
        "$\\text{a&b}$",
    ],
)
def test_math_cannot_carry_html(source):
    out = render(source)
    assert "<script" not in out and "href" not in out and 'onmouseover="' not in out
    assert "style=" not in out


def test_raw_html_and_bad_addresses_stay_text():
    out = render(
        "<script>alert(1)</script>\n\n<img src=x onerror=alert(1)>\n\n"
        "[a](javascript:alert(1)) [b](vbscript:x) ![c](javascript:x) [d](mailto:a@b.io)"
    )
    assert "<script>" not in out and "&lt;script&gt;" in out
    assert "<img src=x" not in out and "javascript:" not in out.split("[a]")[0]
    assert 'href="javascript' not in out and 'src="javascript' not in out
    assert 'href="vbscript' not in out
    assert '<a href="mailto:a@b.io">d</a>' in out


def test_videos():
    out = render("![run 3](media/run.MP4?x=1) ![plot](media/plot.png)")
    assert (
        '<video controls preload="metadata" playsinline src="media/run.MP4?x=1" '
        'crossorigin="anonymous">' in out
    )
    assert '<a href="media/run.MP4?x=1">run 3</a></video>' in out
    assert '<img src="media/plot.png" alt="plot" loading="lazy" crossorigin="anonymous">' in out


def test_title_of():
    assert title_of("intro\n\n## Sub\n\n# The *real* one\n") == "The real one"
    assert title_of("## Only a second level") == "Only a second level"
    assert title_of("```\n# not a heading\n```\n") == ""
    assert title_of("") == ""


def test_media_refs():
    text = (
        "![a](img/a.png) ![a again](./img/a.png) [doc](notes/b%20c.pdf#p2) ![v](clip.mp4?t=1)\n"
        "[web](https://x.org) ![abs](/etc/x.png) [up](../secret.png) [anchor](#top)\n"
        "`![in code](no.png)`\n"
    )
    assert media_refs(text) == ["img/a.png", "notes/b c.pdf", "clip.mp4"]


def test_the_title_is_not_shown_twice():
    assert "<h1" not in render("# Week 41\n\nBody", "Week 41")
    assert '<h1 id="other">Other</h1>' in render("# Other\n\nBody", "Week 41")
    assert "<h1" in render("Intro\n\n# Week 41", "Week 41")  # only an opening heading


def test_page_html():
    page = page_html("A <b>title</b>", render("body"), "me@onticlabs.io, 12.10.2026 18:00")
    assert page.startswith("<!doctype html>") and "<script" not in page
    assert "<title>A &lt;b&gt;title&lt;/b&gt;</title>" in page
    assert '<p class="byline">me@onticlabs.io, 12.10.2026 18:00</p>' in page
    assert '<meta name="generator" content="ontic-pages markdown">' in page


@pytest.mark.parametrize(
    "source",
    [
        "```\n" + "x\n" * 5000,
        "- a\n" + "".join("  " * i + "- a\n" for i in range(300)),
        "*" * 20000,
        "_a" * 10000,
        "[" * 10000 + "a" + "](" * 5000,
        "$" * 10000,
        "$$" + "\\frac{" * 2000,
    ],
)
def test_nasty_inputs_are_quick(source):
    start = time.monotonic()
    render(source)
    assert time.monotonic() - start < 3


def test_external_media_keeps_its_request_mode():
    out = render("![external](https://example.com/a.png) ![v](//example.com/a.mp4)")
    assert "crossorigin=" not in out


def test_preview_media_preserves_credentials_and_needs_no_bucket_cors():
    source = "![plot](media/plot.png) ![clip](media/clip.mp4)"
    preview = render(source, media_cors=False)
    assert "<img" in preview and "<video" in preview
    assert "crossorigin" not in preview
    # Per-render options cannot leak between concurrent preview and publish calls.
    assert render(source).count('crossorigin="anonymous"') == 2
