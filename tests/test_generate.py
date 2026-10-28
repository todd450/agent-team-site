"""Tests for the generator that writes this site's committed pages (issue #28).

From this issue on, `index.html` is generated output: `npm run build` writes it
and nobody edits it by hand. It is written from three sources, all in the repo:

- `templates/` -- `layout.html` (head, masthead, footer), one file per page in
  `templates/pages/`, and one partial per fact-driven section;
- `content/copy/en/common.json` and `content/copy/en/<page>.json` -- every
  user-facing string, by key;
- `content/facts.json` -- the reviewed facts snapshot issue #27 commits.

`content/site.json` holds the site's address and title once, so the footer and
the later deploy issues derive from one value.

What the cases here pin:

- The committed page *is* the generated page, byte for byte, and a hand edit to
  it is a failure that names the file -- which is what makes the diff a
  reviewer reads the bytes a reader gets.
- Two runs write byte-identical output, so a build with nothing new in it
  produces an empty diff.
- Every page template is written to its matching output path, so a later page
  costs a template and not a generator change.
- Neither a copy-key typo nor a dead string survives the build.
- Every value in a copy file is a string the page shows, and a value of any
  other shape is refused in a sentence naming the file, the key and the value.
- A place the build cannot read from or write to is refused in a sentence
  naming that place, and a page it could not write is left exactly as it was.
  Every refusal here is a sentence: a stack trace is a failure of this suite,
  however wrong the thing that caused it was.
- The facts drive the pages: a row added appears in position order with the
  date the page dates it by, and a row removed disappears from the whole site.
  Which page a kind of fact lands on is part of what the build decides, so each
  kind is read off the page that carries it -- the record's entries are on
  `record/index.html` from issue #29 on, and its own rules are in
  `tests/test_record.py`.
- The newest record entry is the one marked "this build", by construction, so
  the page cannot carry two of them.
- An empty list, one item and far more than the page carries today all render
  (`fixtures/empty-and-one`).
- A fact's two inline forms -- `[text](url)` and backticked `code` -- become a
  link and a literal, and everything else in a fact is escaped, so no row in
  the store can put markup on the page.
- The generator makes no network call: it reads the committed snapshot and
  nothing else.

Nothing here pins today's wording: every case either compares the page against
the sources it was generated from or works on a snapshot of its own.
"""

import json
import re
import shutil
import subprocess
import tempfile
import unittest
from html import unescape
from html.parser import HTMLParser
from pathlib import Path

# The kinds and their columns, as the puller defines them -- the shape the
# generator is written against. Asked through node, so this file holds no
# second copy of it (tests/rules.py).
from rules import kinds, links_in
# The pages this site serves and the rules every one of them keeps, so the
# case for a second page asks the same questions of it as the suite asks of
# the committed page (tests/pages.py).
from pages import link_problems, links_on, served_from, served_pages

ROOT = Path(__file__).resolve().parent.parent
GENERATE_JS = ROOT / "scripts" / "generate.js"
CHECK_SH = ROOT / "check.sh"
PACKAGE_JSON = ROOT / "package.json"
CONTENT = ROOT / "content"
TEMPLATES = ROOT / "templates"
SNAPSHOT = CONTENT / "facts.json"
SITE_JSON = CONTENT / "site.json"
INDEX_HTML = ROOT / "index.html"
VERCEL_JSON = ROOT / "vercel.json"
EMPTY_AND_ONE = ROOT / "fixtures" / "empty-and-one"

NODE = shutil.which("node")

KINDS = kinds()

# Where each kind's rows are rendered, and what one row of it looks like on the
# page. One entry per kind, so a kind added to the snapshot has to be given a
# home here before these pass.
#
# `page` is the page the site serves that kind on. The record has a page of its
# own from issue #29 (D6 and P6, decided on #14), so "the facts drive the page"
# is asked of whichever page a kind lands on rather than of the home page.
#
# `date` is where the page shows that kind's dates: a step and a record entry
# carry their own row's date, a measure's rows are headed by one dated column
# for the table, and a cost writes its date into its own `source` text.
#
# `as_class` names the columns the page shows as a class rather than as words --
# a measure's standing is the colour its figure is drawn in -- so a case asking
# what the reader reads does not look for them in the text.
ROWS = {
    "status_steps": {
        "page": "index.html",
        "section": "band",
        "row": re.compile(r'<li class="step">.*?</li>', re.DOTALL),
        "date": "row",
        "as_class": ("standing",),
    },
    "costs": {
        "page": "index.html",
        "section": "costs",
        "row": re.compile(r'<div class="ev">.*?</div>', re.DOTALL),
        "date": "source",
        "as_class": (),
    },
    "measures": {
        "page": "index.html",
        "section": "measures",
        "row": re.compile(r"<tr\b[^>]*>.*?</tr>", re.DOTALL),
        "date": "column",
        "as_class": ("standing",),
    },
    "record_entries": {
        "page": "record/index.html",
        "section": "record",
        "row": re.compile(r"<li\b[^>]*>.*?</li>", re.DOTALL),
        "date": "row",
        "as_class": (),
    },
}

TAG_RE = re.compile(r"<[^>]+>")
HREF_RE = re.compile(r'href="([^"]*)"')

# What a refusal may not look like: a line of stack frames, or the name of a
# Node error class thrown straight out of the build. Either one means the
# generator met something it did not expect and said nothing about it.
STACK_FRAME_RE = re.compile(r"^\s+at \S", re.MULTILINE)
THROWN_RE = re.compile(r"\b(?:Type|Range|Reference|Assertion)Error\b|^\s*Error: ", re.MULTILINE)


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def dump(path, value):
    Path(path).write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def section(html, name):
    match = re.search(rf'<section class="{name}"[^>]*>(.*?)</section>', html, re.DOTALL)
    assert match is not None, f'no <section class="{name}"> on the generated page'
    return match.group(1)


