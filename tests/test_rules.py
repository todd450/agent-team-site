"""The cases the rules about a fact's text have to answer: where a link may
point, how the text is read to find its links, what it quotes as code rather
than putting on the page as markup, and which characters it may not carry at
all.

The rules live in one place, `scripts/pull-facts.js`, because that is the gate
on what enters the facts snapshot (decided on #27, 28 Oct 2026). Every case
here is put to that one definition through `tests/rules.py`, so a hole opened
in it is a failure here rather than a link on the page -- and there is no
second copy to drift from it, which is what used to go wrong.

What the cases pin:

- A link is judged by where a browser would land, not by what the string
  starts with. An address that begins with this repo's own and then walks out
  of it -- on `..`, on backslashes, on escaped dots, or on a tab a browser
  strips before it parses -- is a link off this site.
- A reader that cannot read a link whole says so instead of finding nothing.
  A renderer reads more as a link than the reader's pattern does, and every
  such link is one whose destination no allowlist would ever see.
- A code span is a pair of single backticks on one line of text a renderer
  reads the same way the reader does, and any text it would read some other
  way -- a line ending, a backslash escape, a character reference, a cell
  separator, backticks that do not pair -- is reported rather than read,
  because a tag a reader thinks is quoted is an element on the page.
- A fact's characters come from an allowlist -- letters, marks, numbers,
  punctuation, symbols and ordinary spaces, and nothing invisible by default
  -- so a character neither a reader of the page nor a reviewer reading the
  diff can see is reported wherever it sits, whether or not anyone has met it
  before.
- The readers answer the longest text promptly. A fact's text has no length
  limit, so a reader that rescans it from every opening bracket turns one long
  value into a pull, a check.sh run and a CI run that take minutes.
"""

import time
import unittest

from rules import (PAGES_PREFIX, REPO_PREFIX, allowed_links, links_in,
                   markup_in, refused_in)

# (link, why it is in the allowlist)
ALLOWED = [
    (REPO_PREFIX, "the repository itself"),
    (f"{REPO_PREFIX}/pull/35", "a pull request in it"),
    (f"{REPO_PREFIX}/issues/3#issuecomment-1", "an issue, at a comment"),
    (f"{REPO_PREFIX}/blob/main/check.sh", "a file in it"),
    (f"{REPO_PREFIX}/issues?q=open", "a query on it"),
    (f"{REPO_PREFIX}/pulls?q=is%3Apr+is%3Amerged", "a query whose escapes are ordinary text"),
    (PAGES_PREFIX, "the site itself"),
    (f"{PAGES_PREFIX}/", "the site's own page"),
    (f"{PAGES_PREFIX}/index.html", "a file on the site"),
]

