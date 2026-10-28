#!/usr/bin/env python3
"""Ops watch's check-back: did each shipped brief do what its "how we'll know" said?

`due` lists the briefs whose check-back is due, with what each said; `post <brief>` reads the
verdict as JSON on stdin and posts the check-back in one shape, opening a
`needs-founder` item when the brief did not work; `score` prints the share of shipped briefs that
worked, among those with a verdict. Only the verdict and what happened need judgement, so
everything else is here.

A brief is an issue with a "How we'll know it worked" section. It ships when the last issue cut
from it ("Part of #<brief>") closes as completed, or, for a brief built on its own, when it closes
as completed with `decided`. Its check-back is due the time its section names after that ("Check
back: 3 weeks"), two weeks by default. "Can't tell yet" is checked again one window later, at most
three times in all.

Every call is `gh api` over REST inside this repo, since a routine's cloud proxy blocks GraphQL and
everything outside `repos/<owner>/<repo>/`. The Founding Agents `setup` skill copies this file into
the product. Change it in the plugin's skills/setup/ops_watch/ and rerun setup.
"""

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone

MARK = "**Check-back**"
DEFAULT_WINDOW = timedelta(days=14)
MOST_CHECKS = 3
VERDICTS = {"worked": "worked", "didnt": "didn't", "cant-tell": "can't tell yet"}
RECOMMENDATIONS = ("keep", "change", "remove")

SECTION = re.compile(r"how we(?:'|’)ll know", re.I)
HEADING = re.compile(r"^\s*(#{1,6}\s|\*\*[^*]+\*\*\s*:?\s*$)")
WINDOW = re.compile(r"check[- ]back\D{0,20}?(\d+)\s*(day|week)s?", re.I)
PART_OF = re.compile(r"^\s*Part of #(\d+)\b")
VERDICT_LINE = re.compile(r"^\*\*Verdict:\*\*\s*(.+?)\s*$", re.M)


def gh(*args, input=None):
    result = subprocess.run(["gh", *args], input=input, capture_output=True, text=True)
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def repo_from_origin():
    """<owner>/<repo> from the origin URL; `gh repo view` is GraphQL, which a routine cannot use."""
    url = subprocess.run(["git", "remote", "get-url", "origin"], capture_output=True, text=True).stdout.strip()
    parts = re.split(r"[/:]", url.removesuffix(".git").rstrip("/"))
    return "/".join(parts[-2:]) if len(parts) >= 2 else None


def pages(text):
    """`gh api --paginate` prints one JSON array per page, back to back."""
    items, decoder, at = [], json.JSONDecoder(), 0
    while at < len(text):
        page, at = decoder.raw_decode(text, at)
        items.extend(page)
        while at < len(text) and text[at].isspace():
            at += 1
    return items


def read_all(repo, path, gh=gh):
    code, out, err = gh("api", "--paginate", f"repos/{repo}/{path}")
    if code != 0:
        raise RuntimeError(f"gh api repos/{repo}/{path}: {err}")
    return pages(out)


def when(stamp):
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))


def promise(body):
    """The brief's "how we'll know" section, from its heading to the next one."""
    lines = (body or "").splitlines()
    for start, line in enumerate(lines):
        if SECTION.search(line):
            end = next((i for i in range(start + 1, len(lines)) if HEADING.match(lines[i])), len(lines))
            return "\n".join(lines[start:end]).strip()
    return None


def window(body):
    match = WINDOW.search(body or "")
    if not match:
        return DEFAULT_WINDOW
    number, unit = int(match.group(1)), match.group(2).lower()
    return timedelta(weeks=number) if unit == "week" else timedelta(days=number)


def check_backs(comments):
    """Each check-back posted so far, oldest first, with its verdict."""
    found = []
    for comment in comments:
        body = comment.get("body") or ""
        if body.startswith(MARK):
            verdict = VERDICT_LINE.search(body)
            found.append({"at": when(comment["created_at"]), "verdict": verdict.group(1) if verdict else None})
    return found


def labels(issue):
    return {label["name"] for label in issue.get("labels", [])}


