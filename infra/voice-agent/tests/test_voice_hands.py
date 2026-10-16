import subprocess
import time
import unittest
from unittest.mock import MagicMock, patch

import ratking_cli
import voice_desk
import voice_snapshot

DIRECTORY = [
    {"name": "switchboard", "callsign": "ernestine", "project": None, "label": "switchboard", "role": "owner"},
    {"name": "caboose/desk", "callsign": "Piper", "project": "caboose", "label": "Piper", "role": "desk"},
    {"name": "rubicon/rubicon-desk", "callsign": None, "project": "rubicon", "label": "Rubicon", "role": "desk"},
    {"name": "rubicon-fitness/rubicon-fitness-desk", "callsign": None, "project": "rubicon-fitness", "label": "rubicon-fitness", "role": "desk"},
    {"name": "drovr/drovr-desk-2", "callsign": "Bravestarr", "project": "drovr", "label": "drovr", "role": "desk"},
    {"name": "drovr/alert-boss", "callsign": "Shaman", "project": "drovr", "label": "drovr", "role": "boss"},
]


class ResolveTest(unittest.TestCase):
    def test_spoken_names_resolve_to_fleet_names(self) -> None:
        self.assertEqual([e["name"] for e in voice_desk.resolve("the switchboard", DIRECTORY)], ["switchboard"])
        self.assertEqual([e["name"] for e in voice_desk.resolve("Piper", DIRECTORY)], ["caboose/desk"])
        self.assertEqual([e["name"] for e in voice_desk.resolve("rubicon", DIRECTORY)], ["rubicon/rubicon-desk"])

    def test_desk_beats_other_rows_of_the_project(self) -> None:
        self.assertEqual([e["name"] for e in voice_desk.resolve("drovr", DIRECTORY)], ["drovr/drovr-desk-2"])

    def test_unknown_names_resolve_to_nothing(self) -> None:
        self.assertEqual(voice_desk.resolve("my accountant", DIRECTORY), [])
        self.assertEqual(voice_desk.resolve("", DIRECTORY), [])

    def test_find_item_by_id_or_unique_title(self) -> None:
        items = [
            {"id": "41a8c6ca", "title": "Remove our 8 leftover test athletes", "project": "rubicon-fitness"},
            {"id": "e9c9ad8f", "title": "Name the extracted celld provider", "project": "content-grill"},
        ]
        self.assertEqual(voice_desk.find_item("41a8c6ca", items)["id"], "41a8c6ca")
        self.assertEqual(voice_desk.find_item("the celld provider name", items)["id"], "e9c9ad8f")
        self.assertIsNone(voice_desk.find_item("something else", items))


class SnapshotTest(unittest.TestCase):
    def test_open_items_skip_resolved_and_non_open_kinds(self) -> None:
        items = [
            {"id": "a", "kind": "decision", "title": "x"},
            {"id": "b", "kind": "approval", "title": "y"},
            {"id": "c", "kind": "fyi", "title": "z"},
            {"id": "d", "kind": "done", "resolves": "a", "title": 'Joel: "yes"'},
        ]
        self.assertEqual([i["id"] for i in voice_snapshot.open_items(items)], ["b"])
        self.assertTrue(voice_snapshot.is_ruling(items[3]))
        self.assertFalse(voice_snapshot.is_ruling({"kind": "done", "title": "Joel: yes"}))

    def test_render_says_stale_and_missing(self) -> None:
        built = time.time() - 3 * 3600
        snap = {"builtAt": voice_snapshot._iso(built), "open": [], "missing": ["mail: down"], "openCount": 0}
        text = voice_snapshot.render(snap)
        self.assertIn("STALE: built 3 hours ago", text)
        self.assertIn("mail: down", text)
        fresh = voice_snapshot.render({**snap, "builtAt": voice_snapshot._iso(time.time()), "missing": []})
        self.assertNotIn("STALE", fresh)
        self.assertIn("never guess", voice_snapshot.render(None))

    def test_dial_build_skips_mail_without_a_cursor(self) -> None:
        with patch.object(ratking_cli, "peek") as peek:
            mail, seq, missing = voice_snapshot.escalations(None, fast=True)
        peek.assert_not_called()
        self.assertEqual((mail, seq), ([], None))
        self.assertIn("no cursor", missing)


