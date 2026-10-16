"""Call-start snapshot of Switchboard state, built with no LLM.

A call is a fast, focused fork of the Switchboard chat: it gets the state, not
the transcript. This builder reads the sources desk_inbox reads, all local
except one Rat King peek, and writes one small JSON file the voice worker reads
at answer time:

  - every registered project's desk queue: open items and Joel's rulings
  - each active project's headline or next action ("in flight")
  - recent switchboard/ea escalations, peeked from the Switchboard's mailbox
  - a directory of fleet names and callsigns, so a spoken "tell Piper" resolves
    without a network call

A missing source is listed under "missing", never guessed. Run it from the
launchd timer, and with --fast at dial (no mail peek without a saved cursor).

    python3 voice_snapshot.py [--fast] [--print]
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import ratking_cli

HOME = Path.home()
STATE_DIR = HOME / ".local" / "state" / "switchboard-voice"
SNAPSHOT_PATH = STATE_DIR / "snapshot.json"
PROJECTS_PATH = HOME / ".local" / "state" / "muster" / "projects.jsonl"
QUEUE_DIR = HOME / ".local" / "state" / "herdr-desk"
CALLSIGNS_PATH = HOME / ".local" / "state" / "switchboard" / "callsigns.jsonl"
RATKING_CONFIG = HOME / ".config" / "rat-king" / "pi.json"

OPEN_KINDS = ("blocked", "approval", "decision")
KIND_RANK = {"blocked": 0, "approval": 1, "decision": 2}
ACTIVE_DAYS = 7
MAX_OPEN = 15
MAX_RULINGS = 5
MAX_IN_FLIGHT = 12
MAX_ESCALATIONS = 8
MAIL_OVERLAP = 200
ESCALATION_SENDER = "switchboard/ea"
STALE_SECONDS = 15 * 60
RENDER_CAP = 10_000


def _now() -> float:
    return time.time()


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_ts(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _read_jsonl(path: Path) -> list[dict]:
    rows = []
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows


def _clip(text: str, n: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"


def load_projects() -> list[dict]:
    """Registered projects, last line per slug wins."""
    by_slug: dict[str, dict] = {}
    for row in _read_jsonl(PROJECTS_PATH):
        if row.get("slug") and row.get("dir"):
            by_slug[row["slug"]] = row
    projects = []
    for slug, row in by_slug.items():
        meta: dict = {}
        path = Path(row["dir"]) / ".brain" / "data" / "muster" / "project.json"
        if path.exists():
            try:
                meta = json.loads(path.read_text())
            except (OSError, json.JSONDecodeError):
                meta = {}
        projects.append({"slug": slug, "dir": row["dir"], "meta": meta})
    return projects


def queue_items(slug: str) -> list[dict] | None:
    path = QUEUE_DIR / f"{slug}.jsonl"
    if not path.exists():
        return None
    return _read_jsonl(path)


def open_items(items: list[dict]) -> list[dict]:
    resolved = {item["resolves"] for item in items if item.get("resolves")}
    return [item for item in items if item.get("kind") in OPEN_KINDS and item.get("id") not in resolved]


def is_ruling(item: dict) -> bool:
    """A done line that resolves an item and carries Joel's words."""
    if item.get("kind") != "done" or not item.get("resolves"):
        return False
    text = f"{item.get('title', '')} {item.get('body', '')}"
    return item.get("title", "").startswith(("Joel:", "Ruled:")) or 'Joel: "' in text or "Joel said" in text


def in_flight(project: dict, now: float) -> dict | None:
    meta = project["meta"]
    if meta.get("state") in ("archived", "closed"):
        return None
    updated = _parse_ts(meta.get("updatedAt"))
    if updated is None or now - updated > ACTIVE_DAYS * 86400:
        return None
    line = meta.get("headline") or meta.get("nextAction")
    if not line:
        return None
    return {
        "project": project["slug"],
        "label": meta.get("label") or project["slug"],
        "line": _clip(line, 220),
        "updatedAt": meta.get("updatedAt"),
    }