def briefs(issues):
    """Each brief, with the issues cut from it and when it shipped (None while it has not)."""
    issues = [i for i in issues if "pull_request" not in i]
    parts = {}
    for issue in issues:
        match = PART_OF.match(issue.get("body") or "")
        if match:
            parts.setdefault(int(match.group(1)), []).append(issue)
    found = []
    for issue in issues:
        section = promise(issue.get("body"))
        if not section or PART_OF.match(issue.get("body") or ""):
            continue
        cut = parts.get(issue["number"], [])
        built = cut or ([issue] if "decided" in labels(issue) else [])
        done = [i for i in built if i["state"] == "closed" and i.get("state_reason") == "completed"]
        shipped = None
        if done and all(i["state"] == "closed" for i in built):
            shipped = max(when(i["closed_at"]) for i in done)
        found.append({"number": issue["number"], "title": issue["title"], "promise": section,
                      "window": window(issue.get("body")), "shipped": shipped, "parts": [i["number"] for i in cut]})
    return found


def status(brief, comments, now):
    """Where a shipped brief stands: its check-backs, its verdict and when the next one is due."""
    checks = check_backs(comments)
    last = checks[-1]["verdict"] if checks else None
    settled = last in ("worked", "didn't") or len(checks) >= MOST_CHECKS
    since = checks[-1]["at"] if checks else brief["shipped"]
    due_at = None if settled else since + brief["window"]
    return {**brief, "checks": len(checks), "verdict": last, "due_at": due_at,
            "due": due_at is not None and now >= due_at}


def survey(repo, now, gh=gh):
    """Every shipped brief's status, reading comments only for briefs that have shipped."""
    shipped = [b for b in briefs(read_all(repo, "issues?state=all&per_page=100", gh)) if b["shipped"]]
    return [status(b, read_all(repo, f"issues/{b['number']}/comments?per_page=100", gh), now) for b in shipped]


def score(statuses):
    worked = sum(s["verdict"] == "worked" for s in statuses)
    didnt = sum(s["verdict"] == "didn't" for s in statuses)
    waiting = len(statuses) - worked - didnt
    if not worked + didnt:
        line = "Shipped briefs that did what they said: none has a verdict yet"
    else:
        line = (f"Shipped briefs that did what they said: {worked} of {worked + didnt} with a verdict "
                f"({round(100 * worked / (worked + didnt))}%)")
    return f"{line}; {waiting} more can't tell yet or are not due." if waiting else f"{line}."


def quote(text):
    return "\n".join(f"> {line}" if line else ">" for line in text.splitlines())


def decision_item(brief, verdict):
    reason = verdict.get("why") or "no reason given"
    return "\n".join([
        f"**Decision needed:** keep, change or remove what #{brief['number']} built. It did not do what "
        f"its brief said. Ops watch recommends **{verdict['recommend']}**: {reason}",
        "",
        "**What we said:**",
        quote(brief["promise"]),
        "",
        f"**What happened:** {verdict['happened']}",
        "",
        "- **Keep** it as it is: nothing to build, but it stays in the product, and in every check and "
        "review from now on.",
        "- **Change** it: the product lead shapes the change as a new brief, through `propose`.",
        "- **Remove** it: the product lead shapes the removal as a brief, through `propose`. Removing "
        "is cheapest now, before more is built on it.",
        "",
        "To answer, reply with keep, change or remove. Keep closes this issue; for change or remove, "
        "run `/founding-agents:propose <this issue>`.",
    ])


def check_back_comment(brief, verdict, today, item=None, next_at=None, last=False, scoreline=""):
    lines = [
        f"{MARK} on this brief, {today:%d %b %Y}, {brief['checks'] + 1} of at most {MOST_CHECKS}.",
        "",
        "**What we said:**",
        quote(brief["promise"]),
        "",
        f"**What happened:** {verdict['happened']}",
        "",
        f"**Verdict:** {VERDICTS[verdict['verdict']]}",
    ]
    if item:
        lines += ["", f"Whether to keep, change or remove it is the founder's decision: #{item}."]
    if next_at:
        lines += ["", f"Next check-back: {next_at:%d %b %Y}."]
    if last:
        lines += ["", f"This is the last check-back: {MOST_CHECKS} in a row could not tell, so nothing the "
                      "team can read shows whether this worked."]
    return "\n".join(lines + ["", scoreline])


