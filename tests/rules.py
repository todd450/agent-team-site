"""The one way this suite asks what a valid fact value is.

The rules -- where a link may point, how a fact's text is read to find its
links, what it quotes as code rather than putting on the page as markup, which
characters it may not carry, what a row and a whole snapshot must look like --
are defined once, in `scripts/pull-facts.js`, because that is the gate on what
enters `content/facts.json` (decided on #27, 28 Oct 2026). This module holds
none of them: it hands the questions to `tests/check_facts.js` and returns
what the puller answered.

That is the point. The same rules used to be written twice, here and in the
puller, and the two drifted: `trim` and `strip` disagree about which
characters are whitespace, so a pull could write a snapshot that this suite
then rejected -- a reviewed file that had to be hand-edited before it could
ship. With one definition that cannot happen, and a hole opened in it is a
failure in `tests/test_rules.py` rather than a link on the page.

Nothing here makes a network call: the puller is asked about values, never
run against Supabase.
"""

import json
import shutil
import subprocess
from pathlib import Path

CHECK_FACTS_JS = Path(__file__).resolve().parent / "check_facts.js"
NODE = shutil.which("node")


def ask(**questions):
    """Put questions to the puller and return its answers. One process per
    call, so a caller with many cases asks them all at once."""
    if NODE is None:  # pragma: no cover - CI and check.sh both have Node
        raise RuntimeError("node is needed: it holds the one definition of a valid fact value")
    result = subprocess.run([NODE, str(CHECK_FACTS_JS)], input=json.dumps(questions),
                            capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        raise RuntimeError(f"the puller could not answer: {result.stderr.strip()}")
    return json.loads(result.stdout)


_SHAPE = None


def _shape():
    """The kinds and the allowlist, as the puller defines them. Asked once."""
    global _SHAPE
    if _SHAPE is None:
        _SHAPE = ask()
    return _SHAPE


def kinds():
    """{table: [column, ...]} for the four kinds, in the order the snapshot
    holds them -- the shape the generator is written against."""
    return {kind["table"]: kind["columns"] for kind in _shape()["kinds"]}


def optional_columns():
    """The (table, column) pairs a fact may leave empty, because the fact has
    nothing to put there."""
    return {(kind["table"], column)
            for kind in _shape()["kinds"] for column in kind.get("optional", [])}


def listed_columns():
    """{(table, column): [word, ...]} for the columns the tables restrict to a
    short list of words."""
    return {(kind["table"], column): words
            for kind in _shape()["kinds"] for column, words in kind.get("lists", {}).items()}


def prefixes():
    """The two addresses a link in this repo may point inside."""
    return tuple(_shape()["prefixes"])


REPO_PREFIX, PAGES_PREFIX = prefixes()


def snapshot_problems(snapshot, today=None):
    """Why this snapshot is not one the build may read, or [] when it is.

    `snapshot` is a path to a snapshot file or a snapshot itself; `today` is
    the day to check it as of, which no date in it may be after.
    """
    path_or_facts = str(snapshot) if isinstance(snapshot, Path) else snapshot
    return ask(snapshot=path_or_facts, today=today)["snapshot"]


def allowed_links(links):
    """Whether a browser following each of these would land inside the
    allowlist."""
    return ask(links=list(links))["links"]


def allowed_link(link):
    return allowed_links([link])[0]


def links_in(texts):
    """(targets, unreadable) for each text: where its links point, and whether
    it writes one the reader could not read whole."""
    return [(answer["targets"], answer["unreadable"]) for answer in ask(texts=list(texts))["texts"]]


def link_targets(text):
    return links_in([text])[0]


def markup_in(texts):
    """What each text would put on the page as markup, or the character that
    makes it unreadable."""
    return ask(markup=list(texts))["markup"]


def markup_outside_backticks(text):
    return markup_in([text])[0]


def invisible_in(texts):
    """The code points of the characters in each text that a reader cannot
    see: a control, zero-width or direction character."""
    return ask(invisible=list(texts))["invisible"]


def invisible_characters(text):
    return invisible_in([text])[0]