def rows_of(site, kind):
    """Every row of this kind on the page that carries it, in document order.

    `site` is every page a build wrote, by output path, because which page a
    kind lands on is part of what the build decides: the record's entries are
    on record/index.html and nowhere else.
    """
    shape = ROWS[kind]
    page = shape["page"]
    assert page in site, f"the build wrote no {page}, which is where {kind} goes"
    body = section(site[page], shape["section"])
    if kind == "measures":
        body = re.search(r"<tbody>(.*?)</tbody>", body, re.DOTALL).group(1)
    return shape["row"].findall(body)


def text_of(markup):
    """One line of the text a reader sees. The tags come out with no space put
    in their place, because an inline tag sits inside a sentence: the space
    between two words is the template's own newline or the sentence's. The
    entities come back as the characters they stand for, because that is what
    the reader reads."""
    return " ".join(unescape(TAG_RE.sub("", markup)).split())


def shown_columns(kind, columns, index):
    """The columns of this kind the page shows as words, in the row itself."""
    shown = [column for column in columns[2:] if column not in ROWS[kind]["as_class"]]
    if kind == "record_entries" and index == 0:
        # The newest entry's mark is the generator's, in place of its own
        # headline: only one entry is ever this build.
        shown = [column for column in shown if column != "headline"]
    return shown


class Elements(HTMLParser):
    """The elements a browser's own parser finds in a fragment, with their
    attributes -- so a case about escaping is answered by a parser and not by a
    regular expression that reads the same fragment some other way."""

    def __init__(self, html):
        super().__init__()
        self.tags = []  # (tag, attrs) in document order
        self.feed(html)
        self.close()

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


def when_of(rendered_record_entry):
    """The dated line above a record entry."""
    return re.search(r'<span class="when">(.*?)</span>', rendered_record_entry,
                     re.DOTALL).group(1)


def plain(value):
    """A fact's text as a reader sees it: the two inline forms unwrapped and
    nothing else touched."""
    return re.sub(r"`([^`\n\r]*)`", r"\1",
                  re.sub(r"\[([^[\]]*)\]\(([^()]*)\)", r"\1", value))


# The one column of each kind the page renders as a fact's text is read -- its
# links and its literals -- and where on the page that column lands. Used to
# read a fact back off the page and compare it with the snapshot's own words.
RICH = {
    "status_steps": ("detail",
                     re.compile(r'<span class="step-check">(.*)</span>\s*</li>', re.DOTALL)),
    "costs": ("claim",
              re.compile(r'<p class="ev-claim"><strong>.*?</strong> (.*)</p>', re.DOTALL)),
    "measures": ("label", re.compile(r"<td>(.*?)</td>", re.DOTALL)),
    "record_entries": ("entry", re.compile(r"<p>(.*)</p>", re.DOTALL)),
}

RENDERED_LINK_RE = re.compile(r'<a href="([^"]*)">(.*?)</a>', re.DOTALL)
RENDERED_CODE_RE = re.compile(r'<span class="num">(.*?)</span>', re.DOTALL)


def unrender(markup):
    """A fact's text as it was written, taken back out of the page: the two
    inline forms wrapped up again and every entity back to its character."""
    return unescape(RENDERED_CODE_RE.sub(r"`\1`", RENDERED_LINK_RE.sub(r"[\2](\1)", markup)))


class GeneratorTestCase(unittest.TestCase):
    """Runs the generator, and nothing else: no test here reaches the network,
    and the only snapshot any of them reads is one in this repo or one it wrote
    itself."""

    @classmethod
    def setUpClass(cls):
        if NODE is None:  # pragma: no cover - CI and check.sh both have Node
            raise unittest.SkipTest("node is not installed")
        cls.assertTrue(GENERATE_JS.is_file(), f"no generator at {GENERATE_JS}")

    @classmethod
    def assertTrue(cls, value, message):  # noqa: N802 - used before an instance exists
        if not value:
            raise AssertionError(message)

    def tmpdir(self):
        return Path(self.enterContext(tempfile.TemporaryDirectory()))

    def sources(self):
        """A copy of the repo's generator sources, to edit and build from."""
        root = self.tmpdir()
        shutil.copytree(CONTENT, root / "content")
        shutil.copytree(TEMPLATES, root / "templates")
        return root

    def run_generator(self, *args, root=None, out=None, snapshot=None, check=False):
        command = [NODE, str(GENERATE_JS), *args]
        if root is not None:
            command += ["--root", str(root)]
        if out is not None:
            command += ["--out", str(out)]
        if snapshot is not None:
            command += ["--snapshot", str(snapshot)]
        if check:
            command.append("--check")
        return subprocess.run(command, capture_output=True, text=True, timeout=120,
                              cwd=str(ROOT))

    def build(self, root=None, out=None, snapshot=None):
        """Build the pages and return {output path: text}."""
        if out is None:
            out = self.tmpdir()
        result = self.run_generator(root=root, out=out, snapshot=snapshot)
        self.assertEqual(result.returncode, 0, f"the build failed: {result.stderr}")
        written = [line for line in result.stdout.splitlines() if line.strip()]
        self.assertTrue(written, "the generator wrote no page at all")
        return {name: (Path(out) / name).read_text(encoding="utf-8") for name in written}

    def refuse(self, root, fragment, snapshot=None, out=None):
        """Assert the build fails and says why in a sentence, and return the
        reason."""
        if out is None:
            out = self.tmpdir()
        result = self.run_generator(root=root, out=out, snapshot=snapshot)
        self.assertNotEqual(result.returncode, 0,
                            f"this build should have been refused, and wrote: {result.stdout}")
        self.assertIn(fragment, result.stderr)
        self.assert_a_sentence(result.stderr)
        return result.stderr

    def assert_a_sentence(self, reason):
        """A refusal is a sentence whoever is editing the sources can act on.

        The only people who ever run this build are the founder editing a
        heading and whoever is changing a template, so a raw Node error thrown
        straight out -- the stack of a TypeError, an EACCES from the write --
        names neither the file nor the remedy. However wrong the thing that
        caused it was, a stack trace with no sentence in it is a failure.
        """
        self.assertTrue(reason.startswith("generate:"),
                        f"a refusal says `generate: <what is wrong>` and this said: {reason}")
        self.assertNotRegex(reason, STACK_FRAME_RE,
                            f"this refusal carries a stack trace instead of a sentence: {reason}")
        self.assertNotRegex(reason, THROWN_RE,
                            f"this refusal is a raw Node error, not a sentence: {reason}")

    def page(self, root=None, snapshot=None):
        """The home page as the generator writes it."""
        return self.build(root=root, snapshot=snapshot)["index.html"]

    def site(self, root=None, snapshot=None):
        """Every page the generator writes, by output path -- what a reader of
        the whole site gets, which is more than one page from issue #29 on."""
        return self.build(root=root, snapshot=snapshot)


