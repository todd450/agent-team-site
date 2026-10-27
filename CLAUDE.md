# Founding Agents website

The website for Founding Agents, served at https://todd450.github.io/founding-agents-site/. It is the
team's first product and its test bed: one page, `index.html`, for a founder deciding whether to
use the team. How it is built and deployed is being decided in #14.

Built by the Founding Agents team with one founder. This file is a short map, not a rulebook: a
rule that must hold becomes a test in `check.sh`, a lint rule or a hook, and its evidence goes in
`docs/`.

- **Checks:** `./check.sh` runs every check, and CI runs it on each pull request. Reach for a
  standard validator, linter or browser test before writing a check by hand.
- **Decisions:** an issue labelled `needs-founder` waits on the founder, and `decided` is their
  yes. Technical one-way doors (a framework or none, a build step, how it deploys, its stack, how
  it stores data, a dependency that ships, an outside service) are the founder's; each is
  recorded in the issue that decided it. Never settle one in code.
- **Edge cases:** the visible scenarios are in `fixtures/`, one folder per failure class.
- **Shipping:** changes reach `main` only through a pull request that the integrator merges.
- **This product's own rules:**
  - Colour, font and spacing come only from `tokens.css` (stylelint).
  - The page is valid HTML (html-validate) and passes axe at 390px and 1440px (Playwright).
  - The npm packages are for the checks only; nothing they bring ships to the page.