def _proc(code: int, out: str = "", err: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], code, out, err)


class RatKingCliTest(unittest.TestCase):
    def test_exit_codes_map_to_honest_outcomes(self) -> None:
        cases = [
            (_proc(0, '{"ok":true,"delivered":{"id":"3mx1"}}'), "delivered"),
            (_proc(2, "", "NOT DELIVERED: UnknownName."), "refused"),
            (_proc(3, "", "UNCERTAIN: no receipt"), "uncertain"),
            (_proc(1, "", "boom"), "error"),
        ]
        for proc, status in cases:
            with patch.object(subprocess, "run", return_value=proc):
                result = ratking_cli.send("switchboard", "s", "b")
            self.assertEqual(result.status, status)
        self.assertIn("Do not resend", ratking_cli.SendResult("uncertain", "x").spoken())

    def test_timeout_is_uncertain_never_failed(self) -> None:
        with patch.object(subprocess, "run", side_effect=subprocess.TimeoutExpired("node", 2)):
            result = ratking_cli.send("switchboard", "s", "b")
        self.assertEqual(result.status, "uncertain")

    def test_send_never_ccs_the_recipient(self) -> None:
        with patch.object(subprocess, "run", return_value=_proc(0, '{"delivered":{"id":"x"}}')) as run:
            ratking_cli.send("switchboard", "s", "b", cc=["switchboard"])
        self.assertNotIn("--cc", run.call_args.args[0])
        self.assertIn("switchboard/voice", run.call_args.args[0])


class MainHelpersTest(unittest.TestCase):
    def test_join_back_reports_words_sends_and_timing(self) -> None:
        import main

        actions = [
            {"type": "draft", "draft": "d1", "kind": "tell", "to": "caboose/desk", "summary": "Joel: ship it", "status": "drafted"},
            {"type": "send", "draft": "d1", "kind": "tell", "to": "caboose/desk", "summary": "Joel: ship it", "status": "delivered", "id": "3mx1"},
            {"type": "draft", "draft": "d2", "kind": "ask", "to": "switchboard", "summary": "Joel asks: x", "status": "drafted"},
        ]
        lines = ["**Joel**: tell Piper ship it", "**ShitRat**: Sending.", "**Joel**: yes"]
        summary, body = main.join_back_text("room-1", "/tmp/t.md", lines, actions, {"eou": [0.4], "llm": [1.0], "tts": [0.2]})
        self.assertIn("2 Joel turns, 1 sends, 0 failures", summary)
        self.assertIn("tell Piper ship it", body)
        self.assertIn("delivered id 3mx1", body)
        self.assertIn("never confirmed", body)
        self.assertIn("1.60s", body)

    def test_edition_gate_and_voice_settings(self) -> None:
        import main

        self.assertFalse(main._edition_fresh({"builtAt": "2026-07-24T13:00:03.989Z"}))
        self.assertTrue(main._edition_fresh({"builtAt": voice_snapshot._iso(time.time() - 3600)}))
        self.assertFalse(main._edition_fresh(None))
        settings = main.voice_settings_from({"speed": 1.05})
        self.assertEqual(set(settings), {"stability", "similarity_boost", "style", "speed"})