def directory(projects: list[dict]) -> list[dict]:
    """Fleet names a call may address: live Muster desk rows plus reserved names."""
    callsigns: dict[tuple[str, str], str] = {}
    if CALLSIGNS_PATH.exists():
        for row in _read_jsonl(CALLSIGNS_PATH):
            if row.get("project") and row.get("agent") and row.get("callsign"):
                callsigns[(row["project"], row["agent"])] = row["callsign"]
            elif row.get("project") and row.get("released"):
                for key in [k for k, v in callsigns.items() if k[0] == row["project"] and v == row["released"]]:
                    del callsigns[key]
    names = []
    for project in projects:
        meta = project["meta"]
        for agent in meta.get("agents") or []:
            if agent.get("state") == "closed" or agent.get("role") not in ("desk", "boss", "hawk"):
                continue
            slug, row = project["slug"], agent.get("name")
            if not row:
                continue
            names.append({
                "name": f"{slug}/{row}",
                "callsign": callsigns.get((slug, row)),
                "project": slug,
                "label": meta.get("label") or slug,
                "role": agent.get("role"),
            })
    try:
        reserved = json.loads(RATKING_CONFIG.read_text()).get("reserved") or {}
    except (OSError, json.JSONDecodeError):
        reserved = {}
    for name, entry in reserved.items():
        if name in ("operator", "joel-iphone"):
            continue
        aliases = (entry.get("aliases") or []) if isinstance(entry, dict) else []
        names.append({"name": name, "callsign": aliases[0] if aliases else None, "project": None, "label": name, "role": "owner"})
    return names


def escalations(prior: dict | None, fast: bool) -> tuple[list[dict], int | None, str | None]:
    """Recent switchboard/ea mail to the Switchboard, from a peek that never acks."""
    cursor = (prior or {}).get("mailSeq")
    if fast and cursor is None:
        return (prior or {}).get("escalations") or [], None, "mail: no cursor yet, skipped at dial"
    after = max(0, cursor - MAIL_OVERLAP) if cursor is not None else None
    result = ratking_cli.peek("switchboard", after=after, timeout=4.0 if fast else 30.0)
    if not result.ok:
        return (prior or {}).get("escalations") or [], cursor, f"mail: {result.detail}"
    seen = {e["id"]: e for e in (prior or {}).get("escalations") or []}
    for msg in result.messages:
        if msg.get("from") != ESCALATION_SENDER or not msg.get("summary"):
            continue
        seen[msg["id"]] = {
            "id": msg["id"],
            "seq": msg.get("seq"),
            "summary": _clip(msg["summary"], 240),
            "at": msg.get("acceptedAt"),
        }
    latest = sorted(seen.values(), key=lambda e: e.get("seq") or 0)[-MAX_ESCALATIONS:]
    return latest, result.through_seq, None


