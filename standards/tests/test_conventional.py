"""Conventional-commit parsing, bump levels and the version schemes (DESIGN §8.2)."""

import unittest

from support import KIT, STUB

from standards_core.ci import conventional
from standards_core.ci.conventional import SEMVER, Commit, level, parse
from standards_core.common.report import ConfigError


class TestParse(unittest.TestCase):
    def test_subject_parts(self):
        self.assertEqual(
            parse("feat(api): add x"), Commit("feat", "api", False, "add x")
        )
        self.assertEqual(parse("fix: y\n\nbody"), Commit("fix", None, False, "y"))
        self.assertEqual(parse("Feat!: z"), Commit("feat", None, True, "z"))
        self.assertEqual(parse("refactor(a)!: w").breaking, True)

    def test_breaking_footers(self):
        self.assertTrue(parse("fix: y\n\nBREAKING CHANGE: gone").breaking)
        self.assertTrue(parse("fix: y\n\nBREAKING-CHANGE: gone").breaking)
        self.assertFalse(parse("fix: y\n\nnot a BREAKING CHANGE: inline").breaking)

    def test_comments_and_blank_lines_are_skipped(self):
        self.assertEqual(
            parse("# Please enter the message\n\nfeat: x\n"),
            Commit("feat", None, False, "x"),
        )

    def test_not_conventional(self):
        for message in ("", "Merge branch 'x'", "update stuff", "feat x", "(a): b"):
            with self.subTest(message=message):
                self.assertIsNone(parse(message))


class TestLevel(unittest.TestCase):
    def test_levels(self):
        def lv(*messages):
            return level(parse(m) for m in messages)

        self.assertEqual(lv("fix: a"), "patch")
        self.assertEqual(lv("perf: a"), "patch")
        self.assertEqual(lv("fix: a", "feat: b"), "minor")
        self.assertEqual(lv("feat: b", "chore!: c"), "major")
        self.assertEqual(lv("fix: a\n\nBREAKING CHANGE: x"), "major")
        self.assertIsNone(lv("chore: a", "docs: b", "not conventional"))
        self.assertIsNone(lv())

    def test_custom_types(self):
        self.assertEqual(level([parse("docs: a")], {"docs": "patch"}), "patch")
        self.assertIsNone(level([parse("feat: a")], {"docs": "patch"}))


class TestSemver(unittest.TestCase):
    def test_bump(self):
        self.assertEqual(SEMVER.bump("1.2.3", "patch", {}, {}), "1.2.4")
        self.assertEqual(SEMVER.bump("1.2.3", "minor", {}, {}), "1.3.0")
        self.assertEqual(SEMVER.bump("1.2.3", "major", {}, {}), "2.0.0")
        with self.assertRaises(ConfigError):
            SEMVER.bump("1.2", "patch", {}, {})

    def test_rules(self):
        self.assertIsNone(SEMVER.valid("1.2.3", {}, {}))
        self.assertEqual(
            SEMVER.valid("v1.2.3", {}, {}), "v1.2.3 is not <major>.<minor>.<patch>"
        )
        self.assertEqual(SEMVER.message_problems("feat!: x", {}, {}), [])
        self.assertIsNone(SEMVER.tag_problem("anything", {}, {}))

    def test_lookup(self):
        self.assertIs(conventional.scheme(STUB, "semver"), SEMVER)
        self.assertIs(
            conventional.scheme(STUB, "stub-major"), STUB.version_schemes["stub-major"]
        )
        self.assertEqual(list(conventional.schemes(KIT)), ["semver", "kit-series"])
        with self.assertRaisesRegex(
            ConfigError, "'kit-series' is not one of semver, stub-major"
        ):
            conventional.scheme(STUB, "kit-series")


class TestKitSeries(unittest.TestCase):
    """The kit's ``kit-series``: ``<series>.x.y.z``."""

    scheme = KIT.version_schemes["kit-series"]

    def test_arithmetic(self):
        facts = {"series": "18.0"}
        self.assertEqual(
            self.scheme.bump("18.0.1.2.3", "minor", {}, facts), "18.0.1.3.0"
        )
        self.assertEqual(
            self.scheme.bump("18.0.1.2.3", "patch", {}, facts), "18.0.1.2.4"
        )
        self.assertEqual(
            self.scheme.bump("18.0.1.2.3", "major", {}, facts), "18.0.2.0.0"
        )

    def test_valid_checks_the_series(self):
        self.assertIsNone(self.scheme.valid("18.0.1.0.0", {}, {"series": "18.0"}))
        self.assertEqual(
            self.scheme.valid("17.0.1.0.0", {}, {"series": "18.0"}),
            "17.0.1.0.0 is not on series 18.0",
        )


if __name__ == "__main__":
    unittest.main()