class DecisionGuardTest(unittest.TestCase):
    def _agent(self):
        from unittest.mock import Mock

        import main

        agent = main.JoelclawVoiceAgent(Mock(), "voice", caller_verified=True)
        agent.room = "room-1"
        agent.trusted = True
        agent.save_transcript = lambda: "/tmp/t.md"
        return agent

    def _snapshot(self):
        return {
            "open": [{"id": "41a8c6ca", "title": "Remove our 8 leftover test athletes", "project": "rubicon-fitness", "kind": "approval"}],
            "directory": DIRECTORY,
        }

    def _call(self, fn, *args, **kwargs):
        import asyncio

        return asyncio.run(fn(*args, **kwargs))

    def test_decision_without_an_exact_match_sends_nothing(self) -> None:
        agent = self._agent()
        with patch.object(voice_snapshot, "read", return_value=self._snapshot()), patch.object(ratking_cli, "send") as send:
            out = self._call(agent.record_decision, "the other thing", "yeah")
        self.assertTrue(out.startswith("FAILED"))
        send.assert_not_called()

    def test_decision_sends_once_with_item_words_room_and_transcript(self) -> None:
        agent = self._agent()
        with patch.object(voice_snapshot, "read", return_value=self._snapshot()), patch.object(
            ratking_cli, "send", return_value=ratking_cli.SendResult("delivered", id="3mx9")
        ) as send:
            out = self._call(agent.record_decision, "41a8c6ca", "yes, remove them")
            again = self._call(agent.record_decision, "41a8c6ca", "yes")
        self.assertEqual(send.call_count, 1)
        to, summary, body = send.call_args.args[:3]
        self.assertEqual(to, "switchboard")
        self.assertIn("DECISION [rubicon-fitness#41a8c6ca]", summary)
        self.assertIn('Joel\'s words: "yes, remove them"', body)
        self.assertIn("room room-1", body)
        self.assertIn("/tmp/t.md", body)
        self.assertIn("3mx9", out)
        self.assertIn("Not sending twice", again)

    def test_send_to_desk_sends_at_once_and_refuses_unknown_names(self) -> None:
        agent = self._agent()
        with patch.object(voice_snapshot, "read", return_value=self._snapshot()), patch.object(
            ratking_cli, "send", return_value=ratking_cli.SendResult("delivered", id="x")
        ) as send:
            sent = self._call(agent.send_to_desk, "the switchboard", "pull the celld naming details", "ask")
            unknown = self._call(agent.send_to_desk, "my accountant", "hi")
        self.assertEqual(send.call_count, 1)
        self.assertEqual(send.call_args.args[0], "switchboard")
        self.assertIn("Sent", sent)
        self.assertTrue(unknown.startswith("FAILED"))

    def test_test_calls_refuse_decisions_and_tag_sends(self) -> None:
        agent = self._agent()
        agent.test_call = True
        with patch.object(voice_snapshot, "read", return_value=self._snapshot()), patch.object(
            ratking_cli, "send", return_value=ratking_cli.SendResult("delivered", id="x")
        ) as send:
            self.assertTrue(self._call(agent.record_decision, "41a8c6ca", "yes").startswith("FAILED"))
            self._call(agent.send_to_desk, "switchboard", "hi")
        self.assertEqual(send.call_count, 1)
        self.assertTrue(send.call_args.args[1].startswith("[TEST] "))
        self.assertTrue(send.call_args.args[2].startswith("[TEST CALL"))


class PronounceTest(unittest.TestCase):
    def test_words_hashes_and_chunk_boundaries(self) -> None:
        import asyncio

        import pronounce

        rules = pronounce.load()
        self.assertEqual(pronounce.apply("celld and drovr on pi", rules), "cell D and drover on pie")
        self.assertEqual(pronounce.apply("pi-muster stays", rules), "pi-muster stays")
        self.assertEqual(pronounce.apply("item 41a8c6ca is open", rules), "item is open")
        self.assertEqual(pronounce.apply("a decade ago", rules), "a decade ago")

        async def chunks():
            for c in ["the ce", "lld node ", "is up"]:
                yield c

        async def collect():
            return "".join([part async for part in pronounce.stream(chunks(), rules)])

        self.assertEqual(asyncio.run(collect()), "the cell D node is up")


