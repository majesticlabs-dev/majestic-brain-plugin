# Hermes GBrain Plugin

Local-first SQLite/FTS5 note store with deterministic entity extraction for
[Hermes Agent](https://github.com/NousResearch/hermes-agent).

Inspired by [garrytan/gbrain](https://github.com/garrytan/gbrain), adapted to
the Hermes `MemoryProvider` interface with **no network calls, no model calls,
and no external dependencies**.

## What it does

- **Deterministic extraction**: URLs, file paths, `@handles`, `#tags`, quoted
  phrases, capitalized entity phrases (e.g. "John Doe"), and explicit
  `X aka Y` alias pairs are extracted from every note.
- **FTS5 search**: Full-text search over note content and extracted entities.
  Falls back to `LIKE` if FTS5 is unavailable.
- **Entity linking**: Shared entities between notes create a traversable graph
  via the `links` action.
- **Prefetch recall**: `prefetch(query)` returns the top FTS matches for the
  current query, injected as context before each turn.
- **Memory mirroring**: `on_memory_write()` mirrors explicit built-in memory
  writes into the GBrain store.

## Requirements

None — stdlib only. SQLite ships with Python; FTS5 is included in most builds.

## Installation

Clone directly into the Hermes plugins directory:

```bash
git clone https://github.com/<org>/hermes-gbrain-plugin.git ~/.hermes/plugins/gbrain
```

## Setup

```bash
hermes memory setup    # select "gbrain"
```

Or manually:

```bash
hermes config set memory.provider gbrain
```

## Tools

**gbrain_note** — single tool with four actions:

| Action | Description |
|--------|-------------|
| `add` | Store a note. Returns `{note_id, entities, aliases}`. |
| `search` | FTS5 search. Returns `{results, count}`. |
| `links` | Notes linked via shared entities. Returns `{results, count}`. |
| `stats` | Store statistics. Returns `{total_notes, total_entities, total_aliases, db_path}`. |

## Development

```bash
pip install -e ".[dev]"
pytest
```

## License

MIT — see [LICENSE](LICENSE).
