# -*- coding: utf-8 -*-
"""A feltöltött források kidolgozott példái (tests/reference/source_examples/*.json).

Csak azok az esetek aktívak, amelyeket egy független ellenőrző a forrás nyomtatott
értékeire reprodukálni tudott; a többi 'known_gap' indoklással (pl. a motor nem
implementálja a módszert, vagy a forrás hibás).
"""
import unittest

import _helpers  # noqa: F401  (sys.path)
from source_cases import load_cases, check_case


class TestSourceExamples(unittest.TestCase):
    def test_cases(self):
        cases = load_cases()
        active = [c for c in cases if c.get("status", "active") == "active"]
        if not cases:
            self.skipTest("nincsenek forrás-esetek")
        failures = []
        for c in active:
            for path, want, got, tol, ok, msg in check_case(c):
                if not ok:
                    failures.append("%s [%s] %s: várt %r, kapott %r, tol %r %s" % (
                        c["case_id"], c.get("location", ""), path, want, got, tol, msg))
        self.assertEqual(failures, [], "\n" + "\n".join(failures))

    def test_case_metadata(self):
        for c in load_cases():
            for key in ("case_id", "source_id", "location", "call", "expected"):
                self.assertIn(key, c, c.get("case_id"))
            if c.get("status") == "known_gap":
                self.assertTrue(c.get("gap_reason"), c["case_id"])


if __name__ == "__main__":
    unittest.main()