class ReviewRegressionTest(DecisionGuardTest):
    def test_parallel_duplicate_decisions_send_once(self) -> None:
        import asyncio

        agent = self._agent()

        def slow_send(*args, **kwargs):
            time.sleep(0.05)
            return ratking_cli.SendResult("delivered", id="3mx1")

        async def both():
            return await asyncio.gather(
                agent.record_decision("41a8c6ca", "yes"), agent.record_decision("41a8c6ca", "yes")
            )

        with patch.object(voice_snapshot, "read", return_value=self._snapshot()), patch.object(
            ratking_cli, "send", side_effect=slow_send
        ) as send:
            results = asyncio.run(both())
        self.assertEqual(send.call_count, 1)
        self.assertTrue(any("Not sending twice" in r for r in results))

    def test_refused_send_can_be_retried_but_uncertain_cannot(self) -> None:
        agent = self._agent()
        with patch.object(voice_snapshot, "read", return_value=self._snapshot()), patch.object(ratking_cli, "send") as send:
            send.return_value = ratking_cli.SendResult("refused", "UnknownName")
            self._call(agent.record_decision, "41a8c6ca", "yes")
            send.return_value = ratking_cli.SendResult("uncertain", "timed out")
            self._call(agent.record_decision, "41a8c6ca", "yes")
            third = self._call(agent.record_decision, "41a8c6ca", "yes")
        self.assertEqual(send.call_count, 2)
        self.assertIn("Not sending twice", third)

    def test_send_to_desk_refuses_an_identical_repeat(self) -> None:
        agent = self._agent()
        with patch.object(voice_snapshot, "read", return_value=self._snapshot()), patch.object(
            ratking_cli, "send", return_value=ratking_cli.SendResult("delivered", id="x")
        ) as send:
            self._call(agent.send_to_desk, "switchboard", "Pull the celld details")
            again = self._call(agent.send_to_desk, "the switchboard", "pull the celld details.")
        self.assertEqual(send.call_count, 1)
        self.assertIn("Not sending twice", again)

    def test_reserved_names_are_an_allowlist(self) -> None:
        import json as _json
        import tempfile as _tempfile
        from pathlib import Path as _Path

        with _tempfile.TemporaryDirectory() as d:
            cfg = _Path(d) / "pi.json"
            cfg.write_text(_json.dumps({"reserved": {
                "switchboard": {"aliases": ["ernestine"]}, "joel-iphone": {}, "operator": {}, "some-human": {},
            }}))
            with patch.object(voice_snapshot, "RATKING_CONFIG", cfg), patch.object(voice_snapshot, "CALLSIGNS_PATH", _Path(d) / "none"):
                names = [e["name"] for e in voice_snapshot.directory([])]
        self.assertEqual(names, ["switchboard"])


