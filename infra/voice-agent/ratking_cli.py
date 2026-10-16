"""Rat King for the voice worker, through the ratking CLI.

The voice worker speaks as its own name, switchboard/voice, and never as a
desk. send signs with the name's key and never takes a lease; inbox --all is a
peek that never acks. Each call has a hard timeout so a tool answers within the
voice budget.

A send is not idempotent. Exit 3 (or a timeout) means it may have landed, so it
is reported as uncertain and never resent.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

VOICE_NAME = "switchboard/voice"
CLI = Path.home() / "Code" / "joelhooks" / "rat-king" / "apps" / "ratking-cli" / "bin" / "ratking.ts"
NODE_FALLBACK = Path.home() / ".local" / "share" / "fnm" / "aliases" / "default" / "bin" / "node"


def _node() -> str:
    found = shutil.which("node")
    return found or str(NODE_FALLBACK)


@dataclass
class SendResult:
    status: str  # delivered | refused | uncertain | error
    detail: str = ""
    id: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == "delivered"

    def spoken(self) -> str:
        if self.status == "delivered":
            return f"Sent. Message id {self.id}."
        if self.status == "uncertain":
            return f"UNCERTAIN: it may or may not have landed ({self.detail}). Do not resend; tell Joel."
        return f"FAILED: not sent ({self.detail}). Tell Joel it failed."


@dataclass
class PeekResult:
    ok: bool
    detail: str = ""
    messages: list[dict] = field(default_factory=list)
    through_seq: int | None = None


def _run(args: list[str], timeout: float, stdin: str | None = None) -> tuple[int | None, str, str]:
    try:
        proc = subprocess.run(
            [_node(), str(CLI), *args],
            input=stdin,
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, "NO_COLOR": "1"},
        )
        return proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired:
        return None, "", f"timed out after {timeout:g}s"
    except OSError as e:
        return -1, "", str(e)


def _last_line(text: str) -> str:
    lines = [line for line in text.strip().splitlines() if line.strip()]
    return lines[-1][:200] if lines else ""


def send(
    to: str,
    summary: str,
    body: str,
    cc: list[str] | None = None,
    as_name: str = VOICE_NAME,
    timeout: float = 2.0,
) -> SendResult:
    args = ["send", "--as", as_name, "--to", to, "--summary", summary[:280], "--body", "-", "--json"]
    for name in cc or []:
        if name != to:
            args += ["--cc", name]
    code, out, err = _run(args, timeout, stdin=body)
    if code is None:
        return SendResult("uncertain", err)
    if code == 0:
        try:
            delivered = json.loads(out).get("delivered") or {}
            return SendResult("delivered", id=delivered.get("id"))
        except json.JSONDecodeError:
            return SendResult("uncertain", "accepted but the receipt was unreadable")
    if code == 2:
        return SendResult("refused", _last_line(err) or "refused")
    if code == 3:
        return SendResult("uncertain", _last_line(err) or "admission unconfirmed")
    return SendResult("error", _last_line(err) or f"exit {code}")


def peek(as_name: str, after: int | None = None, timeout: float = 2.0) -> PeekResult:
    args = ["inbox", "--as", as_name, "--all", "--json"]
    if after is not None:
        args += ["--after", str(after)]
    code, out, err = _run(args, timeout)
    if code != 0:
        return PeekResult(False, err.strip()[:200] or f"exit {code}")
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return PeekResult(False, "unreadable inbox output")
    return PeekResult(True, messages=data.get("messages") or [], through_seq=data.get("throughSeq"))
