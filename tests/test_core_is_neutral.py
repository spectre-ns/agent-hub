"""The core knows no one domain: what is finance (or any other trade) belongs in a pack. A word of one trade in the
core is a sign that a rule of that trade has crept in where every practice runs."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "practicehub"
# whole words of one trade, any case: "syntax" and "credentials" are not "tax" and "credit"
TRADE = re.compile(r"\b(invoices?|ledgers?|fiscal|debits?|credits?|tax(?:es)?|hst|gst|bookkeep\w*|accounting|"
                   r"accountants?)\b", re.I)


def test_the_core_names_no_trade_s_own_words():
    found = []
    for path in sorted(ROOT.rglob("*.py")) + sorted((ROOT / "base").glob("*.toml")):
        for n, line in enumerate(path.read_text().splitlines(), 1):
            if m := TRADE.search(line):
                found.append(f"{path.relative_to(ROOT.parent)}:{n}: {m.group(0)!r}")
    assert found == [], "a trade's own words in the core (put the rule in a pack):\n" + "\n".join(found)
