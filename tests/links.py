"""The one rule for where a link on this site may point, in Python, and the
one reader that finds the links in a fact's text.

This page's links -- the ones written into index.html and the ones a fact
carries in content/facts.json -- may go to this repository or to its Pages
site, and nowhere else. A link anywhere else is one the nightly check cannot
vouch for, and a link into the private plugin repo answers 404, opens `red`
and freezes every merge.

scripts/pull-facts.js holds the same rule and the same reader, because it is
the gate on what enters the snapshot; tests/test_links.py runs both copies
over the same cases so they cannot drift apart.
"""

import re
from urllib.parse import urljoin, urlsplit

ALLOWED_LINK_PREFIXES = (
    "https://github.com/todd450/founding-agents-site",
    "https://todd450.github.io/founding-agents-site",
)
REPO_PREFIX, PAGES_PREFIX = ALLOWED_LINK_PREFIXES

# What may follow an allowed prefix: the plain URL characters, and no more.
# A backslash, a space, a tab or a newline is rewritten or stripped by a
# browser before it works out where a link goes, so an address carrying one
# does not say plainly where it lands. A parenthesis is out with them:
# CommonMark keeps a balanced pair inside a link's destination, so a reader
# that stops at the first ")" is left holding a prefix of the address and
# would vouch for a page nobody wrote down.
PLAIN_URL_CHARS_RE = re.compile(r"^[A-Za-z0-9\-._~:/?#\[\]@!$&'*+,;=%]*$")
# A percent escape is fine when it stands for ordinary text (?q=is%3Apr), and
# is not when it stands for a character that decides where the address goes:
# %2e is a dot, %2f a slash, %5c a backslash, and %00-%1f and %7f are the
# control characters a browser strips. A % that begins no escape at all is
# refused with them: nobody can say where it points.
ESCAPE_RE = re.compile(r"%[0-9A-Fa-f]{2}")
SEPARATOR_ESCAPE_RE = re.compile(r"%(?:2[eEfF]|5[cC]|[01][0-9A-Fa-f]|7[fF])")
# Where the path stops: a query or a fragment ends it, and so does the end of
# the address. A dot segment needs no slash after it to walk up a level.
PATH_ENDS_RE = re.compile(r"[?#]")

# A link, as this reader can read one: [text](target), with no bracket in the
# text and no ")" in the target.
LINK_TARGET_RE = re.compile(r"\[[^\]]*\]\(([^)]*)\)")
# A bare address, or anything else a renderer turns into a link on its own.
# GFM autolinks a literal "https://..." run, a scheme-relative "//host", a
# "www." host and an email address, and it ends such a run at whitespace --
# keeping the brackets and parentheses a reader stops at, so where a reader
# thinks a bare address ends and where a renderer puts it in the href are two
# different answers. A fact writes a link one way, [text](url), which this
# reader reads whole; link-like text in any other form is refused rather than
# read (decided on #27, 27 Oct 2026).
LINK_LIKE_RE = re.compile(r"://|//|www\.|@", re.IGNORECASE)
# A backslash before a bracket or a parenthesis: an escape a renderer honours
# and this reader cannot see, so the two disagree about where a link ends.
ESCAPED_BRACKET_RE = re.compile(r"\\[\[\]()]")
# A parenthesis inside a target the reader did read: a destination may hold a
# balanced pair, so what the reader stopped at is not where the link ends.
PAREN_RE = re.compile(r"[()]")
# The other two spellings of a link, which name their destination elsewhere:
# a reference or collapsed link ("[text][ref]", "[text][]") and the
# definition that gives it an address ("[ref]: ..."). Neither carries a "](",
# and a definition's address needs no "://", so a reader looking for those
# finds nothing at all where a renderer finds a link.
REFERENCE_LINK_RE = re.compile(r"\]\[")
LINK_DEFINITION_RE = re.compile(r"^ {0,3}\[[^\]]*\]:", re.MULTILINE)


def link_targets(text):
    """Where every link in this text points, and whether it also writes one
    this reader could not read whole.

    A renderer reads more as a link than LINK_TARGET_RE does. CommonMark says
    link text may hold balanced brackets ("[see [this]](...)"), an image may
    be wrapped in a link ("[![chart](ok.png)](...)"), a bracket may be
    backslash-escaped, a destination may hold a balanced pair of parentheses
    ("(.../a(b)/...)"), and a link may name its destination elsewhere
    ("[text][ref]" with "[ref]: ..."); GFM autolinks a bare address with no
    brackets at all. In each of those this reader finds no target at all, or
    only part of one, so the destination a renderer would put in the href is
    a destination nothing checked -- which is how a "javascript:" target
    walks past an allowlist.

    So the reader has to account for every "](" the text contains: whatever
    is left once the links it did read are taken out must hold none, and must
    hold nothing else link-like either. Beyond that, a backslash-escaped
    bracket, a target carrying a parenthesis, a "][" and a line that defines
    a reference are each a link it cannot see whole. When it cannot, it says
    so and the caller refuses the text rather than vouching for a link it
    never read.

    Returns (targets, unreadable).
    """
    targets = []

    def taken(match):
        targets.append(match.group(1))
        return " "  # a space, so taking a link out can join no new "]("

    residue = LINK_TARGET_RE.sub(taken, text)
    unreadable = ("](" in residue
                  or LINK_LIKE_RE.search(residue) is not None
                  or ESCAPED_BRACKET_RE.search(text) is not None
                  or REFERENCE_LINK_RE.search(text) is not None
                  or LINK_DEFINITION_RE.search(text) is not None
                  or any(PAREN_RE.search(target) for target in targets))
    return targets, unreadable


def allowed_link(link):
    """Whether a browser following this link would land inside the allowlist.

    The question is where the link goes, not what it starts with: an address
    beginning with this repo's own and carrying a "." or ".." segment resolves
    somewhere else entirely, such as
    https://github.com/todd450/founding-agents-site/../../attacker/evil ->
    https://github.com/attacker/evil.
    """
    for prefix in ALLOWED_LINK_PREFIXES:
        if not link.startswith(prefix):
            continue
        rest = link[len(prefix):]
        # A lookalike repository whose name merely starts with this one's is
        # not this one: what follows must end the address or begin a path,
        # query or fragment.
        if rest and rest[0] not in "/?#":
            continue
        # fullmatch, not match: Python's "$" also matches before a trailing
        # newline, and a newline is a character a browser strips.
        if not PLAIN_URL_CHARS_RE.fullmatch(rest):
            return False
        if SEPARATOR_ESCAPE_RE.search(rest) or "%" in ESCAPE_RE.sub("", rest):
            return False
        path = PATH_ENDS_RE.split(rest, maxsplit=1)[0]
        if any(segment in (".", "..") for segment in path.split("/")):
            return False
        # The URL parser has the last word on where the link lands: resolving
        # the address against itself drops the query and the fragment and
        # removes any dot segment, leaving the page a browser would open.
        try:
            parts = urlsplit(link)
        except ValueError:
            return False
        if parts.scheme != "https":
            return False
        landing = urljoin(link, parts.path)
        return landing == prefix or landing.startswith(prefix + "/")
    return False
