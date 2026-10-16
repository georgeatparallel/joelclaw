"""Pure helpers for the voice worker's Switchboard hands.

A spoken name ("Piper", "rubicon", "the switchboard") resolves only against the
snapshot's directory of fleet names, so a call can never address anyone
outside the fleet. Ambiguity is returned, not guessed.
"""

from __future__ import annotations

import re

FILLER = {"the", "desk", "agent", "owner", "boss", "please", "to", "a"}
# Common words that say nothing about which item is meant.
STOPWORDS = FILLER | {"and", "or", "of", "for", "on", "in", "it", "is", "our", "we", "that", "this", "with", "from", "fix", "name", "one"}


def norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def _words(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if w not in FILLER]


def _keys(entry: dict) -> set[str]:
    keys = {norm(entry["name"]), norm(entry["name"].split("/")[0])}
    for field in ("callsign", "label", "project"):
        if entry.get(field):
            keys.add(norm(entry[field]))
    return {k for k in keys if k}


def resolve(spoken: str, directory: list[dict]) -> list[dict]:
    """Exact matches on name, callsign, label or project first; then prefix
    matches. Desks win over other rows of the same project."""
    words = _words(spoken)
    if not words:
        return []
    key = "".join(words)
    exact = [e for e in directory if key in _keys(e)]
    found = exact or [e for e in directory if any(k.startswith(key) or key.startswith(k) for k in _keys(e) if len(k) >= 4)]
    desks = [e for e in found if e.get("role") in ("desk", "owner")]
    found = desks or found
    unique: dict[str, dict] = {}
    for entry in found:
        unique.setdefault(entry["name"], entry)
    return list(unique.values())


def spoken_name(entry: dict) -> str:
    if entry.get("callsign"):
        return f"{entry['callsign']} ({entry.get('label') or entry['name']})"
    return entry.get("label") or entry["name"]


def body(message: str, kind: str) -> str:
    lead = "Joel asks, on his verified voice call" if kind == "ask" else "Joel, on his verified voice call"
    reply = (
        "\n\nReply to switchboard/voice to reach the call, or to switchboard; Joel hears back through the Switchboard."
        if kind == "ask"
        else ""
    )
    return f"{lead}: {message}{reply}\n\nSent by switchboard/voice on Joel's spoken instruction. Switchboard copied."


def find_item(ref: str, items: list[dict]) -> dict | None:
    """An open item by id, or by the one title the words point at."""
    ref_norm = norm(ref)
    for item in items:
        if item.get("id") and norm(item["id"]) == ref_norm:
            return item
    words = set(_words(ref)) - STOPWORDS
    if not words:
        return None

    def score(item: dict) -> tuple[int, int]:
        title = set(_words(item.get("title", ""))) - STOPWORDS
        return len(words & title), len(title)

    scored = sorted(((score(item), item) for item in items), key=lambda pair: pair[0][0], reverse=True)
    if not scored:
        return None
    (overlap, size), best = scored[0]
    # At least two meaningful words, half the title's, and a clear winner.
    if overlap >= 2 and overlap * 2 >= size and (len(scored) == 1 or overlap > scored[1][0][0]):
        return best
    return None
