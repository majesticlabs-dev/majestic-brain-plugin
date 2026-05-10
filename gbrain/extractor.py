r"""Deterministic entity extractor for GBrain.

Extracts structured entities from free-text notes using pure regex.
No model calls, no network — fully deterministic.

Entity types extracted:
  - URLs             : https?://\S+
  - File paths       : bounded slash-delimited paths ending in an extension
  - @handles         : @\w+
  - #tags            : #\w+
  - Quoted phrases   : "..." or '...'
  - Capitalized phrases: Two or more title-case words (e.g. "John Doe", "New York")
  - AKA aliases      : X aka Y, X also known as Y
"""

from __future__ import annotations

import re
from typing import Dict, List, Tuple

# ---------------------------------------------------------------------------
# Regex patterns (compiled once at module load)
# ---------------------------------------------------------------------------

_RE_URL = re.compile(r'https?://\S+')
_RE_FILE_PATH = re.compile(
    r'(?<![\w.-])(?:~/|\.\.?/|/)?(?:[\w.-]+/){1,8}[\w.-]+\.[A-Za-z0-9]{1,10}\b'
)
_RE_HANDLE = re.compile(r'@(\w+)')
_RE_TAG = re.compile(r'#(\w+)')
_RE_QUOTED = re.compile(r'"([^"]+)"|\'([^\']+)\'')
_RE_CAPPED = re.compile(r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\b')
_RE_AKA = re.compile(
    r'(\w+)\s+(?:aka|also known as)\s+(\w+)',
    re.IGNORECASE,
)


def extract(text: str) -> Dict[str, List[str]]:
    """Extract structured entities from *text*.

    Returns a dict with keys:
      urls, file_paths, handles, tags, quoted, capped, aliases

    Each value is a list of strings (deduplicated, first-seen order).
    `aliases` is a list of ``(name, alias)`` tuples (stored as two-element
    lists for JSON serialisation).
    """
    urls = _dedup(_RE_URL.findall(text))
    file_paths = _dedup(_RE_FILE_PATH.findall(text))
    handles = _dedup(_RE_HANDLE.findall(text))
    tags = _dedup(_RE_TAG.findall(text))

    quoted: List[str] = []
    for m in _RE_QUOTED.finditer(text):
        val = m.group(1) or m.group(2)
        if val:
            quoted.append(val)
    quoted = _dedup(quoted)

    capped = _dedup(_RE_CAPPED.findall(text))

    aliases: List[List[str]] = []
    seen_alias: set = set()
    for m in _RE_AKA.finditer(text):
        a, b = m.group(1).strip(), m.group(2).strip()
        key = (a.lower(), b.lower())
        if key not in seen_alias and a and b:
            seen_alias.add(key)
            aliases.append([a, b])

    return {
        "urls": urls,
        "file_paths": file_paths,
        "handles": handles,
        "tags": tags,
        "quoted": quoted,
        "capped": capped,
        "aliases": aliases,
    }


def all_entity_names(entities: Dict[str, List]) -> List[str]:
    """Return a flat, deduplicated list of all entity names from an extraction result.

    This is what gets stored in the entities table and indexed for linking.
    """
    names: List[str] = []
    names.extend(entities.get("urls", []))
    names.extend(entities.get("file_paths", []))
    names.extend(entities.get("handles", []))
    names.extend(entities.get("tags", []))
    names.extend(entities.get("quoted", []))
    names.extend(entities.get("capped", []))
    for pair in entities.get("aliases", []):
        names.extend(pair)
    return _dedup(names)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _dedup(items: List[str]) -> List[str]:
    """Deduplicate a list preserving first-seen order (case-sensitive)."""
    seen: set = set()
    result: List[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result
