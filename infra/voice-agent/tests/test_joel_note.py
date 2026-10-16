import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import joel_note


class JoelNoteTest(unittest.TestCase):
    def test_one_note_per_event_and_no_latch_on_failure(self) -> None:
        calls = []

        def ok(cmd, **_):
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps({"ok": True, "status": 200}) + "\n", stderr="")

        def bad(cmd, **_):
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 1, stdout=json.dumps({"ok": False, "status": 500}), stderr="")

        with tempfile.TemporaryDirectory() as d, patch.object(joel_note, "SENT_DIR", Path(d)):
            self.assertFalse(joel_note.send("room-a", "hello", runner=bad)["ok"])
            self.assertTrue(joel_note.send("room-a", "hello", runner=ok)["ok"])
            again = joel_note.send("room-a", "hello", runner=ok)
        self.assertEqual(again.get("skipped"), "already sent for this event")
        self.assertEqual(len(calls), 2)
        self.assertIn("ShitRat call line", calls[0])

    def test_receipt_survives_trailing_log_lines_and_bad_output(self) -> None:
        self.assertEqual(joel_note.last_receipt('{"ok": true}\nsome log line\n')["ok"], True)
        with self.assertRaises(ValueError):
            joel_note.last_receipt('["not", "a", "dict"]\n')

        def junk(cmd, **_):
            return subprocess.CompletedProcess(cmd, 0, stdout="[1]\n", stderr="")

        with tempfile.TemporaryDirectory() as d, patch.object(joel_note, "SENT_DIR", Path(d)):
            self.assertFalse(joel_note.send("room-b", "hi", runner=junk)["ok"])
            self.assertFalse(joel_note.key_path("room-b").exists())

    def test_a_claimed_event_is_never_sent_twice(self) -> None:
        calls = []

        def ok(cmd, **_):
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout='{"ok": true}', stderr="")

        with tempfile.TemporaryDirectory() as d, patch.object(joel_note, "SENT_DIR", Path(d)):
            joel_note.key_path("room-c").write_text("")  # another run holds the claim
            self.assertEqual(joel_note.send("room-c", "hi", runner=ok).get("skipped"), "already sent for this event")
        self.assertEqual(calls, [])

    def test_plain_words(self) -> None:
        text = joel_note.plain("**Ship** `abc1234def` now\n\n# please " + "x" * 600)
        self.assertNotIn("abc1234def", text)
        self.assertNotIn("*", text)
        self.assertNotIn("#", text)
        self.assertLessEqual(len(text), joel_note.MAX_CHARS)


if __name__ == "__main__":
    unittest.main()
