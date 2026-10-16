"""Post a short note to Joel's ShitRat chat when the call line needs him and can't reach him.

Joel only, one note per event: --key names the event (the call's room), and a key
that already got a delivered note is never sent again. Plain words; the caller
keeps anything from an unverified inbound call out of the text.

    joel_note.py --key ROOM --kind needs_joel "message"

Prints the message-shitrat receipt (JSON). Exit 0 delivered or already sent,
1 not sent.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

SCRIPT = Path.home() / "Code" / "joelhooks" / "dark-wizard" / "scripts" / "message-shitrat.mjs"
SENT_DIR = Path.home() / ".local" / "state" / "joel-note"
MAX_CHARS = 400
# launchd and the voice worker run with a thin PATH; node and secrets live here.
EXTRA_PATH = [
    str(Path.home() / ".local" / "bin"),
    str(Path.home() / ".local" / "share" / "fnm" / "aliases" / "default" / "bin"),
    "/opt/homebrew/bin",
    "/usr/local/bin",
]


def tool_env() -> dict:
    env = dict(os.environ)
    env["PATH"] = os.pathsep.join([env.get("PATH", "")] + EXTRA_PATH).strip(os.pathsep)
    return env


def last_receipt(stdout: str) -> dict:
    """The receipt is the last stdout line that parses as a JSON object."""
    for line in reversed((stdout or "").strip().splitlines()):
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict):
            return value
    raise ValueError("no JSON receipt on stdout")


def plain(text: str) -> str:
    """One short paragraph: no markdown, no long hashes or ids."""
    text = re.sub(r"\b[0-9a-f]{7,}\b", "", text)
    text = re.sub(r"[`*_#>]+", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:MAX_CHARS]


def key_path(key: str) -> Path:
    return SENT_DIR / (re.sub(r"[^A-Za-z0-9._-]", "_", key)[:120] + ".sent")


def send(key: str, message: str, kind: str = "needs_joel", runner=subprocess.run) -> dict:
    marker = key_path(key)
    # Claim the event before sending (O_EXCL), so two runs can't both send it.
    try:
        SENT_DIR.mkdir(parents=True, exist_ok=True)
        fd = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return {"ok": True, "skipped": "already sent for this event"}
    except OSError as e:
        return {"ok": False, "error": f"claim failed: {e}"[:200]}
    receipt: dict = {"ok": False}
    try:
        env = tool_env()
        node = shutil.which("node", path=env["PATH"]) or "node"
        out = runner(
            [node, str(SCRIPT), "--kind", kind, "--agent", "ShitRat call line", plain(message)],
            capture_output=True, text=True, timeout=30, env=env,
        )
        receipt = last_receipt(out.stdout)
    except (OSError, subprocess.TimeoutExpired, ValueError) as e:
        receipt = {"ok": False, "error": f"{type(e).__name__}: {e}"[:200]}
    finally:
        try:
            if receipt.get("ok"):
                os.write(fd, json.dumps(receipt).encode())
            os.close(fd)
            if not receipt.get("ok"):
                marker.unlink(missing_ok=True)  # a failed send can be retried
        except OSError:
            pass
    return receipt


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--key", required=True)
    parser.add_argument("--kind", default="needs_joel", choices=["ask", "update", "needs_joel"])
    parser.add_argument("message")
    args = parser.parse_args(argv)
    receipt = send(args.key, args.message, args.kind)
    print(json.dumps(receipt))
    return 0 if receipt.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