def post(repo, number, verdict, now, gh=gh):
    """Post one check-back on the brief, and a decision item first when it did not work."""
    if verdict.get("verdict") not in VERDICTS:
        raise ValueError(f"verdict must be one of {', '.join(VERDICTS)}")
    if not (verdict.get("happened") or "").strip():
        raise ValueError("say what happened, or that there is no data")
    if verdict["verdict"] == "didnt" and verdict.get("recommend") not in RECOMMENDATIONS:
        raise ValueError(f"a brief that did not work needs a recommendation: {', '.join(RECOMMENDATIONS)}")

    statuses = survey(repo, now, gh)
    brief = next((s for s in statuses if s["number"] == number), None)
    if not brief:
        raise ValueError(f"#{number} is not a brief that has shipped")
    if not brief["due"]:
        raise ValueError(f"#{number} has no check-back due")

    item, name = None, re.sub(r"^Brief:\s*", "", brief["title"])
    if verdict["verdict"] == "didnt":
        code, out, err = gh("api", "-X", "POST", f"repos/{repo}/issues",
                            "-f", f"title=Check-back: #{number} {name} did not do what it said",
                            "-f", f"body={decision_item(brief, verdict)}", "-f", "labels[]=needs-founder")
        if code != 0:
            raise RuntimeError(f"could not open the decision item: {err}")
        item = json.loads(out)["number"]

    brief.update(verdict=VERDICTS[verdict["verdict"]])
    still = verdict["verdict"] == "cant-tell"
    last = still and brief["checks"] + 1 >= MOST_CHECKS
    comment = check_back_comment(brief, verdict, now, item=item,
                                 next_at=now + brief["window"] if still and not last else None,
                                 last=last, scoreline=score(statuses))
    code, out, err = gh("api", "-X", "POST", f"repos/{repo}/issues/{number}/comments", "-f", f"body={comment}")
    if code != 0:
        raise RuntimeError(f"could not post the check-back: {err}")
    return json.loads(out).get("html_url"), item


def describe_due(statuses):
    due = [s for s in statuses if s["due"]]
    if not due:
        return "No check-back is due."
    out = []
    for s in due:
        parts = ", ".join(f"#{n}" for n in s["parts"]) or "none; built as one issue"
        out += [f"## #{s['number']} {s['title']}",
                f"Shipped {s['shipped']:%d %b %Y}; issues cut from it: {parts}; check-back {s['checks'] + 1} "
                f"of at most {MOST_CHECKS}.", "", s["promise"], ""]
    return "\n".join(out).rstrip()


def main(argv=None, gh=gh, now=None, stdin=sys.stdin):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", help="<owner>/<repo>; read from origin by default")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("due", help="list the briefs whose check-back is due, then the score")
    commands.add_parser("score", help="print the share of shipped briefs that worked")
    posting = commands.add_parser("post", help="post a check-back, with the verdict as JSON on stdin: "
                                  '{"verdict": "worked|didnt|cant-tell", "happened": "...", '
                                  '"recommend": "keep|change|remove", "why": "..."}')
    posting.add_argument("brief", type=int)
    args = parser.parse_args(argv)

    repo = args.repo or repo_from_origin()
    if not repo:
        print("Could not tell which repo this is; pass --repo <owner>/<repo>.", file=sys.stderr)
        return 1
    now = now or datetime.now(timezone.utc)
    try:
        if args.command == "post":
            url, item = post(repo, args.brief, json.loads(stdin.read()), now, gh)
            print(f"Posted {url}" + (f"; the founder's decision is #{item}" if item else ""))
            return 0
        statuses = survey(repo, now, gh)
    except (RuntimeError, ValueError, json.JSONDecodeError) as problem:
        print(problem, file=sys.stderr)
        return 1
    if args.command == "due":
        print(describe_due(statuses), end="\n\n")
    print(score(statuses))
    return 0


if __name__ == "__main__":
    sys.exit(main())
