#!/bin/sh
# The site's one command for every check. CI runs exactly this on every pull
# request and on main (.github/workflows/ci.yml), so nothing merges unless all
# of it passes; run it yourself before you push.
#
#   sh ./check.sh
#
# The tools are development-only. Nothing they need ships to the page, which
# stays plain HTML and CSS.
set -eu

cd "$(dirname "$0")"

echo "==> unit tests: this site's own rules, offline"
python3 -m unittest discover -s tests

echo "==> npm ci: the pinned checking tools"
npm ci

echo "==> markdown-it: what a renderer makes of every fact value the puller accepts"
# The oracle for scripts/pull-facts.js, which reads a fact's text with Node's
# standard library alone: a real CommonMark + GFM renderer renders every text
# that gate accepts and must find no off-site href and no bracket outside a
# <code> element.
node tests/render_facts.js

echo "==> html-validate: index.html is valid, well-formed HTML"
npx --no-install html-validate index.html

echo "==> stylelint: colour, font and spacing come only from tokens.css"
# --report-needless-disables so a stylelint-disable comment left behind after
# the CSS it excused has gone is itself a failure.
npx --no-install stylelint index.html tokens.css --report-needless-disables

echo "==> chromium for playwright"
if [ "$(uname -s)" = "Linux" ]; then
  # On Linux the browser needs its system libraries too; on a GitHub runner
  # this is the only place they come from.
  npx --no-install playwright install --with-deps chromium
else
  npx --no-install playwright install chromium
fi

echo "==> playwright + axe: the rendered page at 390px and 1440px"
npx --no-install playwright test

echo "==> all checks passed"
