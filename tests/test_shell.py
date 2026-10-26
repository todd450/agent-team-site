"""Tests for the page shell built in issue #4: index.html and tokens.css.

- The page parses as HTML with a header, placeholder sections and a footer.
- Every link on the page goes only to this repo's public files, issues and
  PRs, or to the site's own address (never into the private plugin repo or
  anywhere else).
- No colour or font is used on the page that is not named in tokens.css.
"""

import re
import unittest
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INDEX_HTML = ROOT / "index.html"
TOKENS_CSS = ROOT / "tokens.css"

ALLOWED_LINK_PREFIXES = (
    "https://github.com/todd450/agent-team-site",
    "https://todd450.github.io/agent-team-site",
)

LINK_ATTRS = {"a": "href", "link": "href", "img": "src", "script": "src", "source": "src",
              "iframe": "src"}


class PageParser(HTMLParser):
    """Collects the tag/attribute shape of the page so tests can inspect it."""

    def __init__(self):
        super().__init__()
        self.tags = []  # (tag, attrs dict) in document order
        self.links = []  # every href/src found on the page
        self._text_by_tag = {}
        self._stack = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.tags.append((tag, attrs))
        self._stack.append(tag)
        value = attrs.get(LINK_ATTRS.get(tag, ""))
        if value:
            self.links.append(value.strip())

    def handle_endtag(self, tag):
        if self._stack and self._stack[-1] == tag:
            self._stack.pop()

    def handle_data(self, data):
        if self._stack:
            current = self._stack[-1]
            self._text_by_tag.setdefault(current, []).append(data)


def parse(html):
    parser = PageParser()
    parser.feed(html)
    parser.close()
    return parser


def read(path):
    return path.read_text(encoding="utf-8")


class PageParsesTests(unittest.TestCase):
    def setUp(self):
        self.html = read(INDEX_HTML)
        self.page = parse(self.html)

    def test_files_exist(self):
        self.assertTrue(INDEX_HTML.is_file())
        self.assertTrue(TOKENS_CSS.is_file())

    def test_parser_reaches_the_end_without_error(self):
        # HTMLParser raises on malformed markup it cannot recover from; feeding
        # the whole document and closing it is enough to prove it parses.
        parse(self.html)

    def test_has_one_doctype_html_head_and_body(self):
        tags = [tag for tag, _ in self.page.tags]
        self.assertEqual(tags.count("html"), 1)
        self.assertEqual(tags.count("head"), 1)
        self.assertEqual(tags.count("body"), 1)
        self.assertTrue(self.html.strip().lower().startswith("<!doctype html>"))

    def test_links_the_one_token_source(self):
        stylesheet_links = [attrs.get("href") for tag, attrs in self.page.tags
                             if tag == "link" and attrs.get("rel") == "stylesheet"]
        self.assertEqual(stylesheet_links, ["tokens.css"])

    def test_header_has_name_headline_and_test_bed_paragraph(self):
        headers = [attrs for tag, attrs in self.page.tags if tag == "header"]
        self.assertEqual(len(headers), 1)
        h1_count = sum(1 for tag, _ in self.page.tags if tag == "h1")
        self.assertEqual(h1_count, 1)
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

    def test_every_link_is_relative_or_to_this_repo_or_its_pages_site(self):
        self.assertTrue(self.page.links, "expected at least one link on the page")
        for link in self.page.links:
            if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", link) and not link.startswith(("http://", "https://")):
                self.fail(f"link uses a disallowed scheme: {link!r}")
            if link.startswith(("http://", "https://")):
                self.assertTrue(
                    link.startswith(ALLOWED_LINK_PREFIXES),
                    f"link leaves this repo's public pages: {link!r}",
                )
            else:
                # A relative link must stay inside this site (no "../" escapes).
                self.assertFalse(link.startswith(".."), f"relative link escapes the site: {link!r}")

    def test_no_link_into_the_private_plugin_repo(self):
        for link in self.page.links:
            self.assertNotIn("founding-agents", link.lower())


class TokensOnlyTests(unittest.TestCase):
    def setUp(self):
        self.tokens_css = read(TOKENS_CSS)
        self.html = read(INDEX_HTML)
        self.style_block = self._extract_style_block(self.html)

    @staticmethod
    def _extract_style_block(html):
        match = re.search(r"<style[^>]*>(.*?)</style>", html, re.DOTALL)
        assert match, "index.html has no <style> block to check"
        return match.group(1)

    def _token_names(self):
        return set(re.findall(r"--([a-z0-9-]+)\s*:", self.tokens_css))

    def test_tokens_css_defines_colour_type_and_spacing(self):
        names = self._token_names()
        self.assertIn("ink", names)
        self.assertIn("paper", names)
        self.assertIn("font-text", names)
        self.assertIn("font-mono", names)
        self.assertIn("s4", names)

    def test_page_style_has_no_literal_colour(self):
        hex_colours = re.findall(r"#[0-9a-fA-F]{3,8}\b", self.style_block)
        self.assertEqual(hex_colours, [], f"literal colour(s) outside tokens.css: {hex_colours}")
        named = ("white", "black", "red", "green", "blue", "gray", "grey")
        for name in named:
            self.assertNotRegex(
                self.style_block, rf"(?i)[:\s]{name}\b",
                f"literal colour name used on the page: {name!r}",
            )

    def test_page_style_sets_no_literal_font_family(self):
        declarations = re.findall(r"font-family\s*:\s*([^;}]+)[;}]", self.style_block)
        self.assertTrue(declarations, "expected at least one font-family declaration")
        for declaration in declarations:
            self.assertRegex(
                declaration.strip(), r"^var\(--font-(text|mono)\)$",
                f"font-family is not one of tokens.css's font tokens: {declaration!r}",
            )

    def test_every_var_the_page_uses_is_defined_in_tokens_css(self):
        used = set(re.findall(r"var\(--([a-z0-9-]+)\)", self.style_block))
        defined = self._token_names()
        undefined = used - defined
        self.assertEqual(undefined, set(), f"uses var(...) not defined in tokens.css: {undefined}")


if __name__ == "__main__":
    unittest.main()