class CommittedPageTests(GeneratorTestCase):
    """The committed page is the generated page -- which is the whole of P4."""

    def test_the_committed_page_is_what_the_generator_writes(self):
        for name, text in self.build(root=ROOT).items():
            with self.subTest(page=name):
                self.assertEqual(text, (ROOT / name).read_text(encoding="utf-8"),
                                 f"{name} is not what `npm run build` writes: run it and commit "
                                 f"the result -- the generated pages are committed so the diff a "
                                 f"reviewer reads is the bytes a reader gets")

    def test_a_fresh_build_over_the_committed_sources_leaves_every_page_alone(self):
        result = self.run_generator(check=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("index.html", result.stdout,
                      "the generator does not name the pages it is responsible for")

    def test_a_hand_edit_to_a_generated_page_is_refused_and_names_the_file(self):
        root = self.sources()
        self.build(root=root, out=root)
        edited = root / "index.html"
        edited.write_text(edited.read_text(encoding="utf-8").replace("</body>", "<p>by hand</p></body>"),
                          encoding="utf-8")
        result = self.run_generator(root=root, out=root, check=True)
        self.assertNotEqual(result.returncode, 0,
                            "a hand-edited page must fail the check that CI runs")
        self.assertIn("index.html", result.stderr,
                      "the failure must name the file that was edited")

    def test_two_runs_write_byte_identical_output(self):
        first, second = self.build(root=ROOT), self.build(root=ROOT)
        self.assertEqual(first, second,
                         "a build with nothing new in it must produce an empty diff")

    def test_check_sh_runs_the_generator_so_a_stale_page_cannot_merge(self):
        # The check above is only worth anything if the one command CI runs
        # actually runs it.
        self.assertRegex(CHECK_SH.read_text(encoding="utf-8"),
                         r"generate\.js.*--check|--check.*generate\.js")

    def test_npm_run_build_is_the_one_way_to_write_the_pages(self):
        scripts = load(PACKAGE_JSON)["scripts"]
        self.assertEqual(scripts.get("build"), "node scripts/generate.js")
        self.assertEqual(scripts.get("facts:pull"), "node scripts/pull-facts.js")

    def test_the_generator_adds_no_npm_dependency(self):
        package = load(PACKAGE_JSON)
        self.assertNotIn("dependencies", package,
                         "the npm packages are for the checks only; the generator is Node's "
                         "standard library and nothing else")


class DeployedOutputTests(GeneratorTestCase):
    """The deploy serves the directory the build writes the pages into.

    A repo with no build script is uploaded as it stands; one with a build
    script is built and then served from an output directory, which defaults
    to `public/` -- a directory this site does not have and will not have,
    because the pages are committed at the repo root (P4, decided on #14).
    So the repo pins the output directory itself, rather than leaving it to a
    dashboard nobody reviews (decided by the founder on #28).
    """

    def test_the_deploy_serves_the_directory_the_build_writes_the_pages_into(self):
        self.assertIn("build", load(PACKAGE_JSON)["scripts"],
                      "this case is about what a build script does to the deploy")
        self.assertTrue(VERCEL_JSON.is_file(),
                        "package.json declares a build script, so the deploy builds the repo and "
                        "then serves one directory: vercel.json is where this repo says which, "
                        "and there is no vercel.json")
        output = load(VERCEL_JSON).get("outputDirectory")
        self.assertIsNotNone(output, "vercel.json does not say which directory the deploy serves")
        served = (ROOT / output).resolve()
        self.assertEqual(served, ROOT.resolve(),
                         f"the pages are committed at the repo root, and the deploy serves "
                         f"{output!r}")
        for name in self.build(root=ROOT):
            with self.subTest(page=name):
                self.assertTrue((served / name).is_file(),
                                f"the deploy serves {output!r}, which does not hold {name}")


class OutputPathTests(GeneratorTestCase):
    """Every template in templates/pages/ is written to its matching output
    path, so a page added later costs a template and not a generator change."""

    def test_the_index_template_is_written_to_the_repo_root(self):
        self.assertIn("index.html", self.build(root=ROOT))

    def test_any_other_page_template_is_written_to_its_own_directory(self):
        root = self.sources()
        (root / "templates" / "pages" / "extra.html").write_text(
            '  <section class="extra">\n    <h2>{{extra.heading}}</h2>\n  </section>\n',
            encoding="utf-8")
        dump(root / "content" / "copy" / "en" / "extra.json", {
            "headline": "An extra page",
            "lede": "Its own wording, in its own file.",
            "extra.heading": "Extra",
        })
        pages = self.build(root=root)
        self.assertIn("extra/index.html", pages)
        self.assertIn("Extra", pages["extra/index.html"])

    def test_a_page_template_with_no_copy_file_of_its_own_is_refused(self):
        root = self.sources()
        (root / "templates" / "pages" / "extra.html").write_text(
            '  <section class="extra"><h2>{{extra.heading}}</h2></section>\n', encoding="utf-8")
        self.refuse(root, "extra")


class PlaceItCannotReadOrWriteTests(GeneratorTestCase):
    """A place the build cannot read from or write to is refused in a sentence
    naming that place.

    The build writes files, so the ways it fails are the filesystem's: a page
    left read-only by a checkout or an editor, an output path that is not a
    directory, a directory it may not write into, a source directory that is
    not there. Each one has to arrive as the place and the reason, because the
    remedy is a chmod or a path and the person running it cannot guess which.
    Nothing is half-written either way: a page it could not write is left
    exactly as it was.
    """

    @staticmethod
    def writable(path):
        """Whether this user can in fact write there, asked by writing."""
        try:
            if path.is_dir():
                probe = path / "probe"
                probe.write_text("", encoding="utf-8")
                probe.unlink()
            else:
                with path.open("a", encoding="utf-8"):
                    pass
            return True
        except OSError:
            return False

    def unwritable(self, path, mode=0o444):
        """Make a path read-only for the rest of this test, and put it back
        afterwards so the scratch directory can be cleaned up."""
        was = path.stat().st_mode & 0o777
        self.addCleanup(path.chmod, was)
        path.chmod(mode)
        if self.writable(path):  # pragma: no cover - a user the filesystem never refuses
            raise unittest.SkipTest(f"{path} is still writable at mode {mode:o}: this case needs "
                                    f"a user the filesystem says no to, and root never hears no")
        return path

    def with_a_second_page(self, root):
        """A second page, so a build has a directory to make as well as a file
        to write."""
        (root / "templates" / "pages" / "extra.html").write_text(
            '  <section class="extra">\n    <h2>{{extra.heading}}</h2>\n  </section>\n',
            encoding="utf-8")
        dump(root / "content" / "copy" / "en" / "extra.json", {
            "headline": "An extra page",
            "lede": "Its own wording, in its own file.",
            "extra.heading": "Extra",
        })
        return root

    def test_a_page_it_cannot_write_is_refused_naming_the_page(self):
        root = self.sources()
        out = self.tmpdir()
        written = self.build(root=root, out=out)
        page = self.unwritable(out / "index.html")
        reason = self.refuse(root, "index.html", out=out)
        self.assertIn(str(page), reason,
                      "the refusal must name the place it could not write to")
        self.assertEqual(page.read_text(encoding="utf-8"), written["index.html"],
                         "a build that could not write a page must leave it as it was")

    def test_an_output_path_that_is_not_a_directory_is_refused_naming_it(self):
        root = self.sources()
        out = self.tmpdir() / "not-a-directory"
        out.write_text("I am a file.\n", encoding="utf-8")
        reason = self.refuse(root, str(out), out=out)
        self.assertIn("index.html", reason, "the refusal must name the page it was writing")
        self.assertEqual(out.read_text(encoding="utf-8"), "I am a file.\n")

    def test_an_output_directory_it_cannot_write_into_is_refused_naming_it(self):
        root = self.with_a_second_page(self.sources())
        out = self.unwritable(self.tmpdir(), 0o555)
        reason = self.refuse(root, str(out), out=out)
        self.assertEqual(sorted(path.name for path in out.iterdir()), [],
                         f"nothing should have been written into {out}")
        self.assertIn("extra", reason, "the refusal must name the directory it could not make")

    def test_a_source_directory_it_cannot_read_is_refused_naming_it(self):
        root = self.sources()
        shutil.rmtree(root / "templates" / "pages")
        self.refuse(root, "templates/pages")


class EveryServedPageTests(GeneratorTestCase):
    """Every page a build writes is held to this site's page rules, not just
    the one page there is today.

    The convention this issue sets up is that a later page costs a template
    and a copy file. A check that reads `index.html` by name would let the
    first page added after it carry a link nothing ever looked at -- a scheme
    that runs in this site's origin, or an address off this site -- because
    the committed page is, truthfully, the generated page.
    """

    def site_with_a_second_page(self, body):
        """A copy of this repo's sources with an extra page whose wording is
        `body`, built where the generator puts it."""
        root = self.sources()
        (root / "templates" / "pages" / "extra.html").write_text(
            '  <section class="extra">\n    <h2>{{extra.heading}}</h2>\n'
            "    <p>{{&extra.body}}</p>\n  </section>\n", encoding="utf-8")
        dump(root / "content" / "copy" / "en" / "extra.json", {
            "headline": "An extra page",
            "lede": "Its own wording, in its own file.",
            "extra.heading": "Extra",
            "extra.body": body,
        })
        self.build(root=root, out=root)
        return root

    def test_the_pages_the_suite_checks_are_the_pages_the_generator_writes(self):
        root = self.site_with_a_second_page("Nothing to see.")
        served = served_pages(root)
        self.assertIn("extra/index.html", served,
                      "the pages this suite checks are a list of names kept by hand, so a page "
                      "added later is checked by nothing")
        self.assertEqual(sorted(served), sorted(self.build(root=root)),
                         "the pages the suite checks are not the pages the build writes")

    def test_a_link_this_site_may_not_carry_is_caught_on_a_page_that_is_not_index(self):
        root = self.site_with_a_second_page(
            "A [beacon](http://evil.test/grab?c=1) and a [trap](javascript:void 0).")
        found = {name: link_problems(name, links_on((root / name).read_text(encoding="utf-8")))
                 for name in served_pages(root)}
        self.assertEqual(found["index.html"], [],
                         "the home page itself carries a link this site may not carry")
        self.assertEqual(len(found["extra/index.html"]), 2,
                         f"a second page's off-site link and its javascript: link are not both "
                         f"refused: {found['extra/index.html']!r}")

    def test_a_relative_link_that_climbs_out_of_the_site_is_caught_on_that_page(self):
        root = self.site_with_a_second_page("A [way out](../../elsewhere/) here.")
        page = "extra/index.html"
        problems = link_problems(page, links_on((root / page).read_text(encoding="utf-8")))
        self.assertEqual(len(problems), 1,
                         f"a link that climbs past the site's root from {page} is not refused: "
                         f"{problems!r}")

    def test_a_relative_link_is_judged_from_the_page_it_is_written_on(self):
        # A page served from a directory of its own reaches the stylesheets
        # and the home page by climbing out of that directory, and lands
        # inside the site; the same addresses written on the home page land
        # above it. So where a relative link goes is asked of the page that
        # carries it, not of the characters it starts with.
        inside = ["../", "../tokens.css"]
        self.assertEqual(link_problems("extra/index.html", inside), [],
                         "a page in its own directory cannot reach the site's root")
        self.assertEqual(len(link_problems("index.html", inside)), len(inside),
                         "the home page is the site's root, and these leave the site")

    def test_an_asset_a_second_page_loads_is_resolved_from_that_page_s_own_url(self):
        # A page at <name>/index.html is served from its own directory, so a
        # relative asset on it does not resolve to the repo root. Which file a
        # page asks for is the question the asset checks ask, so they ask it
        # of the page's own URL.
        root = self.site_with_a_second_page("Nothing to see.")
        self.assertEqual(served_from("extra/index.html", "tokens.css"), "extra/tokens.css")
        self.assertEqual(served_from("index.html", "tokens.css"), "tokens.css")


class CopyKeyTests(GeneratorTestCase):
    """Every user-facing string is a key, and the build is what says so:
    neither a typo nor a dead string survives it."""

    def test_a_key_a_template_calls_and_nothing_defines_is_refused(self):
        root = self.sources()
        copy = load(root / "content" / "copy" / "en" / "index.json")
        called = next(key for key in copy if key.startswith("limits."))
        copy.pop(called)
        dump(root / "content" / "copy" / "en" / "index.json", copy)
        self.refuse(root, called)

    def test_a_key_defined_and_called_by_nothing_is_refused(self):
        for name in ("common.json", "index.json"):
            with self.subTest(copy=name):
                root = self.sources()
                copy = load(root / "content" / "copy" / "en" / name)
                copy["a.string.nobody.shows"] = "Written and never read."
                dump(root / "content" / "copy" / "en" / name, copy)
                self.refuse(root, "a.string.nobody.shows")

    def test_a_typo_inside_a_copy_string_is_refused(self):
        # A copy string may name a value the generator works out -- the site's
        # address, a row's date -- so a typo in one of those has to fail too.
        root = self.sources()
        copy = load(root / "content" / "copy" / "en" / "common.json")
        copy["footer"] = "Served at {{site.addresss}}."
        dump(root / "content" / "copy" / "en" / "common.json", copy)
        self.refuse(root, "site.addresss")

    def test_no_user_facing_string_is_written_into_a_template(self):
        # The page's wording is the copy files' job. A template carries
        # markup, the keys that fill it, and nothing a reader reads -- so the
        # text of every template, with its tags and its tags' contents taken
        # out, is empty.
        for template in sorted(TEMPLATES.glob("**/*.html")):
            with self.subTest(template=template.name):
                body = re.sub(r"<!--.*?-->", "", template.read_text(encoding="utf-8"),
                              flags=re.DOTALL)
                body = re.sub(r"<style[^>]*>.*?</style>", "", body, flags=re.DOTALL)
                words = re.sub(r"\{\{[^}]*\}\}", "", TAG_RE.sub(" ", body))
                self.assertEqual(words.split(), [],
                                 f"{template.name} writes {words.split()!r} into the page: every "
                                 f"user-facing string is a key in content/copy/en/")


class CopyValueTests(GeneratorTestCase):
    """Every value in a copy file is a string the page shows.

    A copy file is the one source in this repo a founder editing a heading
    opens, and JSON holds four other shapes: a figure typed without its
    quotes, a `null` for a string not written yet, a flag, a list, and keys
    nested under a name instead of written with dots. Each is refused in a
    sentence naming the file, the key and what is wrong with the value -- the
    way a missing key, a dead key and a duplicated key already are.
    """

    # One per shape JSON can hold instead of a string, which is the whole
    # class: the check is "not a string", not a list of the shapes already
    # found to be trouble.
    SHAPES = {
        "a figure typed without quotes": 42,
        "a figure that is not whole": 0.5,
        "null for a string not written yet": None,
        "a flag": True,
        "a list": ["Done.", "Not done yet."],
        "keys nested under the name": {"thisBuild": "This build."},
        "an empty object": {},
    }

    COPY_FILES = ("common.json", "index.json")

    def test_a_copy_value_that_is_not_a_string_is_refused_naming_the_key(self):
        for shape, value in self.SHAPES.items():
            for name in self.COPY_FILES:
                with self.subTest(shape=shape, copy=name):
                    root = self.sources()
                    path = root / "content" / "copy" / "en" / name
                    copy = load(path)
                    key = next(iter(copy))
                    copy[key] = value
                    dump(path, copy)
                    reason = self.refuse(root, f"content/copy/en/{name}")
                    self.assertIn(key, reason,
                                  "the refusal must name the key whose value is wrong")

    def test_a_key_nested_instead_of_dotted_is_told_the_key_to_write(self):
        # The slip is writing {"band": {"heading": "..."}} for the key
        # "band.heading", and the remedy is the dotted key -- so the refusal
        # names it.
        root = self.sources()
        path = root / "content" / "copy" / "en" / "index.json"
        copy = load(path)
        dotted = next(key for key in copy if "." in key)
        head, _, tail = dotted.partition(".")
        copy[head] = {tail: copy.pop(dotted)}
        dump(path, copy)
        self.refuse(root, dotted)

    def test_a_copy_file_that_is_not_keys_and_strings_at_all_is_refused(self):
        for value in (["Some words."], "Some words.", 7, None):
            for name in self.COPY_FILES:
                with self.subTest(copy=name, shape=type(value).__name__):
                    root = self.sources()
                    dump(root / "content" / "copy" / "en" / name, value)
                    self.refuse(root, f"content/copy/en/{name}")

    def test_the_copy_this_repo_holds_is_keys_and_strings(self):
        for path in sorted((CONTENT / "copy").glob("**/*.json")):
            with self.subTest(copy=path.name):
                copy = load(path)
                self.assertIsInstance(copy, dict)
                for key, value in copy.items():
                    self.assertIsInstance(value, str, f"{path.name}: {key} is not a string")


class FactTests(GeneratorTestCase):
    """The snapshot drives the pages: every row of every kind, in position
    order, with the date the page dates it by."""

    def setUp(self):
        self.snapshot = load(SNAPSHOT)

    def written(self, snapshot):
        path = self.tmpdir() / "facts.json"
        dump(path, snapshot)
        return self.site(root=ROOT, snapshot=path)

    def test_every_row_of_every_kind_is_on_the_page_in_position_order(self):
        site = self.site(root=ROOT)
        for kind, columns in KINDS.items():
            with self.subTest(kind=kind):
                rows = rows_of(site, kind)
                self.assertEqual(len(rows), len(self.snapshot[kind]),
                                 f"the page shows {len(rows)} {kind} and the snapshot holds "
                                 f"{len(self.snapshot[kind])}")
                for index, (rendered, row) in enumerate(zip(rows, self.snapshot[kind])):
                    shown = text_of(rendered)
                    for column in shown_columns(kind, columns, index):
                        if not row[column]:
                            continue
                        self.assertIn(plain(row[column]), shown,
                                      f"{kind}[{row['position'] - 1}].{column} is not in the row "
                                      f"the page shows at that position")

    def test_a_row_added_to_a_snapshot_appears_at_its_position(self):
        for kind, columns in KINDS.items():
            with self.subTest(kind=kind):
                snapshot = load(SNAPSHOT)
                rows = snapshot[kind]
                added = json.loads(json.dumps(rows[0]))
                added.update({column: f"added {kind} {column}"
                              for column in shown_columns(kind, columns, 1) if rows[0][column]})
                rows.insert(1, added)
                for index, row in enumerate(rows, start=1):
                    row["position"] = index
                shown = [text_of(row) for row in rows_of(self.written(snapshot), kind)]
                self.assertEqual(len(shown), len(rows))
                self.assertIn(f"added {kind}", shown[1],
                              f"a {kind} row added at position 2 is not the second on the page")

    def test_a_row_removed_from_a_snapshot_disappears(self):
        for kind, columns in KINDS.items():
            with self.subTest(kind=kind):
                snapshot = load(SNAPSHOT)
                gone = snapshot[kind].pop(1)
                for index, row in enumerate(snapshot[kind], start=1):
                    row["position"] = index
                site = self.written(snapshot)
                self.assertEqual(len(rows_of(site, kind)), len(snapshot[kind]))
                left = [row[column] for row in snapshot[kind] for column in columns[2:]]
                told = 0
                for column in shown_columns(kind, columns, 1):
                    # Only what this row alone said: a value another row of the
                    # same kind also carries ("Recorded 26 Oct 2026") is still
                    # on the page, and rightly.
                    if gone[column] in left or len(gone[column]) < 20:
                        continue
                    told += 1
                    # Read off every page the build wrote, not just the one
                    # that kind belongs on: a row taken out of the snapshot is
                    # gone from the site, wherever it used to be shown.
                    self.assertNotIn(plain(gone[column]), text_of("\n".join(site.values())),
                                     f"a {kind} row taken out of the snapshot is still shown")
                self.assertTrue(told, f"nothing in the {kind} row removed was its own to check")

    def test_every_row_is_shown_with_the_date_the_page_dates_it_by(self):
        snapshot = load(SNAPSHOT)
        for kind in KINDS:
            # A date the page has never shown, so a hard-coded one cannot pass.
            for row in snapshot[kind]:
                row["as_of"] = "2026-09-07"
            if ROWS[kind]["date"] == "source":
                for row in snapshot[kind]:
                    row["source"] = "Recorded 7 Sep 2026"
        site = self.written(snapshot)
        for kind in KINDS:
            with self.subTest(kind=kind):
                if ROWS[kind]["date"] == "column":
                    self.assertIn("7 Sep 2026",
                                  section(site[ROWS[kind]["page"]], ROWS[kind]["section"]),
                                  f"the {kind} table is not headed by the day its rows are "
                                  f"true as of")
                    continue
                for index, row in enumerate(rows_of(site, kind)):
                    self.assertIn("7 Sep 2026", text_of(row),
                                  f"{kind} row {index} on the page carries no date")

    def test_only_the_newest_record_entry_is_marked_this_build(self):
        # The mark is the generator's, not a headline somebody has to remember
        # to move, so the page cannot carry two of them however the entries
        # change.
        snapshot = load(SNAPSHOT)
        snapshot["record_entries"][0]["headline"] = "a headline of its own"
        whens = [when_of(row) for row in rows_of(self.written(snapshot), "record_entries")]
        marked = [index for index, when in enumerate(whens) if "this build" in when.lower()]
        self.assertEqual(marked, [0],
                         f"the newest record entry, and only it, is marked 'this build': {whens!r}")

    def test_a_row_column_named_after_a_value_the_generator_works_out_is_refused(self):
        # A row's columns are the values in scope inside its {{#each}}, so a
        # column named after one of the generator's own values -- the site's
        # address, the day a section is dated by -- would stand in for that
        # value wherever the block names it. The puller refuses a column it
        # does not know at pull time; the build refuses this one too, because
        # the page is written from the snapshot rather than from the pull.
        for column in ("site.address", "dates.status"):
            with self.subTest(column=column):
                snapshot = load(SNAPSHOT)
                snapshot["record_entries"][0][column] = "somewhere else"
                path = self.tmpdir() / "facts.json"
                dump(path, snapshot)
                self.refuse(ROOT, column, snapshot=path)

    def test_a_this_build_mark_left_behind_in_an_older_entry_is_refused(self):
        snapshot = load(SNAPSHOT)
        snapshot["record_entries"][1]["headline"] = "this build"
        path = self.tmpdir() / "facts.json"
        dump(path, snapshot)
        self.refuse(ROOT, "this build", snapshot=path)


class InlineFormTests(GeneratorTestCase):
    """A fact's text is text. Two inline forms are rendered -- `[text](url)`
    and backticked `code` -- and everything else is escaped, so no row in the
    store can put markup on the page."""

    ATTACK = ('A <script>alert("x")</script> tag, a "quote", a 5 > 3 & a 2 < 3, '
              "a [link](https://github.com/todd450/founding-agents-site/pull/35) "
              "and a `<li>` literal.")

    def rendered_record_entry(self, text):
        snapshot = load(SNAPSHOT)
        snapshot["record_entries"] = [dict(snapshot["record_entries"][0], entry=text)]
        path = self.tmpdir() / "facts.json"
        dump(path, snapshot)
        site = self.site(root=ROOT, snapshot=path)
        return re.search(r"<p>(.*?)</p>", rows_of(site, "record_entries")[0], re.DOTALL).group(1)

    def test_nothing_in_a_fact_but_the_two_inline_forms_becomes_markup(self):
        rendered = self.rendered_record_entry(self.ATTACK)
        self.assertNotIn("<script", rendered)
        self.assertIn("&lt;script&gt;", rendered)
        self.assertIn("&quot;quote&quot;", rendered)
        self.assertIn("&amp;", rendered)
        self.assertEqual(
            sorted(TAG_RE.findall(rendered)),
            sorted(['<a href="https://github.com/todd450/founding-agents-site/pull/35">', "</a>",
                    '<span class="num">', "</span>"]),
            "a fact put a tag on the page that is neither a link nor a literal")

    def test_a_link_in_a_fact_comes_out_as_a_link_and_a_literal_as_a_literal(self):
        rendered = self.rendered_record_entry(self.ATTACK)
        self.assertIn('<a href="https://github.com/todd450/founding-agents-site/pull/35">link</a>',
                      rendered)
        self.assertIn('<span class="num">&lt;li&gt;</span>', rendered)

    def test_a_quote_in_a_fact_cannot_close_an_attribute(self):
        # Read by a browser's own rules rather than by eye: the link has to
        # come out carrying one attribute, the href, holding the exact string
        # the fact wrote -- not an href that ends early and a second attribute
        # after it.
        target = 'https://github.com/todd450/founding-agents-site/" onmouseover="x'
        rendered = self.rendered_record_entry(f"A [link]({target}) here.")
        elements = Elements(rendered)
        self.assertEqual(elements.tags, [("a", {"href": target})],
                         "a quote in a link's target opened an attribute of its own")

    def test_the_generator_reads_a_fact_s_links_the_way_the_puller_does(self):
        # Both read the same two inline forms, and the puller is the gate on
        # what may be in the snapshot at all. If they drift, a destination the
        # puller checked is not the destination that reaches the page.
        site = self.site(root=ROOT)
        snapshot = load(SNAPSHOT)
        for kind, columns in KINDS.items():
            rows = rows_of(site, kind)
            for index, (rendered, row) in enumerate(zip(rows, snapshot[kind])):
                with self.subTest(row=f"{kind}[{index}]"):
                    expected = [target
                                for targets, _ in links_in([row[column] for column in columns[2:]])
                                for target in targets]
                    self.assertEqual(HREF_RE.findall(rendered), expected,
                                     "the links the page carries for this row are not the ones "
                                     "the puller reads in it")

    def test_a_fact_s_text_reads_back_out_of_the_page_exactly(self):
        # The other half of the same question, for the backticks: taking the
        # generator's two forms back off the page has to give the fact's text
        # character for character. A span paired some other way, a character
        # dropped, or anything invented would show up here -- and what a real
        # renderer makes of those backticks is check.sh's markdown-it run.
        site = self.site(root=ROOT)
        snapshot = load(SNAPSHOT)
        for kind, (column, pattern) in RICH.items():
            for index, (rendered, row) in enumerate(zip(rows_of(site, kind), snapshot[kind])):
                with self.subTest(value=f"{kind}[{index}].{column}"):
                    found = pattern.search(rendered)
                    self.assertIsNotNone(found, f"no {column} in the row the page shows")
                    self.assertEqual(unrender(found.group(1)), row[column])
                    # And no backtick survives: the gate lets a fact quote only
                    # in pairs, so every pair became one literal rather than
                    # two characters a reader has to read past.
                    self.assertNotIn("`", found.group(1),
                                     "a backtick reached the page instead of quoting a literal")


class EmptyAndOneTests(GeneratorTestCase):
    """fixtures/empty-and-one: no items, one item, and far more than the page
    has ever carried."""

    def scenarios(self):
        found = sorted(EMPTY_AND_ONE.glob("*.json"))
        self.assertTrue(found, f"no scenarios in {EMPTY_AND_ONE}")
        return found

    def test_every_scenario_renders_a_page(self):
        for path in self.scenarios():
            with self.subTest(scenario=path.name):
                site = self.site(root=ROOT, snapshot=path)
                snapshot = load(path)
                for kind in KINDS:
                    self.assertEqual(len(rows_of(site, kind)), len(snapshot[kind]),
                                     f"{path.name}: the site does not show every {kind}")

    def test_the_sections_are_all_there_however_many_rows_there_are(self):
        for path in self.scenarios():
            with self.subTest(scenario=path.name):
                html = self.page(root=ROOT, snapshot=path)
                for name in ("band", "costs", "flow", "limits", "measures", "record"):
                    self.assertIsNotNone(
                        re.search(rf'<section class="{name}"[^>]*>', html),
                        f"{path.name}: the {name} section is missing")

    def test_the_folder_covers_none_one_and_the_most(self):
        counts = [len(load(path)["record_entries"]) for path in self.scenarios()]
        self.assertIn(0, counts, "no scenario with an empty record")
        self.assertIn(1, counts, "no scenario with a single record entry")
        self.assertTrue(max(counts) > len(load(SNAPSHOT)["record_entries"]),
                        "no scenario with more record entries than the page carries today")


class OfflineTests(GeneratorTestCase):
    """The generator reads the committed snapshot and nothing else."""

    # Runs the generator with fetch replaced by something that throws, so
    # "makes no network call" is a fact and not a promise.
    NO_NETWORK = (
        "globalThis.fetch = () => { throw new Error('the generator makes no network call'); };"
        "require(process.argv[1]).build({ root: process.argv[2], out: process.argv[3] });"
    )

    def test_it_writes_the_same_pages_with_no_network_at_all(self):
        out = self.tmpdir()
        result = subprocess.run(
            [NODE, "-e", self.NO_NETWORK, str(GENERATE_JS), str(ROOT), str(out)],
            capture_output=True, text=True, timeout=120, cwd=str(ROOT))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((out / "index.html").read_text(encoding="utf-8"),
                         INDEX_HTML.read_text(encoding="utf-8"))

    def test_it_imports_only_node_s_standard_library(self):
        imports = re.findall(r"require\('([^']+)'\)", GENERATE_JS.read_text(encoding="utf-8"))
        self.assertTrue(imports, "expected the generator to require something")
        for name in imports:
            with self.subTest(module=name):
                self.assertTrue(name.startswith("node:"),
                                f"the generator must use Node's standard library only: {name!r}")

    def test_it_names_no_address_to_read_facts_from(self):
        source = GENERATE_JS.read_text(encoding="utf-8")
        for reaching_out in ("fetch(", "http://", "https://", "supabase"):
            self.assertNotIn(reaching_out, source,
                             f"the generator names {reaching_out!r}: it reads the committed "
                             f"snapshot and nothing else")


class SiteAddressTests(GeneratorTestCase):
    """The site's address lives once, in content/site.json, and the layout
    reads it -- so #30 moves it in one file."""

    def test_the_footer_links_the_address_site_json_holds(self):
        address = load(SITE_JSON)["address"]
        footer = re.search(r"<footer[^>]*>(.*?)</footer>",
                           self.page(root=ROOT), re.DOTALL).group(1)
        self.assertIn(f'href="{address}"', footer)

    def test_moving_the_address_moves_it_everywhere_on_the_page(self):
        root = self.sources()
        site = load(root / "content" / "site.json")
        was = site["address"]
        site["address"] = "https://founding-agents.example/"
        dump(root / "content" / "site.json", site)
        html = self.page(root=root)
        self.assertIn('href="https://founding-agents.example/"', html)
        self.assertIn("founding-agents.example", html)
        self.assertNotIn(was, html,
                         "the old address is still on the page, so it lives in more than one "
                         "place")

    def test_an_address_that_is_not_an_https_url_is_refused(self):
        # The address is the one value the footer of every page links, so the
        # build is where it is held to being an address a browser follows over
        # https -- not a scheme that runs, and not a word that happens to be a
        # non-empty string. #30 is the issue that edits this value.
        for address in ("javascript:alert`1`", "x\" onload=\"alert(1)", "/founding-agents-site/",
                        "http://todd450.github.io/founding-agents-site/", "  "):
            with self.subTest(address=address):
                root = self.sources()
                site = load(root / "content" / "site.json")
                site["address"] = address
                dump(root / "content" / "site.json", site)
                self.refuse(root, "address")

    def test_the_title_comes_from_site_json(self):
        title = re.search(r"<title>(.*?)</title>", self.page(root=ROOT), re.DOTALL).group(1)
        self.assertEqual(title, load(SITE_JSON)["title"])


if __name__ == "__main__":
    unittest.main()