def build(fast: bool = False, prior: dict | None = None) -> dict:
    now = _now()
    missing: list[str] = []
    try:
        projects = load_projects()
    except OSError as e:
        projects = []
        missing.append(f"projects: {e}")

    opened, rulings = [], []
    for project in projects:
        items = queue_items(project["slug"])
        if items is None:
            continue
        for item in open_items(items):
            ts = _parse_ts(item.get("ts"))
            opened.append({
                "project": project["slug"],
                "id": item.get("id"),
                "kind": item.get("kind"),
                "title": _clip(item.get("title", ""), 200),
                "from": item.get("from"),
                "ageMin": int((now - ts) / 60) if ts else None,
            })
        for item in items:
            if is_ruling(item):
                rulings.append({
                    "project": project["slug"],
                    "resolves": item.get("resolves"),
                    "words": _clip(item.get("title", ""), 240),
                    "at": item.get("ts"),
                })
    opened.sort(key=lambda o: (KIND_RANK.get(o["kind"], 9), o["ageMin"] if o["ageMin"] is not None else 10**9))
    rulings.sort(key=lambda r: r.get("at") or "", reverse=True)

    flight = [f for f in (in_flight(p, now) for p in projects) if f]
    flight.sort(key=lambda f: f.get("updatedAt") or "", reverse=True)

    mail, mail_seq, mail_missing = escalations(prior, fast)
    if mail_missing:
        missing.append(mail_missing)
    if not projects:
        missing.append("projects: none registered")

    return {
        "schema": 1,
        "builtAt": _iso(now),
        "openCount": len(opened),
        "open": opened[:MAX_OPEN],
        "rulings": rulings[:MAX_RULINGS],
        "inFlight": flight[:MAX_IN_FLIGHT],
        "escalations": mail,
        "mailSeq": mail_seq if mail_seq is not None else (prior or {}).get("mailSeq"),
        "directory": directory(projects),
        "missing": missing,
    }


def read(path: Path = SNAPSHOT_PATH) -> dict | None:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def write(snapshot: dict, path: Path = SNAPSHOT_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(snapshot, ensure_ascii=False, indent=1))
    os.chmod(tmp, 0o600)
    tmp.replace(path)


def age_seconds(snapshot: dict, now: float | None = None) -> float | None:
    built = _parse_ts(snapshot.get("builtAt"))
    return None if built is None else (now or _now()) - built


def _spoken_age(seconds: float) -> str:
    minutes = int(seconds // 60)
    if minutes < 90:
        return f"{minutes} minutes"
    return f"{minutes // 60} hours"


def render(snapshot: dict | None, now: float | None = None) -> str:
    """The prompt section. Says plainly when the snapshot is stale or partial."""
    if not snapshot:
        return "## Switchboard Snapshot\nNo snapshot available. Say you don't have current fleet state; never guess it."
    age = age_seconds(snapshot, now)
    lines = ["## Switchboard Snapshot"]
    if age is None or age > STALE_SECONDS:
        lines.append(
            f"STALE: built {_spoken_age(age) if age is not None else 'at an unknown time'} ago. "
            "Tell Joel it may be out of date before using it."
        )
    else:
        lines.append(f"Built {int(age // 60)} minutes ago.")
    if snapshot.get("missing"):
        lines.append("Missing sources (say you don't have these): " + "; ".join(snapshot["missing"]))

    lines.append(f"\n### Waiting on Joel ({snapshot.get('openCount', 0)} open, top {len(snapshot.get('open') or [])})")
    for o in snapshot.get("open") or []:
        age_text = f", {o['ageMin'] // 1440} days old" if o.get("ageMin") and o["ageMin"] >= 1440 else ""
        lines.append(f"- [{o['kind']}] {o['title']} ({o['project']}, item {o['id']}{age_text})")
    lines.append("\n### Joel's recent decisions (his words)")
    for r in snapshot.get("rulings") or []:
        lines.append(f"- {r['words']} ({r['project']}, {(r.get('at') or '')[:10]})")
    lines.append("\n### In flight")
    for f in snapshot.get("inFlight") or []:
        lines.append(f"- {f['label']}: {f['line']}")
    lines.append("\n### Recent Switchboard escalations")
    for e in snapshot.get("escalations") or []:
        lines.append(f"- {e['summary']}")
    text = "\n".join(lines)
    return text if len(text) <= RENDER_CAP else text[: RENDER_CAP - 1] + "…"


def main(argv: list[str]) -> int:
    fast = "--fast" in argv
    snapshot = build(fast=fast, prior=read())
    write(snapshot)
    if "--print" in argv:
        print(render(snapshot))
    else:
        print(json.dumps({"builtAt": snapshot["builtAt"], "open": snapshot["openCount"], "missing": snapshot["missing"]}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
