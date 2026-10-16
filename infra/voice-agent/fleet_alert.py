"""Alert the Switchboard over Rat King from a launchd check, with a receipt.

Replaces the retired `joelclaw notify`. Sends as switchboard/voice (the voice
line's own name). --latch KEY sends only when this KEY's text changed since the
last delivered alert, so a daily check doesn't repeat itself.

    fleet_alert.py --summary "one line" --body "detail" [--latch sms-vetting]

Exit 0 delivered (or unchanged under --latch), 3 uncertain, 1 not sent.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

import ratking_cli

LATCH_DIR = Path.home() / ".local" / "state" / "fleet-alert"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--summary", required=True)
    parser.add_argument("--body", default="")
    parser.add_argument("--latch")
    args = parser.parse_args(argv)

    latch = LATCH_DIR / f"{args.latch}.sha" if args.latch else None
    digest = hashlib.sha256(f"{args.summary}\n{args.body}".encode()).hexdigest()
    if latch and latch.exists() and latch.read_text().strip() == digest:
        print("unchanged; not re-sent")
        return 0

    result = ratking_cli.send("switchboard", args.summary, args.body or args.summary, timeout=15.0)
    print(f"{result.status} {result.id or result.detail}")
    if result.ok and latch:
        latch.parent.mkdir(parents=True, exist_ok=True)
        latch.write_text(digest)
    return 0 if result.ok else 3 if result.status == "uncertain" else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
