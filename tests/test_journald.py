"""python/journald.py: the priority mapping, the JOURNAL_STREAM check, the
line format with a prefix on every line, and ANSI stripping.

Run: python3 -I tests/test_journald.py <python/>

Standard library only, and run on every interpreter the products' presets name
(lib.journald.check), since the core loads into the application's own.
"""

import logging
import os
import sys
import tempfile
import unittest
from pathlib import Path

if len(sys.argv) < 2:
    sys.exit("usage: test_journald.py <python dir>")
sys.path.insert(0, str(Path(sys.argv.pop(1)).resolve()))

import journald


def record(level=logging.WARNING, msg="hello", name="stub.app", exc=False, args=None):
    exc_info = None
    if exc:
        try:
            raise ValueError("boom")
        except ValueError:
            exc_info = sys.exc_info()
    return logging.LogRecord(name, level, __file__, 1, msg, args, exc_info)


class Priority(unittest.TestCase):
    def test_levels(self):
        for level, expected in (
            (logging.DEBUG, 7),
            (logging.INFO, 6),
            (logging.WARNING, 4),
            (logging.ERROR, 3),
            (logging.CRITICAL, 2),
        ):
            self.assertEqual(
                journald.priority(level), expected, logging.getLevelName(level)
            )

    def test_levels_in_between_land_below(self):
        self.assertEqual(journald.priority(9), 7)  # Odoo's DEBUG_RPC
        self.assertEqual(journald.priority(15), 7)  # bench's LOG
        self.assertEqual(journald.priority(25), 6)  # Odoo's RUNBOT
        self.assertEqual(journald.priority(45), 3)
        self.assertEqual(journald.priority(60), 2)
        self.assertEqual(journald.priority(0), 7)

    def test_parse_level(self):
        self.assertEqual(journald.parse_level(" Warn "), logging.WARNING)
        self.assertEqual(journald.parse_level("debug"), logging.DEBUG)
        self.assertIsNone(journald.parse_level("loud"))
        self.assertIsNone(journald.parse_level(None))


class JournalStream(unittest.TestCase):
    def setUp(self):
        self.saved = os.environ.pop("JOURNAL_STREAM", None)
        self.file = tempfile.TemporaryFile()  # noqa: SIM115 -- closed by addCleanup
        self.addCleanup(self.file.close)

    def tearDown(self):
        os.environ.pop("JOURNAL_STREAM", None)
        if self.saved is not None:
            os.environ["JOURNAL_STREAM"] = self.saved

    def point_at(self, fd):
        st = os.fstat(fd)
        os.environ["JOURNAL_STREAM"] = f"{st.st_dev}:{st.st_ino}"

    def test_unset(self):
        self.assertFalse(journald.journal_stream(self.file.fileno()))

    def test_matches_the_descriptor(self):
        self.point_at(self.file.fileno())
        self.assertTrue(journald.journal_stream(self.file.fileno()))

    def test_inherited_but_redirected(self):
        with tempfile.TemporaryFile() as other:
            self.point_at(other.fileno())
            self.assertFalse(journald.journal_stream(self.file.fileno()))

    def test_malformed(self):
        for value in ("", "garbage", "1", "a:b", "1:2:3"):
            os.environ["JOURNAL_STREAM"] = value
            self.assertFalse(journald.journal_stream(self.file.fileno()), value)

    def test_closed_descriptor(self):
        self.point_at(self.file.fileno())
        fd = os.dup(self.file.fileno())
        os.close(fd)
        self.assertFalse(journald.journal_stream(fd))

    def test_defaults_to_stderr(self):
        self.point_at(2)
        self.assertTrue(journald.journal_stream())


class Format(unittest.TestCase):
    def test_line(self):
        out = journald.JournaldFormatter().format(record())
        self.assertEqual(out, "<4>stub.app hello")

    def test_tenant(self):
        formatter = journald.JournaldFormatter(tenant=lambda r: "erp.example.com")
        self.assertEqual(
            formatter.format(record(logging.INFO)),
            "<6>stub.app [erp.example.com] hello",
        )

    def test_no_tenant_no_brackets(self):
        formatter = journald.JournaldFormatter(tenant=lambda r: None)
        self.assertEqual(formatter.format(record()), "<4>stub.app hello")
        formatter = journald.JournaldFormatter(tenant=lambda r: "")
        self.assertEqual(formatter.format(record()), "<4>stub.app hello")

    def test_tenant_that_raises_keeps_the_record(self):
        def broken(r):
            raise RuntimeError("torn down")

        formatter = journald.JournaldFormatter(tenant=broken)
        self.assertEqual(formatter.format(record()), "<4>stub.app hello")

    def test_fixed_logger(self):
        formatter = journald.JournaldFormatter(logger="frappe", tenant=lambda r: "s")
        out = formatter.format(record(name="frappe-erp.example.com"))
        self.assertEqual(out, "<4>frappe [s] hello")

    def test_tenant_from_the_record(self):
        formatter = journald.JournaldFormatter(
            tenant=lambda r: getattr(r, "dbname", None)
        )
        rec = record(logging.ERROR)
        rec.dbname = "acme"
        self.assertEqual(formatter.format(rec), "<3>stub.app [acme] hello")

    def test_args(self):
        out = journald.JournaldFormatter().format(record(msg="%s=%d", args=("n", 3)))
        self.assertEqual(out, "<4>stub.app n=3")

    def test_every_line_prefixed(self):
        out = journald.JournaldFormatter(tenant=lambda r: "s").format(
            record(logging.ERROR, "failed", exc=True)
        )
        lines = out.split("\n")
        self.assertEqual(lines[0], "<3>stub.app [s] failed")
        self.assertIn("<3>Traceback (most recent call last):", lines)
        self.assertEqual(lines[-1], "<3>ValueError: boom")
        self.assertTrue(all(line.startswith("<3>") for line in lines), lines)

    def test_multiline_message(self):
        out = journald.JournaldFormatter().format(
            record(logging.CRITICAL, "one\ntwo\n\nfour\n")
        )
        self.assertEqual(out, "<2>stub.app one\n<2>two\n<2>\n<2>four")

    def test_ansi_stripped(self):
        msg = "\x1b[1;31mred\x1b[0m \x1b]8;;https://example.com\x07link\x1b]8;;\x07 \x1b(Bdone"
        out = journald.JournaldFormatter().format(record(msg=msg))
        self.assertEqual(out, "<4>stub.app red link done")

    def test_message_override(self):
        class WithPerf(journald.JournaldFormatter):
            def message(self, record):
                text = super().message(record)
                perf = getattr(record, "perf_info", "")
                return f"{text} {perf}" if perf else text

        rec = record(logging.INFO)
        rec.perf_info = "3 0.002 0.010"
        self.assertEqual(WithPerf().format(rec), "<6>stub.app hello 3 0.002 0.010")

    def test_prefix_lines(self):
        self.assertEqual(journald.prefix_lines("a\nb", 5), "<5>a\n<5>b")


if __name__ == "__main__":
    unittest.main(verbosity=2)
