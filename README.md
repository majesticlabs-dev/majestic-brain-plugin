# Majestic Brain - Hermes Memory Provider

Local-first SQLite/FTS5 note store with deterministic entity extraction for
[Hermes Agent](https://github.com/NousResearch/hermes-agent).

Inspired by [garrytan/gbrain](https://github.com/garrytan/gbrain) and
[OpenHuman](https://openhuman.ai)-style memory primitives, adapted to
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
- **Content-hash deduplication**: Identical content returns the existing
  note_id with `duplicate=True` instead of creating a duplicate row.
- **Provenance tracking**: Every note carries `note_kind` (fact|episodic|
  semantic|artifact), `source_type` (manual|memory_write|cron_report|
  auto_fetch_artifact|import|unknown), `source_ref`, and optional
  `metadata_json`.
- **Markdown mirror**: Each note is automatically exported as a human-readable
  `.md` file under `<db_dir>/markdown/` with YAML-ish frontmatter.
- **Prefetch recall**: `prefetch(query)` returns the top FTS matches for the
  current query, injected as context before each turn.
- **Memory mirroring**: `on_memory_write()` mirrors explicit built-in memory
  writes into the Majestic Brain store with `source_type='memory_write'` and
  metadata including the original action/target.

## What it does NOT do

- No auto-fetch, no cron, no scheduled reports inside the plugin.
- No network calls or model calls.
- No external dependencies.

## Requirements

None — stdlib only. SQLite ships with Python; FTS5 is included in most builds.

## Installation

Clone directly into the Hermes plugins directory:

```bash
git clone https://github.com/<org>/hermes-majestic-brain-plugin.git ~/.hermes/plugins/majestic-brain
```

The directory name `majestic-brain` matches the provider name for config
discovery. A legacy install under `~/.hermes/plugins/gbrain` also works —
the provider accepts both names via `matches_name()`.

## Setup

```bash
hermes memory setup    # select "majestic-brain" from the provider list
```

Or manually:

```bash
hermes config set memory.provider majestic-brain
```

For legacy installs under the `gbrain` directory name:

```bash
hermes config set memory.provider gbrain
```

## Package Structure

The canonical implementation lives in the `majestic_brain` package:

- `majestic_brain/` — primary implementation (provider, store, extractor)
- `gbrain/` — thin compatibility shim, re-exports from `majestic_brain`

Both import paths work:

```python
from majestic_brain import MajesticBrainProvider   # canonical
from gbrain import GBrainProvider                    # legacy alias
```

`GBrainProvider is MajesticBrainProvider` — they are the same class.

## Tools

**majestic_brain_note** — primary tool with four actions (legacy name
`gbrain_note` also accepted for backward compatibility):

- **`add`** — Store a note. Returns `{note_id, entities, aliases, content_hash, note_kind, source_type, duplicate}`.
- **`search`** — FTS5 search. Returns `{results, count}`.
- **`links`** — Notes linked via shared entities. Returns `{results, count}`.
- **`stats`** — Store statistics. Returns `{total_notes, total_entities, total_aliases, note_kinds, db_path, markdown_dir}`.

### `add` parameters

- `content` (required): Note text.
- `note_kind` (optional): `fact` | `episodic` | `semantic` | `artifact` (default: `fact`).
- `source_type` (optional): `manual` | `memory_write` | `cron_report` | `auto_fetch_artifact` | `import` | `unknown` (default: `manual`).
- `source_ref` (optional): Source reference string.
- `metadata` (optional): Arbitrary JSON-serializable metadata.

## Legacy Compatibility

- Provider `name` property returns `"majestic-brain"` (primary).
- `legacy_name` returns `"gbrain"`.
- `matches_name()` accepts `"gbrain"`, `"majestic-brain"`, and `"majestic_brain"`.
- `gbrain_note` tool continues to work alongside `majestic_brain_note`.
- DB path remains `<hermes_home>/gbrain/gbrain.db` (not renamed, preserving existing data).
- `gbrain/` package is a thin re-export shim from `majestic_brain/`.

## Development

```bash
pip install -e ".[dev]"
pytest
```

## License

MIT — see [LICENSE](LICENSE).
