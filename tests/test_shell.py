"""Tests for the page shell built in issue #4: index.html and tokens.css.

- The page parses as HTML with a header, placeholder sections and a footer,
  with every tag properly nested and closed.
- Every link on the page goes only to this repo's public files, issues and
  PRs, or to the site's own address (never into the private plugin repo or
  anywhere else, and never a root-relative path that leaves the project
  site's subdirectory on GitHub Pages).

No test here checks that colours or fonts come only from tokens.css: doing
that correctly means parsing CSS declaration values rather than grepping
text, which is a CSS linter and is beyond this issue's appetite.
"""

import re
import unittest
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INDEX_HTML = ROOT / "index.html"
TOKENS_CSS = ROOT / "tokens.css"

ALLOWED_LINK_PREFIXES = (
    "https://github.com/todd450/founding-agents-site",
    "https://todd450.github.io/founding-agents-site",
)

LINK_ATTRS = {"a": "href", "link": "href", "img": "src", "script": "src", "source": "src",
              "iframe": "src"}

# Elements that never get a matching end tag, so they must not be pushed
# onto the open-tag stack used to check that every tag closes.
VOID_ELEMENTS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
}


class PageParser(HTMLParser):
    """Collects the tag/attribute shape of the page so tests can inspect it."""

    def __init__(self):
        super().__init__()
        self.tags = []  # (tag, attrs dict) in document order
        self.links = []  # every href/src found on the page
        self.text_by_tag = {}  # tag name -> list of text chunks found inside it
        self.stack = []  # open, non-void tags waiting for their end tag
        self.mismatched_end_tags = []  # end tags that didn't match the top of the stack

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.tags.append((tag, attrs))
        if tag not in VOID_ELEMENTS:
            self.stack.append(tag)
        value = attrs.get(LINK_ATTRS.get(tag, ""))
        if value:
            self.links.append(value.strip())

    def handle_endtag(self, tag):
        if tag in VOID_ELEMENTS:
            # A self-closing void tag such as <br/> or <img .../> makes
            # HTMLParser's default handle_startendtag() call handle_endtag()
            # right after handle_starttag(), but void elements are never
            # pushed onto the stack above -- there is no end tag to match,
            # and valid HTML does not require one.
            return
        if self.stack and self.stack[-1] == tag:
            self.stack.pop()
        else:
            self.mismatched_end_tags.append(tag)

    def handle_data(self, data):
        if self.stack:
            current = self.stack[-1]
            self.text_by_tag.setdefault(current, []).append(data)


def parse(html):
    parser = PageParser()
    parser.feed(html)
    parser.close()
    return parser


def read(path):
    return path.read_text(encoding="utf-8")


class FilesExistTests(unittest.TestCase):
    """Kept in its own class, with no setUp that reads the files, so this
    test can actually fail on its own if a file goes missing rather than
    always being pre-empted by another test's setUp."""

    def test_files_exist(self):
        self.assertTrue(INDEX_HTML.is_file())
        self.assertTrue(TOKENS_CSS.is_file())


class PageParsesTests(unittest.TestCase):
    def setUp(self):
        self.html = read(INDEX_HTML)
        self.page = parse(self.html)

    def test_has_one_doctype_html_head_and_body(self):
        tags = [tag for tag, _ in self.page.tags]
        self.assertEqual(tags.count("html"), 1)
        self.assertEqual(tags.count("head"), 1)
        self.assertEqual(tags.count("body"), 1)
        self.assertTrue(self.html.strip().lower().startswith("<!doctype html>"))

    def test_page_tags_are_all_properly_nested_and_closed(self):
        # Every non-void start tag must be matched by an end tag in the
        # right place: nothing left open, and no end tag that closes the
        # wrong element.
        self.assertEqual(self.page.mismatched_end_tags, [],
                          f"mismatched end tag(s): {self.page.mismatched_end_tags}")
        self.assertEqual(self.page.stack, [], f"unclosed tag(s): {self.page.stack}")

    def test_links_the_one_token_source(self):
        stylesheet_links = [attrs.get("href") for tag, attrs in self.page.tags
                             if tag == "link" and attrs.get("rel") == "stylesheet"]
        self.assertEqual(stylesheet_links, ["tokens.css"])

    def test_header_has_name_headline_and_test_bed_paragraph(self):
        headers = [attrs for tag, attrs in self.page.tags if tag == "header"]
        self.assertEqual(len(headers), 1)
        h1_count = sum(1 for tag, _ in self.page.tags if tag == "h1")
        self.assertEqual(h1_count, 1)
        headline_text = "".join(self.page.text_by_tag.get("h1", [])).strip()
        self.assertTrue(headline_text, "the h1 headline has no text")
        self.assertIn("test bed", self.html)
        self.assertIn("not a launch", self.html)

    def test_footer_present_and_explains_status(self):
        footer_count = sum(1 for tag, _ in self.page.tags if tag == "footer")
        self.assertEqual(footer_count, 1)
        self.assertIn("GitHub Pages", self.html)

    def test_placeholder_sections_for_later_issues_are_present_and_empty(self):
        section_classes = ["band", "costs", "flow", "limits", "measures", "record", "question"]
        for name in section_classes:
            match = re.search(
                rf'<section class="{name}"[^>]*>(.*?)</section>', self.html, re.DOTALL)
            self.assertIsNotNone(match, f"no placeholder section for {name!r}")
            body = re.sub(r"<!--.*?-->", "", match.group(1), flags=re.DOTALL)
            body = re.sub(r"<h2[^>]*>.*?</h2>", "", body, flags=re.DOTALL)
            self.assertEqual(body.strip(), "",
                              f"section {name!r} should be an empty placeholder")