# (link, where it really goes)
REFUSED = [
    (f"{REPO_PREFIX}/../../attacker/evil-repo", "github.com/attacker/evil-repo"),
    (f"{REPO_PREFIX}/..\\..\\attacker/evil", "github.com/attacker/evil, on backslashes"),
    (f"{REPO_PREFIX}/%2e%2e/%2e%2e/evil", "github.com/evil, on escaped dots"),
    (f"{REPO_PREFIX}/x%2F%2E%2E%2Fevil", "github.com, by a path only the server decodes: an "
                                         "escaped slash and dots that no reader can follow"),
    (f"{REPO_PREFIX}/%zz", "nowhere a reader can work out: that is not an escape"),
    (f"{REPO_PREFIX}/\t../../evil/x", "github.com/evil/x, past a stripped tab"),
    (f"{REPO_PREFIX}/\n../../evil/x", "github.com/evil/x, past a stripped newline"),
    (f"{REPO_PREFIX}/./evil", "github.com/todd450/founding-agents-site/evil, "
                              "which is inside the allowlist but is not the address written down"),
    # A dot segment does not need a slash after it to do its work: the query or
    # the fragment ends the path just as well, and so does the end of the
    # address itself.
    (f"{REPO_PREFIX}/..?q=1", "github.com/todd450/, on dots a query ends"),
    (f"{REPO_PREFIX}/..#readme", "github.com/todd450/, on dots a fragment ends"),
    (f"{REPO_PREFIX}/..", "github.com/todd450/, on dots the address ends"),
    (f"{REPO_PREFIX}/..\n", "github.com/todd450/, on dots a stripped newline ends"),
    (f"{REPO_PREFIX}/x/..?q=1", "github.com/todd450/founding-agents-site/, inside the allowlist "
                                "but not the address written down"),
    # A renderer resolves character references inside a link's destination,
    # so an address whose literal bytes carry no dot segment at all can still
    # put one in the href. Six spellings, every one of them confirmed against
    # GitHub's renderer.
    (f"{REPO_PREFIX}/&period;&period;/&period;&period;/attacker/evil",
     "github.com/attacker/evil, on named references a renderer decodes to dots"),
    (f"{REPO_PREFIX}/&#46;&#46;/&#46;&#46;/attacker/evil", "the same, in decimal"),
    (f"{REPO_PREFIX}/&#x2E;&#x2E;/&#x2E;&#x2E;/attacker/evil", "the same, in hex"),
    (f"{REPO_PREFIX}/..&sol;..&sol;attacker/evil",
     "the same, with the separators as references, so no segment reads as '..' either"),
    (f"{REPO_PREFIX}/..&#x2F;..&#x2F;attacker/evil", "the same, in hex"),
    (f"{REPO_PREFIX}/&#37;2e&#37;2e/evil",
     "github.com/evil: a decoded '&#37;' is a '%', which puts back the escaped dots"),
    (f"{PAGES_PREFIX}/&period;&period;/evil/x", "todd450.github.io/evil/x, past the same dots"),
    (f"{PAGES_PREFIX}/../../evil/x", "todd450.github.io/evil/x, another site on the account"),
    (f"{PAGES_PREFIX}/..?q=1", "todd450.github.io/, which answers 404"),
    (f"{PAGES_PREFIX}/..#top", "todd450.github.io/, which answers 404"),
    (f"{PAGES_PREFIX}/..\n", "todd450.github.io/, which answers 404"),
    # A destination may hold parentheses when they are balanced, so a reader
    # that stops at the first ")" reads less of the address than a renderer
    # does. Neither the whole address nor the prefix a reader is left holding
    # is a link this site vouches for.
    (f"{REPO_PREFIX}/a(b)/../../attacker/evil", "github.com/attacker/evil, past balanced "
                                                "parentheses a renderer keeps in the address"),
    (f"{REPO_PREFIX}/a(b", "a prefix of that address, which is not the address written down"),
    (f"{REPO_PREFIX}-evil/pull/2", "a repository whose name merely starts with this one's"),
    (f"{REPO_PREFIX}@evil.example/x", "evil.example, with this repo's address as a username"),
    ("https://github.com/todd450/founding-agents", "the private plugin repo"),
    ("http://github.com/todd450/founding-agents-site", "this repo, but not over https"),
    ("https://github.com.evil.example/todd450/founding-agents-site", "a lookalike host"),
    ("https://evil.example/https://github.com/todd450/founding-agents-site", "evil.example"),
    ("javascript:alert(1)", "nowhere: a script, not a page"),
    ("//github.com/todd450/founding-agents-site", "whatever scheme the page was served over"),
    ("", "nowhere at all"),
]

# A fact's text, and every link a reader has to find in it. These are texts
# the reader can read whole, so what it finds is what a renderer would.
# (text, targets)
READABLE = [
    ("Nothing here links anywhere.", []),
    (f"Built in [PR #35]({REPO_PREFIX}/pull/35).", [f"{REPO_PREFIX}/pull/35"]),
    (f"[the issue]({REPO_PREFIX}/issues/27) and [the site]({PAGES_PREFIX}/),",
     [f"{REPO_PREFIX}/issues/27", f"{PAGES_PREFIX}/"]),
    ("A bracketed aside [like this one] that links nowhere.", []),
    ("An empty target [here]() points at the page itself.", [""]),
    ("A link off this site [evil](https://evil.example/x) is still read whole.",
     ["https://evil.example/x"]),
]

