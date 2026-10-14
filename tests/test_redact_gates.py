"""The word gates of loglens.redact are complete (BRIEF-DEVWS-027).

For the multi-word, spoken-form and key=value rules, every keyword alternative
the rule's own regex accepts is enumerated from the module's keyword patterns
(the same strings the rule is built from, checked to be inside the rule) and
must be found by the rule's gate, in every case variant the rule accepts,
including U+017F (long s) and U+212A (Kelvin sign)."""
import re
import unittest
from re import _constants as C
from re import _parser

from loglens import redact as R

LONG_S = "\u017f"
KELVIN = "\u212a"


def _strings(items):
    out = [""]
    for op, av in items:
        alternatives = _node(op, av)
        out = [a + b for a in out for b in alternatives]
    return out


def _node(op, av):
    if op is C.LITERAL:
        return [chr(av)]
    if op is C.IN:
        chars = []
        for kind, value in av:
            if kind is C.LITERAL:
                chars.append(chr(value))
            elif kind is C.RANGE:
                chars.extend(chr(c) for c in range(value[0], value[1] + 1))
            else:
                raise ValueError(f"unsupported set item {kind}")
        return chars
    if op is C.BRANCH:
        return [s for branch in av[1] for s in _strings(branch)]
    if op is C.SUBPATTERN:
        return _strings(av[-1])
    if op in (C.MAX_REPEAT, C.MIN_REPEAT, C.POSSESSIVE_REPEAT):
        low, high, sub = av
        if high is C.MAXREPEAT:
            raise ValueError("unbounded repeat in a keyword pattern")
        parts = _strings(sub)
        out = []
        for n in range(low, high + 1):
            seq = [""]
            for _ in range(n):
                seq = [a + b for a in seq for b in parts]
            out.extend(seq)
        return out
    if op in (C.AT, C.ASSERT, C.ASSERT_NOT):
        return [""]       # zero-width: adds no characters
    raise ValueError(f"unsupported regex node {op}")


def alternatives(source):
    """Every string the (finite) keyword pattern accepts, ignoring case."""
    return sorted(set(_strings(_parser.parse(source, re.IGNORECASE))))


def case_variants(word):
    """Spellings the (?i) rule also accepts: upper, lower, mixed, and every
    s as long s or k as Kelvin sign (one and all)."""
    mixed = "".join(c.upper() if i % 2 else c.lower() for i, c in enumerate(word))
    out = {word, word.lower(), word.upper(), word.title(), mixed}
    for ch, alt in (("s", LONG_S), ("k", KELVIN)):
        if ch in word.lower():
            low = word.lower()
            out.add(low.replace(ch, alt))
            out.add(low.replace(ch, alt, 1))
            out.add(word.upper().replace(ch.upper(), alt))
    return sorted(out)


# rule index -> (gate, keyword sources the rule is built from)
RULE_KEYWORDS = {
    4: (R._multi_keys_gate, (R._MULTI, R._PERSONAL)),
    5: (R._MULTI_GATE, (R._MULTI,)),
    6: (R._keys_gate, (R._LONG, R._SHORT, R._PERSONAL, R._LAST)),
}


class TestWordGates(unittest.TestCase):
    def test_gates_belong_to_their_rules(self):
        for index, (gate, sources) in RULE_KEYWORDS.items():
            with self.subTest(rule=index):
                rule_gate, pattern, _ = R._RULES[index]
                self.assertIs(rule_gate, gate)
                self.assertTrue(pattern.flags & re.IGNORECASE)
                for source in sources:
                    self.assertIn(source, pattern.pattern)
        for search in (R._MULTI_GATE, R._MULTI_KEYS_GATE, R._KEYS_GATE):
            self.assertTrue(search.__self__.flags & re.IGNORECASE)

    def test_every_keyword_alternative_passes_its_gate(self):
        total = 0
        for index, (gate, sources) in RULE_KEYWORDS.items():
            for source in sources:
                words = alternatives(source)
                self.assertTrue(words)
                keyword = re.compile(source, re.IGNORECASE)
                for word in words:
                    for variant in case_variants(word):
                        with self.subTest(rule=index, variant=ascii(variant)):
                            # The rule's own keyword pattern accepts it ...
                            self.assertTrue(keyword.fullmatch(variant))
                            # ... so its gate must let the rule run.
                            self.assertTrue(gate(variant + "=x"))
                        total += 1
        # _LONG, _SHORT, _PERSONAL, _LAST and _MULTI expand to dozens of words.
        self.assertGreater(total, 300)

    def test_expansion_covers_known_words(self):
        words = alternatives(R._LONG)
        self.assertTrue(words)
        for word in ("password", "api_key", "api-key", "apikey", "privatekey",
                     "signature", "cookie"):
            self.assertIn(word, words)
        self.assertEqual(alternatives(R._SHORT), ["auth", "pass", "pwd", "sig"])
        self.assertIn("sessid", alternatives(R._LAST))
        self.assertIn("date-of_birth", alternatives(R._PERSONAL))


if __name__ == "__main__":
    unittest.main()
