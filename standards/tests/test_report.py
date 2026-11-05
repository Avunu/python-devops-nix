import json
import unittest

from support import KIT, STUB

from standards_core.common import report
from standards_core.common.report import Finding

FINDINGS = [Finding("ci.yml", "whole", "differs", "--- current\n+++ rendered\n")]


def render(*args, product=STUB):
    return report.render(*args, product=product)


class TestReport(unittest.TestCase):
    def test_worst(self):
        self.assertEqual(report.worst(), 0)
        self.assertEqual(report.worst(1, 3, 2), 3)

    def test_errors_carry_their_codes(self):
        self.assertEqual(report.StandardsError("x").code, 3)
        self.assertEqual(report.ConfigError("x").code, 2)
        self.assertEqual(report.EnvError("x").code, 3)
        self.assertTrue(issubclass(report.ConfigError, report.StandardsError))

    def test_text(self):
        out = render(FINDINGS, "text", 1)
        self.assertIn("ci.yml (whole): differs", out)
        self.assertTrue(
            out.endswith("stub-nix: 1 file(s) drifted — run `stub-init --sync`\n")
        )
        self.assertEqual(render([], "text", 0), "stub-nix: clean\n")
        self.assertEqual(render([], "text", 0, product=KIT), "kit-nix: clean\n")

    def test_github(self):
        self.assertIn("::error file=ci.yml::differs", render(FINDINGS, "github", 1))
        self.assertEqual(render([], "github", 0), "stub-nix: clean\n")

    def test_github_escapes_and_fences(self):
        findings = [
            Finding("docs/a,b:c.md", "whole", "differs: 100% wrong\nsecond line"),
            Finding(
                "x\n::add-mask::y.txt",
                "retire",
                "legacy file",
                " ::warning::from a diff\n",
            ),
        ]
        lines = render(findings, "github", 1).splitlines()
        self.assertIn(
            "::error file=docs/a%2Cb%3Ac.md::differs: 100%25 wrong%0Asecond line", lines
        )
        self.assertIn("::error file=x%0A%3A%3Aadd-mask%3A%3Ay.txt::legacy file", lines)
        # Every line that would run as a workflow command is either one of ours or inside
        # the stop-commands fence, whose token nothing in the text knows.
        start = lines[0]
        self.assertTrue(start.startswith("::stop-commands::"))
        token = start.removeprefix("::stop-commands::")
        end = lines.index(f"::{token}::")
        for line in lines[end + 1 :]:
            self.assertTrue(line.startswith(("::error file=", "stub-nix:")), line)
        self.assertIn("::add-mask::y.txt (retire): legacy file", lines[1:end])

    def test_json(self):
        out = render(FINDINGS, "json", 1, {"rev": "abc", "version": "1.0.0"})
        doc = json.loads(out)
        self.assertEqual(doc["status"], "drift")
        self.assertEqual(doc["stub_nix"]["rev"], "abc")
        self.assertEqual(doc["files"][0]["path"], "ci.yml")
        # The key order is fixed: status, the engine key, files.
        self.assertEqual(list(doc), ["status", "stub_nix", "files"])
        self.assertEqual(
            list(doc["files"][0]), ["path", "strategy", "module", "problem", "diff"]
        )
        kit = json.loads(render([], "json", 0, product=KIT))
        self.assertEqual(kit, {"status": "clean", "kit_nix": {}, "files": []})

    def test_unknown_format(self):
        with self.assertRaises(report.ConfigError):
            render([], "xml", 0)


if __name__ == "__main__":
    unittest.main()
