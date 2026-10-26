"""Every token the page uses is defined in tokens.css."""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class TokenTest(unittest.TestCase):
    def test_every_token_used_is_defined(self):
        defined = set(re.findall(r"(--[\w-]+)\s*:", (ROOT / "tokens.css").read_text()))
        used = set(re.findall(r"var\((--[\w-]+)", (ROOT / "index.html").read_text()))
        self.assertEqual(used - defined, set())


if __name__ == "__main__":
    unittest.main()