# A text that writes a link this reader cannot read whole. A renderer sees a
# link in each of these; the reader sees no target, or only the inner one, so
# the destination that would reach the href is one nothing checked. Each must
# be reported as unreadable rather than quietly passing.
# (text, what a renderer makes of it)
UNREADABLE = [
    ("[the writeup [part two]](javascript:alert(1))",
     "a link to javascript:, since link text may hold balanced brackets"),
    (f"[![chart]({REPO_PREFIX}/chart.png)](javascript:alert(1))",
     "an image wrapped in a link to javascript:; only the image is read"),
    (r"[the run \]log](javascript:alert(1))",
     "a link to javascript:, past a backslash-escaped bracket"),
    (f"Read [the run]({REPO_PREFIX}/pull/35), then [see [this]](javascript:alert(1)).",
     "one link the reader does read, and one it does not"),
    (f"[a]({REPO_PREFIX}/x)](javascript:alert(1))",
     "a target left over once the link the reader did read is taken out"),
    (f"Read [the plan]({REPO_PREFIX}/a(b)/../../attacker/evil) first.",
     "a link to github.com/attacker/evil: a destination may hold balanced parentheses, so the "
     "reader stops at the first ')' and is left with an allowed prefix of the address"),
    ("See [the writeup][r] for detail.\n\n[r]: javascript:alert(1)",
     "a reference link to javascript:, with no '](' anywhere for the reader to find"),
    ("See [the writeup][] for detail.\n\n[the writeup]: javascript:alert(1)",
     "the same link, collapsed"),
    ("[r]: javascript:alert(1)",
     "a definition on its own, which gives a reference in any text beside it its destination"),
    # A fact writes a link one way, [text](url), and this reader reads that
    # one way whole. A bare address is refused rather than read: a renderer
    # autolinks it and ends the run at whitespace, keeping the brackets and
    # parentheses a reader stops at, so the two never agree on where it goes.
    (f"See {REPO_PREFIX}/a)/../../../attacker/evil for detail.",
     "an autolink to github.com/attacker/evil: the run ends at the space, and a reader that "
     "stops at the ')' is left holding an allowed prefix"),
    (f"See {REPO_PREFIX}/a]/../../../attacker/evil for detail.",
     "the same address past a ']', which a renderer escapes and keeps in the href"),
    (f"See {REPO_PREFIX}/pull/35 for detail.",
     "an autolink into this repo -- where it lands is allowed, and it is still not the one form "
     "a fact writes a link in"),
    ("See www.attacker.example/evil for detail.",
     "an autolink to www.attacker.example/evil: GFM needs no scheme"),
    ("Mail steal@attacker.example for detail.",
     "an autolink to mailto:steal@attacker.example"),
    (f"Served over {REPO_PREFIX[len('https:'):]} now.",
     "whatever scheme the page was served over, with no scheme in the text to find"),
]


# A fact's text, and what the reader reports of it: the brackets it would put
# on the page as markup, or the one character that makes it unreadable -- a
# line ending, a backslash, an ampersand that may begin a character reference,
# a pipe, or a backtick this reader cannot pair the way a renderer would. Each
# of those is a text a renderer reads differently from the reader, and reading
# it differently is how a tag that looks quoted becomes an element.
# (text, what the reader reports)
MARKUP = [
    ("Nothing here is markup at all.", []),
    ("A literal `<li>` in backticks is quoted code.", []),
    ("Two literals, `<li>` and `<ul>`, in one line.", []),
    ("An <img src=x> outside backticks is markup.", ["<", ">"]),
    ("An unclosed `backtick leaves what is quoted unclear.", ["`"]),
    # A renderer closes a run of N backticks only with the next run of exactly
    # N -- of exactly two as surely as of three.
    ("The flag ```<img src=x onerror=alert(1)>` is set.", ["`"]),
    ("A ``<img src=x>`` pair quotes nothing this reader can pair.", ["`"]),
    # A backslash escape a renderer honours, which makes a backtick a literal
    # character rather than a delimiter.
    (r"Literal backtick \`<img src=x onerror=alert(1)>\` here.", ["\\"]),
    (r"A \`b` and `<img src=x>` c.", ["\\"]),
    # A line ending ends the paragraph, and with it the span -- as a blank
    # line, a list item, a heading or a thematic break each do. A fact is one
    # line, so a line ending anywhere is reported however it is spelled.
    ("`x\n- <img src=a>\n`", ["\n"]),
    ("`x\n# <img src=a>\n`", ["\n"]),
    ("`x\n***\n<img src=a> `", ["\n"]),
    ("`x\n\n<img src=x onerror=alert(1)>`", ["\n"]),
    ("A span the paragraph's own wrap broke: `<li>\nand more`.", ["\n"]),
    # A CommonMark line ending is a carriage return not followed by a newline
    # as much as it is a newline, so two lone carriage returns are a paragraph
    # break exactly as "\n\n" is.
    ("The flag `x\r\r<img src=x onerror=alert(1)>` is set.", ["\n"]),
    # And one a renderer would keep inside the span: still a line this reader
    # does not read, because a fact is one line.
    ("A flag `x\rb` on two lines.", ["\n"]),
    ("`x\r\n\r\n<img src=a>`", ["\n"]),
    # A pipe is a cell separator, and a row is split into cells before any
    # inline is read, so the backticks never pair at all.
    ("h | `x|<img src=a onerror=alert(1)>`\n--- | --- | ---\na | b | c", ["\n", "|"]),
    ("The flag `x|<img src=a onerror=alert(1)>` is set.", ["|"]),
    # A character reference is decoded before anything reads the text, so a
    # tag can be spelled without a bracket in sight.
    ("A flag &#60;img src=x onerror=alert(1)&#62; here.", ["&"]),
    ("A flag &lt;img src=x&gt; here.", ["&"]),
    ("A target that decodes to dots: [plan](https://x.test/&period;&period;/evil)", ["&"]),
]