class LinksAllowedTests(unittest.TestCase):
    def setUp(self):
        self.page = parse(read(INDEX_HTML))

    @staticmethod
    def _matches_allowed_prefix(link):
        # A prefix match is only real if the next character is a path,
        # query or fragment separator (or the link ends there) -- otherwise
        # "...founding-agents-site" also matches the unrelated, non-existent
        # "...founding-agents-site-evil".
        for prefix in ALLOWED_LINK_PREFIXES:
            if not link.startswith(prefix):
                continue
            rest = link[len(prefix):]
            if rest == "" or rest[0] in "/?#":
                return True
        return False

    def test_every_link_is_relative_or_to_this_repo_or_its_pages_site(self):
        self.assertTrue(self.page.links, "expected at least one link on the page")
        for link in self.page.links:
            if link.startswith("//"):
                self.fail(f"protocol-relative link leaves this site: {link!r}")
            if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", link) and not link.startswith(("http://", "https://")):
                self.fail(f"link uses a disallowed scheme: {link!r}")
            if link.startswith(("http://", "https://")):
                self.assertTrue(
                    self._matches_allowed_prefix(link),
                    f"link leaves this repo's public pages: {link!r}",
                )
            else:
                # A relative link must stay inside this site: no "../"
                # escape, and no leading "/" either -- this site is served
                # from a project subdirectory on GitHub Pages
                # (todd450.github.io/founding-agents-site/), so a
                # root-relative link such as "/about" resolves to
                # todd450.github.io/about, outside the site entirely, and
                # 404s on the nightly link check.
                self.assertFalse(link.startswith(".."), f"relative link escapes the site: {link!r}")
                self.assertFalse(link.startswith("/"),
                                  f"root-relative link leaves the project site: {link!r}")

    # No separate "not the private plugin repo" test: the allowlist above
    # already requires every absolute link to match one of
    # ALLOWED_LINK_PREFIXES exactly, with a boundary check on the character
    # that follows, so a link into the private repo (todd450/founding-agents,
    # without "-site") can never pass it. A standalone substring check for
    # "founding-agents" would now be a false positive on this repo's own,
    # legitimate address -- founding-agents-site contains that substring.


STYLE_BLOCK_RE = re.compile(r"<style[^>]*>(.*?)</style>", re.DOTALL)
CSS_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
DECLARATION_RE = re.compile(r"([a-zA-Z-]+)\s*:\s*([^;{}]+)")


class TokensOnlyTests(unittest.TestCase):
    def setUp(self):
        self.tokens_css = read(TOKENS_CSS)
        self.html = read(INDEX_HTML)
        self.page = parse(self.html)
        self.style_text = self._collect_style_text(self.html, self.page)
        self.declarations = DECLARATION_RE.findall(self.style_text)

    @staticmethod
    def _collect_style_text(html, page):
        # Every <style> block (not just the first) plus every style=
        # attribute found anywhere on the page -- those are the only two
        # places CSS can live on this page.
        blocks = STYLE_BLOCK_RE.findall(html)
        style_attrs = [attrs["style"] for _, attrs in page.tags if attrs.get("style")]
        text = "\n".join(blocks + style_attrs)
        # Strip comments so a comment that happens to contain a colour word
        # (or anything else that looks like a declaration) can't trip the
        # checks below -- only real declarations are inspected.
        return CSS_COMMENT_RE.sub("", text)

    def _token_names(self):
        return set(re.findall(r"--([a-z0-9-]+)\s*:", self.tokens_css))

    def test_tokens_css_defines_colour_type_and_spacing(self):
        names = self._token_names()
        self.assertIn("ink", names)
        self.assertIn("paper", names)
        self.assertIn("font-text", names)
        self.assertIn("font-mono", names)
        self.assertIn("s4", names)

    def test_every_var_the_page_uses_is_defined_in_tokens_css(self):
        # Match the token name whether the call is var(--name) or has a
        # fallback, var(--name, literal) -- a fallback's name must still
        # exist in tokens.css, not just the call's syntax.
        used = set(re.findall(r"var\(--([a-z0-9-]+)\s*[,)]", self.style_text))
        defined = self._token_names()
        undefined = used - defined
        self.assertEqual(undefined, set(), f"uses var(...) not defined in tokens.css: {undefined}")

    def test_page_style_defines_no_custom_property(self):
        # tokens.css is the one place custom properties may be defined. Any
        # "--name:" declaration in the page's own <style> blocks or style=
        # attributes -- even inside a :root block -- is a second token
        # source that can silently out-cascade tokens.css.
        custom_props = [prop for prop, _ in self.declarations if prop.strip().startswith("--")]
        self.assertEqual(
            custom_props, [],
            f"page style defines its own custom propert(y/ies), a second token source: {custom_props}",
        )


if __name__ == "__main__":
    unittest.main()
