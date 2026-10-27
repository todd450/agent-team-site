# Fixtures

The visible edge-case scenarios. Builders write their tests from these, and edge-case QA runs them
on every change. The Founding Agents QA agent also runs holdout scenarios kept outside this repo,
which builders never see, so a change has to handle each whole failure class, not just these cases.

One folder per failure class, made when it gets its first scenario:

- `empty-and-one/`: no items, one item, and the most there can be
- `data-and-account-shapes/`: other shapes of data and account than the usual one
- `other-language/`: every language the product ships, and the longest text
- `phone-width/`: a phone-width screen, and the widest
- `failed-read/`: a read or write that fails, is slow, or happens twice
- `second-user/`: a second person, and someone who should not see it
- `time-zones/`: other time zones, midnight, month ends and daylight saving

A scenario is whatever the product's tests can load: a data file, a saved page, or a Markdown file
with its setup, steps and what is expected. A holdout a builder has seen moves here, and a new
holdout takes its place.