# A fact's text, and the characters in it the allowlist refuses. A fact may
# carry letters, marks, numbers, punctuation, symbols and ordinary spaces
# (Unicode L, M, N, P, S and Zs) and no character that is invisible by default
# (decided on #27, 28 Oct 2026). Anything else is refused wherever in the value
# it sits and whether or not it is inside backticks -- not only when it is the
# whole of a value, because the value that matters is the ordinary-looking one
# with one of these hidden in it.
#
# These are the two ways a fact can get past the review this snapshot's safety
# rests on: a value a reader sees nothing of, which fills a required column
# and leaves the page blank, and a value whose stored order and displayed
# order are not the same, which lets the page show a reader something other
# than what the fact says while every check is green. Neither shows in
# `git diff` either: JSON.stringify writes them into the snapshot raw.
#
# Every case below used to need its own line in a list of refused code points,
# and the list was never finished -- which is why the rule says what a fact may
# carry instead.
# (text, the code points reported)
REFUSED_CHARACTERS = [
    ("Nothing here is hidden at all.", []),
    (f"A literal `<li>` and a [link]({REPO_PREFIX}/pull/35).", []),
    # An em dash, an accent, a right-to-left script and a CJK word are
    # ordinary text: the rule is about what cannot be seen, not about what
    # is not English.
    ("Recorded 26 Oct 2026 \u2014 caf\u00e9, \u05e2\u05d1\u05e8\u05d9\u05ea, \u4e2d\u6587.", []),
    # A value made of them, which is neither empty nor whitespace and shows
    # a reader nothing: a required column filled and the page still blank.
    ("\u001f", ["U+001F"]),
    ("\u200b\u200b\u200b", ["U+200B"]),
    ("\ufeff", ["U+FEFF"]),
    # And the same characters inside a value that reads perfectly well.
    ("A step\u0085 that is done.", ["U+0085"]),
    ("A figure of 5,\u200c410 commits.", ["U+200C"]),
    ("Two words joined\u2060up.", ["U+2060"]),
    # Every direction override, embedding and isolate: each reorders the
    # text after it, so the page and the snapshot can say different things.
    ("Done, see the plan.\u202a", ["U+202A"]),
    ("Done, see the plan.\u202b", ["U+202B"]),
    ("Done, see the plan.\u202c", ["U+202C"]),
    ("Done, see the plan.\u202d", ["U+202D"]),
    ("Done, see the plan.\u202e and nothing else.", ["U+202E"]),
    ("Done, see the plan.\u2066", ["U+2066"]),
    ("Done, see the plan.\u2067", ["U+2067"]),
    ("Done, see the plan.\u2068", ["U+2068"]),
    ("Done, see the plan.\u2069", ["U+2069"]),
    # Inside backticks is no different: the rule is about a character
    # nobody can see, not about what a renderer would do with it.
    ("The flag `x\u200by` is set.", ["U+200B"]),
    # Reported once each, in the order they are first found, so the reason
    # a pull gives names them all.
    ("A\u200bb\u202ec\u200bd", ["U+200B", "U+202E"]),
    # The bidirectional marks, which are the same two harms under another
    # Unicode heading: no width of their own, and they reorder the neutral
    # text that follows them.
    ("Done, see the plan.\u200e", ["U+200E"]),
    ("Done, see the plan.\u200f", ["U+200F"]),
    ("Done, see the plan.\u061c", ["U+061C"]),
    # A bar of "3-5" between two marks, which displays as "5-3": the
    # snapshot and the page then state different numbers, and `git diff`
    # shows the reviewer neither mark.
    ("\u061c3-5\u061c", ["U+061C"]),
    # A soft hyphen: a letter-width character with nothing to show for
    # itself unless the line happens to break on it.
    ("Recorded 26 Oct 2026, 61,365 lines\u00ad.", ["U+00AD"]),
    # A variation selector, which is a mark (an allowed category) and
    # default-ignorable, so it is the character the category list alone
    # would let through.
    ("A step that is done.\ufe0f", ["U+FE0F"]),
    # The invisible mathematical operators, and the characters whose own
    # Unicode name says they are fillers.
    ("Two figures 61\u2062365 apart.", ["U+2062"]),
    ("A label of nothing: \u3164", ["U+3164"]),
    ("A tag nobody can see \U000e0001here.", ["U+E0001"]),
]

