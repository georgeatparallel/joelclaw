"""Spoken forms for words TTS gets wrong, applied right before speech.

Only the audio path uses this: transcripts, tool calls and Rat King sends keep
the real spelling. Matching is whole-word and case-insensitive. IDs and short
hashes (7+ hex characters with a digit and a letter) are dropped rather than
spelled out.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterable, AsyncIterator
from pathlib import Path

import yaml

DEFAULT_PATH = Path(__file__).parent / "pronunciations.yaml"
HASH = re.compile(r"\b(?=[0-9a-f]*[0-9])(?=[0-9a-f]*[a-f])[0-9a-f]{7,40}\b", re.IGNORECASE)
# A chunk is spoken up to its last word boundary, so a word split across chunks
# is matched whole.
BOUNDARY = re.compile(r"[\s.,!?;:)\]]")


def load(path: Path = DEFAULT_PATH) -> list[tuple[re.Pattern[str], str]]:
    try:
        data = yaml.safe_load(path.read_text()) or {}
    except (OSError, yaml.YAMLError):
        return []
    words = data.get("words") or {}
    rules = []
    # Longest first, so "AI Hero" style phrases win over their parts.
    for word in sorted(words, key=len, reverse=True):
        spoken = str(words[word])
        rules.append((re.compile(rf"(?<![\w-]){re.escape(str(word))}(?![\w-])", re.IGNORECASE), spoken))
    return rules


def apply(text: str, rules: list[tuple[re.Pattern[str], str]]) -> str:
    text = HASH.sub("", text)
    for pattern, spoken in rules:
        text = pattern.sub(spoken, text)
    return re.sub(r"[ \t]{2,}", " ", text)


async def stream(chunks: AsyncIterable[str], rules: list[tuple[re.Pattern[str], str]]) -> AsyncIterator[str]:
    pending = ""
    async for chunk in chunks:
        pending += chunk
        cut = max((m.end() for m in BOUNDARY.finditer(pending)), default=0)
        if cut:
            yield apply(pending[:cut], rules)
            pending = pending[cut:]
    if pending:
        yield apply(pending, rules)