class InboundLockTest(DecisionGuardTest):
    def test_outbound_detection_fails_closed(self) -> None:
        import main

        joel = "+18175550100"
        ours = {"sip.trunkID": main.OUTBOUND_TRUNK_ID, "sip.phoneNumber": joel}
        self.assertTrue(main.call_is_outbound("call-outbound-joel-123", ours, True, joel))
        self.assertFalse(main.call_is_outbound("call-outbound-joel-123", {}, True, joel))
        self.assertFalse(main.call_is_outbound("call-outbound-joel-123", ours, False, joel))
        self.assertFalse(main.call_is_outbound("voice-_8175550100_abc", ours, True, joel))
        self.assertFalse(main.call_is_outbound("call-outbound-joel-123", {**ours, "sip.ruleID": "SDR_x"}, True, joel))
        self.assertFalse(main.call_is_outbound("call-outbound-joel-123", {**ours, "sip.trunkID": "ST_other"}, True, joel))
        self.assertFalse(main.call_is_outbound("call-outbound-joel-123", {**ours, "sip.phoneNumber": "+15555550123"}, True, joel))

    def test_inbound_call_cannot_send_decide_or_text(self) -> None:
        agent = self._agent()
        agent.trusted = False
        with patch.object(voice_snapshot, "read", return_value=self._snapshot()), patch.object(ratking_cli, "send") as send:
            outs = [
                self._call(agent.send_to_desk, "switchboard", "do a thing"),
                self._call(agent.record_decision, "41a8c6ca", "yes"),
                self._call(agent.text_joel, "hi"),
            ]
            items = self._call(agent.switchboard_items)
        send.assert_not_called()
        self.assertTrue(all(o.startswith("LOCKED") for o in outs))
        self.assertIn("verify_by_callback", outs[0])
        self.assertNotIn("LOCKED", items)

    def test_inbound_allowlist_is_time_voice_and_callback_only(self) -> None:
        import main

        agent = self._agent()
        names = {getattr(getattr(t, "info", None), "name", None) for t in agent.tools}
        self.assertLessEqual(main.INBOUND_READ_TOOLS, names)  # no typos: every allowed tool exists
        self.assertEqual(
            main.INBOUND_READ_TOOLS,
            {"verify_by_callback", "current_time", "list_voices", "sample_voices", "switch_voice", "adjust_voice"},
        )

    def test_locked_call_hears_and_relays_nothing_private(self) -> None:
        import main

        self.assertIn("call you straight back so I know it's you", main.LOCKED_OPENER)
        for leak in ("USER.md", "SOUL", "snapshot", "memory"):
            self.assertNotIn(leak, main.LOCKED_INSTRUCTIONS)
        agent = main.JoelclawVoiceAgent(MagicMock(), "voice", caller_verified=True, instructions=main.LOCKED_INSTRUCTIONS)
        self.assertEqual(agent.instructions, main.LOCKED_INSTRUCTIONS)

        summary, body = main.locked_note_text("voice-_x_1", "/tmp/t.md", [{"type": "callback", "status": "scheduled"}])
        self.assertIn("scheduled to Joel", summary)
        self.assertIn("not relayed", body)
        self.assertNotIn("/tmp/t.md", body)
        summary, _ = main.locked_note_text("voice-_x_1", None, [{"type": "callback", "status": "refused"}])
        self.assertIn("not placed (refused)", summary)

    def test_callback_cooldown_and_daily_cap(self) -> None:
        import tempfile as _tempfile
        from pathlib import Path as _Path

        import main

        with _tempfile.TemporaryDirectory() as d, patch.object(main, "CALLBACK_STATE", _Path(d) / "cb.json"):
            t = 100_000.0
            self.assertTrue(main.callback_allowed(now=t))
            self.assertFalse(main.callback_allowed(now=t + 60))
            allowed = [main.callback_allowed(now=t + i * (main.CALLBACK_COOLDOWN_S + 1)) for i in range(1, 8)]
        self.assertEqual(sum(allowed), main.CALLBACK_DAILY_CAP - 1)

    def test_callback_needs_joels_number_carries_nothing_and_fires_once(self) -> None:
        import asyncio

        import main

        self.assertNotIn("{", main.CALLBACK_REASON)
        stranger = self._agent()
        stranger.trusted = False
        stranger._caller_verified = False
        self.assertTrue(self._call(stranger.verify_by_callback).startswith("FAILED"))

        agent = self._agent()
        agent.trusted = False
        fired, hung = [], []
        agent.spawn_callback = lambda: fired.append(1) or True
        agent.hang_up = lambda: hung.append(1)

        async def run():
            first = await agent.verify_by_callback()
            second = await agent.verify_by_callback()
            await asyncio.sleep(0.2)
            return first, second

        with patch.object(main, "CALLBACK_HANGUP_S", 0.05), patch.object(main, "callback_allowed", return_value=True):
            first, second = asyncio.run(run())
        self.assertIn("Call-back scheduled", first)
        self.assertIn("already on its way", second)
        self.assertEqual(fired, [1])
        self.assertEqual(hung, [1])

        broken = self._agent()
        broken.trusted = False
        broken.spawn_callback = lambda: False
        with patch.object(main, "callback_allowed", return_value=True):
            self.assertTrue(self._call(broken.verify_by_callback).startswith("FAILED"))

    def test_outbound_trust_defaults_to_configured_number(self) -> None:
        import main

        attrs = {"sip.trunkID": main.OUTBOUND_TRUNK_ID, "sip.phoneNumber": "+18175550100"}
        with patch.dict("os.environ", {"JOEL_PHONE_NUMBER": "+18175550100"}):
            self.assertTrue(main.call_is_outbound("call-outbound-joel-1", attrs, True))
        with patch.dict("os.environ", {"JOEL_PHONE_NUMBER": ""}):
            self.assertFalse(main.call_is_outbound("call-outbound-joel-1", attrs, True))


if __name__ == "__main__":
    unittest.main()