# A fact's text has no length limit: a Postgres text column has none, the page
# already carries a fact of 1,423 characters, and nobody writing one counts.
# So a reader has to be linear in the length of the text it reads. A reader
# that rescans from each opening bracket is quadratic instead, and one long
# value made of a single repeated punctuation character then takes a pull, and
# with it check.sh and CI, from a tenth of a second to minutes.
#
# Each of these is the start of something the readers look for, repeated to a
# value far longer than any fact: a bracket a link opens with, a link left
# open, a line that could begin a reference definition, and a backtick that
# opens a code span.
LONG_RUNS = ("[", "[a](", "  [\n", "`")
LONG_RUN_LENGTH = 200000
# A linear reader answers each of these in milliseconds, so a bound this loose
# fails only on a reader that rescans: the quadratic one took 17s over 160,000
# characters and 70s inside a full pull.
LONG_RUN_SECONDS = 5


def long_run(run):
    return run * (LONG_RUN_LENGTH // len(run))


class LinkRuleTests(unittest.TestCase):
    """Where a link in a fact, or on the page, may point."""

    def test_a_link_into_this_repo_or_its_site_is_allowed(self):
        for (link, why), allowed in zip(ALLOWED, allowed_links(link for link, _ in ALLOWED)):
            with self.subTest(link=link):
                self.assertTrue(allowed, f"{why} is this site")

    def test_a_link_that_lands_anywhere_else_is_refused(self):
        for (link, where), allowed in zip(REFUSED, allowed_links(link for link, _ in REFUSED)):
            with self.subTest(link=link):
                self.assertFalse(allowed, f"this link goes to {where}")


class LinkReaderTests(unittest.TestCase):
    """Finding the links in a fact's text, and saying so when a link cannot be
    read whole."""

    def test_every_link_in_a_text_it_can_read_is_found(self):
        for (text, targets), read in zip(READABLE, links_in(text for text, _ in READABLE)):
            with self.subTest(text=text):
                self.assertEqual(read, (targets, False))

    def test_a_link_it_cannot_read_whole_is_reported_rather_than_missed(self):
        for (text, renders_as), (_, unreadable) in zip(
                UNREADABLE, links_in(text for text, _ in UNREADABLE)):
            with self.subTest(text=text):
                self.assertTrue(unreadable, f"a renderer makes {renders_as} of this")


class MarkupReaderTests(unittest.TestCase):
    """What a fact's text quotes as code, and what it would put on the page as
    markup."""

    def test_only_a_bracket_a_renderer_would_emit_is_reported(self):
        for (text, brackets), reported in zip(MARKUP, markup_in(text for text, _ in MARKUP)):
            with self.subTest(text=text):
                self.assertEqual(reported, brackets)


class CharacterAllowlistTests(unittest.TestCase):
    """The characters a fact may carry, and the ones it may not -- because
    nobody reading the page or the diff can see them."""

    def test_every_character_outside_the_allowlist_is_reported(self):
        for (text, found), reported in zip(
                REFUSED_CHARACTERS, refused_in(text for text, _ in REFUSED_CHARACTERS)):
            with self.subTest(text=text):
                self.assertEqual(reported, found)


class LongTextTests(unittest.TestCase):
    """The longest text: the readers a pull runs answer a value far longer
    than any fact promptly, however much of it is punctuation."""

    def test_a_long_run_of_one_character_is_read_promptly(self):
        texts = [long_run(run) for run in LONG_RUNS]
        started = time.monotonic()
        read = links_in(texts)
        reported = markup_in(texts)
        elapsed = time.monotonic() - started
        self.assertEqual(len(read), len(texts))
        self.assertEqual(len(reported), len(texts))
        self.assertLess(elapsed, LONG_RUN_SECONDS,
                        f"reading {len(texts)} values of {LONG_RUN_LENGTH} characters took "
                        f"{elapsed:.1f}s: a reader that rescans the text makes a pull, and with "
                        "it check.sh and CI, take minutes")


if __name__ == "__main__":
    unittest.main()
