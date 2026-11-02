# Memory

Simon has an optional built-in memory service. It stores memories in the shared application SQLite database and searches embeddings in-process with `sqlite-vss`. Custom integrations implement the same small `MemoryService` contract.

## Enable built-in memory

Install the project dependencies with `uv sync`, then configure `config.toml`:

```toml
[llm.providers.openai]
api_key = "your-key"

[memory]
backend = "local"
database_path = "datastore/simon.sqlite3"
directory = "memories" # Optional editable Markdown directory
embedding_provider = "openai"
embedding_model = "text-embedding-3-small"
recall_limit = 3
context_token_budget = 1000
```

Run `uv run simon.py`. Ask the agent to remember something using its `memory.add` tool. Memories survive restarts and are recalled before inference. The chat provider can be OpenAI, Anthropic, or Google independently of the embedding provider.

## Editable Markdown directory

Set `[memory] directory = "memories"` to synchronize one recursive directory with built-in SQLite memory. Paths resolve against the project root, like the database path. Leave it blank/unset for SQLite-only memory.

Synchronization runs at startup. Agent/programmatic add, update, and remove operations also mirror the affected record immediately. To import edits while the agent is running, or retry pending writes, run:

```bash
uv run simon.py --sync-memory
# With a particular configuration:
uv run simpon.py --config /path/to/config.toml --sync-memory
```

This command starts no chat session and makes no chat-model request. New or changed title/content still needs the configured embedding provider. Exit status is `0` for a clean sync and `1` when conflicts/errors need attention. The startup report and command output show import/update/export/removal counts and per-file issues. There is no automatic directory watcher in this version.

### Files and identity

An agent-created file looks like:

```markdown
---
id: 42
type: semantic
importance: high
namespace: default
---

# Development preferences

Prefer small, modular implementations.
```

Generated filenames combine a sanitized title and stable ID, such as `development-preferences--42.md`. Rename or move a file within the directory while keeping its frontmatter ID: it remains the same memory, and future agent writes use its new location. Existing imported filenames are preserved.

To add a memory yourself or from a script, create a `.md` file without an ID. Synchronization assigns an ID and writes it back. The first H1 supplies the title (or use frontmatter `title`; otherwise the filename is used). The rest of the body is the memory content. Type/importance default to semantic/medium. JSON-compatible custom data can live under `metadata`; extra frontmatter fields are imported into that metadata. Metadata-only edits do not regenerate embeddings. Exported creation/modification timestamps are informative; identity and timestamps remain managed by SQLite.

An interrupted import is bound to its source path transactionally with the new database record. Retrying a failed ID write does not create another memory. If a pending import's source was moved before its ID could be written, restore that path first; sync reports the issue rather than importing ambiguous new files. Duplicate IDs, unknown IDs, malformed files, and unsupported namespaces are reported without overwriting the stored record. Markdown symlinks are not synchronized.

### Conflicts, deletion, and failures

SQLite remains the operational source of truth. Sync stores the last synchronized file and record hashes in `memory_markdown_sync`, with directory ownership in `memory_sync_roots` (`memory:003` migration).

- A file-only change updates SQLite, refreshing embeddings only if title/content changed.
- A database-only change updates its file.
- If both changed differently, the source file is preserved and a database snapshot is written under `.memory-conflicts/`. That folder is excluded from imports. To choose the database version, copy the snapshot over the source and sync. To choose the file version, apply its values through the ordinary memory update API/tool and sync; once both agree, the conflict clears.
- Removing a previously synchronized file from an intact directory soft-removes that memory from recall. If the database record also changed, deletion becomes a conflict instead. Invalid/incomplete scans defer missing-file deletions.
- A directory identity marker, `.simon-memory.json`, distinguishes an available directory from a missing/replaced one. If a previously initialized directory or marker disappears, sync makes no deletion decisions. Restore the directory/marker rather than replacing it with an empty folder. After a database reset, an orphaned directory containing only the marker and sync lock can be rebound automatically and populated from SQLite. An unbound marked directory containing other files is rejected rather than overwritten.
- Agent removals write a `removed_at` tombstone into the existing file. Reintroducing an old copy or deleting the tombstone field does not resurrect a retired memory; it is reported as a conflict. A deliberately deleted file stays deleted after retirement.

Direct file edits/deletions are the user/programmatic editing interface and are applied during startup/manual sync; they do not invoke agent-tool approval. The agent's `memory.remove` tool continues to require approval.

SQLite writes and filesystem writes cannot share a transaction. If a file write fails after storage succeeds, the memory tool returns success with `metadata.markdown_sync_pending=true` and an explanation that the record is saved in SQLite. Retry with the sync command after resolving the issue. File replacement is atomic and checks the read version before replacing it; simultaneous external editor writes cannot be made atomic with SQLite. POSIX advisory file locking serializes cooperating sync processes. Watchers, multiple root directories, restoration, and permanent deletion remain future work.

Programmatically, a directory-configured service exposes `sync()` and `last_sync_report` through the optional `SynchronizableMemoryService` protocol. The configured service wraps `SQLiteMemoryService`; custom memory adapters do not need to implement filesystem synchronization.

## Agent-facing memory tools

Every perception includes a `Memory` section stating whether persistent memory is enabled and which operations the backend supports. A disabled backend explicitly reports that persistence is unavailable. An enabled backend with no relevant results is distinguished from disabled memory or a recall failure.

**Recall is automatic.** Before inference, the perception loop searches the current context and supplies relevant records with explicit `memory_id`, title, type, importance, and modification time. The agent does not need to call a retrieval tool. It is instructed to prefer the latest recalled version and current user corrections over older copies in conversation history.

Built-in SQLite memory exposes three tools across all roles:

- **`memory.add`** saves new durable information. Only `title` and `content` are required; `type` defaults to `semantic` and `importance` to `medium`. Optional types are `semantic` (facts/preferences) and `episodic` (events/experiences). Temporary work belongs in task notes.
- **`memory.update`** corrects or refreshes an existing memory by its exact ID from recall or successful memory-tool result metadata. Supply at least one of `title`, `content`, `type`, or `importance`. Omitted fields stay unchanged. Content is a full replacement, so preserve still-valid details and the scope of any exception.
- **`memory.remove`** takes an exact `memory_id` and a nonempty `reason`. Each invocation uses the shared tool approval flow. Approval retires the memory from automatic recall; denial or a missing approval callback makes no changes. The ID, content, metadata, embedding provenance, and removal timestamp remain in SQLite for recovery. Use update for corrections rather than removing and recreating a memory.

For example, the tool arguments might be:

```json
{
  "title": "Development preferences",
  "content": "Prefer small, modular implementations."
}
```

After `memory.add` returns `metadata.memory_id="42"`, a correction can use:

```json
{
  "memory_id": "42",
  "content": "Prefer small, modular implementations. Use temporary tests during development and remove newly created tests after verification."
}
```

Successful creation and updates return `ToolResult` with the persisted ID in `metadata.memory_id`. Failed writes are tool errors, not success messages. Updating preserves the ID, creation time, and metadata. Title/content changes replace the vector and embedding provenance transactionally; importance/type-only edits do not call the embedding provider. Concurrent edits detected while generating an embedding produce a conflict error rather than overwriting the intervening edit. The current recalled copy is refreshed immediately after a successful update.

For example, request retirement with:

```json
{
  "memory_id": "42",
  "reason": "This project-specific preference is obsolete and should no longer influence future tasks."
}
```

The approval prompt displays the tool's soft-removal behavior plus the exact ID and reason. Approval is per invocation, with denial as the default. A successful result includes `metadata.memory_id` and `metadata.removed_at`. Removal sets `memories.removed_at`, removes its vectors from all memory search indexes, and immediately clears the record from current/proposed recall. The record and embedding provenance remain stored. Repeated removal is idempotent, and removed records cannot be edited through update. Get-by-ID still returns the retained record with `removed_at` set. Removal does not purge existing conversation history or logs; automated restoration and permanent deletion are not currently exposed.

Custom services expose only capabilities they implement. Add/search-only adapters get `memory.add` and automatic recall, with an explicit statement that editing/removal are unavailable. Implement the optional standard capability interfaces below to expose `memory.update` or `memory.remove`.

Memory is a core agent module. `memory/engine.py` owns recall, domain operations, tool schemas, descriptions, and structured result formatting. The harness registers `MemoryEngine.get_tools()` once per agent as shared tools across roles, rather than composing memory into role capability libraries. Tools use the current `Tool`/`ToolResult` interface, canonical `memory.*` names, schema defaults, and the registry approval policy for removal. Disabled memory exposes no tools; available operations follow the configured service's capabilities. Persistence and embeddings stay behind the memory service.

Relative database paths resolve against the Simon project root. The database and parent directories are created on first startup. `datastore/` is ignored by Git. This database is application storage: future modules can add their own tables through `storage.database.Database` and named transactional migrations.

Other modules can use `Database.from_config(config)` to resolve the same application database, then apply their own named migrations within `with database.connect() as connection:`. Plain database connections do not load vector extensions; vector consumers opt in with `connect(vectors=True)`.

The SQLite runtime must allow loading extensions and support the `sqlite-vss` binary for your platform. An extension loading failure is reported at memory startup. The built-in service currently accepts only the `default` namespace.

### Configuration

The fields in `[memory]` use the lowercase names shown in `config.example.toml`.
For example, `MEMORY_RECALL_LIMIT` maps to `memory.recall_limit`; embedding fields
use `embedding_provider`, `embedding_model`, and `embedding_dimensions`. Provider
keys and base URLs come from `[llm.providers.openai]`. The legacy variables below
are still accepted as environment overrides or `.env` fallbacks.

| Variable                      | Default / behavior                                                                                                                           |
| ----------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------- |
| `MEMORY_BACKEND`              | `local` (built-in SQLite), `none`, or a registered custom factory name. Blank/unset means `none`.                                            |
| `DATABASE_PATH`               | `datastore/simon.sqlite3`; shared application database path.                                                                                 |
| `MEMORY_DIRECTORY`            | Blank by default; `[memory] directory` enables recursive Markdown synchronization for the local backend.                                     |
| `EMBEDDING_PROVIDER`          | `openai`; independent adapter selection.                                                                                                     |
| `EMBEDDING_MODEL`             | `text-embedding-3-small`.                                                                                                                    |
| `EMBEDDING_DIMENSIONS`        | Optional. Known OpenAI model defaults are inferred; custom model names require an explicit dimension count.                                  |
| `OPENAI_API_KEY`              | Required for OpenAI embeddings, even if a different provider handles chat.                                                                   |
| `OPENAI_BASE_URL`             | Optional OpenAI-compatible endpoint, included in embedding compatibility metadata.                                                           |
| `MEMORY_RECALL_LIMIT`         | `3`; accepts 1–100. Built-in recall returns the nearest memories in squared-L2 distance order, without a distance cutoff by default.         |
| `MEMORY_CONTEXT_TOKEN_BUDGET` | `1000`; positive recalled-record text budget using `cl100k_base` tokenization. A short capability/status description is included separately. |

Set `MEMORY_BACKEND=none` to disable memory explicitly. Disabled memory does not construct an embedding provider or initialize memory tables. Unknown backend/provider names produce configuration errors.

## Storage and embedding compatibility

Memory data lives in `memories`. Embedding provenance lives in `embedding_profiles` and `memory_embeddings`; `memory_embedding_indexes` identifies each profile's vector virtual table. The active profile is recorded in `memory_settings`. Applied module migrations are tracked in `schema_migrations`.

Each memory gets a stable integer ID; titles are not identifiers and do not become filesystem paths. Content, type (`semantic`/`episodic`), importance (`low`/`medium`/`high`/`extreme`), timestamps, and JSON metadata are stored in ordinary tables. One vector represents `# <title>\n\n<content>`. Vector row IDs map through `memory_embeddings` to the memory record.

Embedding profiles identify provider, model, dimensions, model revision when exposed, provider options, task configuration, normalization, and input-format version. Adapter version and generation timestamps provide provenance. Different embedding spaces are never compared merely because their dimensions match. Hosted providers may not expose an immutable model revision; the metadata records the requested identifier without inventing one.

Changing embedding settings when active memories exist currently reports that re-embedding is required. Restore the original settings to continue using existing memory. Automated model migration, restoration, and permanent deletion remain follow-up work. Built-in memory supports add, search, get-by-ID, partial updates, soft removal, and optional editable Markdown synchronization. The `memory:002` migration adds the removal timestamp to existing databases without discarding memories; `memory:003` adds synchronization bookkeeping.

Adding a memory generates its vector before opening the write transaction, then commits the memory, provenance, and vector together. Failed writes return errors. Recall failures clear recalled state, publish an agent error, and allow the turn to proceed. There is no automatic memory extraction, importance weighting, or extra chat-model call for recall.

## Custom memory services

The agent uses `memory.service.MemoryService`. Built-in SQLite memory implements this same public contract:

```python
class MemoryService:
    def startup(self) -> None: ...
    def close(self) -> None: ...
    def add(self, draft: MemoryDraft) -> Memory: ...
    def search(self, query: MemoryQuery) -> list[SearchResult]: ...
```

- `startup`/`close` must be safe to repeat. Operations require a started service; startup after close is supported.
- `MemoryDraft` validates title/content, type, importance, namespace, and JSON metadata. Add returns a persisted `Memory` with an integer or opaque string ID.
- `MemoryQuery` contains text, limit, and namespace. Empty/whitespace queries return no results. Search returns at most the requested limit in relevance order.
- `SearchResult` carries the memory, optional distance, and declared metric. A custom service that uses similarity can declare its metric accordingly; the agent uses returned order rather than comparing scores across services.
- Use `MemoryValidationError` for invalid/unsupported input and `MemoryServiceError` for operational failures. Never report a failed write as successful. Reject unsupported namespaces instead of ignoring them.
- Implementations must support calls from the agent worker thread. SQLite memory uses operation-local connections for this purpose.

Services supporting editing implement the optional `EditableMemoryService` protocol as well:

```python
def get(self, memory_id: int | str, namespace: str = "default") -> Memory: ...

def update(
    self, memory_id: int | str, changes: MemoryUpdate, namespace: str = "default"
) -> Memory: ...
```

`MemoryUpdate` contains optional title/content/type/importance fields plus programmatic metadata replacement and rejects an empty update. Both operations use the same ID returned by add/search. Get returns the complete current record; update preserves omitted fields, identity, creation time, and namespace. Metadata is preserved unless explicitly supplied by a programmatic caller or Markdown edit; the agent tool exposes only title/content/type/importance. A missing ID raises `MemoryNotFoundError` and must never create a new memory. A concurrent edit can raise `MemoryConflictError`. The agent detects this protocol structurally, so a custom editable service receives the same update tool without agent-loop changes. Get-by-ID supports the editing implementation; automatic recall remains the normal agent retrieval path.

Removal is a separate optional `RemovableMemoryService` capability:

```python
def remove(self, memory_id: int | str, namespace: str = "default") -> Memory: ...
```

The operation soft-removes the identified memory, preserves its recoverable record, excludes it from search, and returns the record with `removed_at` set. Repeating removal returns the same retired record; a nonexistent ID raises `MemoryNotFoundError`. Services can implement editing, removal, both, or neither. Approval belongs to the tool registry: register the core memory tools with `Toolbox(approve=...)` and dispatch through `execute_result`/`execute`, rather than calling a removal handler directly. Direct service methods are programmatic storage operations, not an approval boundary. Markdown synchronization respects removal tombstones instead of automatically reimporting retired files.

For example, adapt an existing service without changing the agent:

```python
from memory.config import MEMORY_FACTORIES


class CustomMemoryService:
    # Implement startup, close, add, and search using your source's client.
    # Translate records/errors to the types in memory.service.
    ...


MEMORY_FACTORIES["custom"] = lambda config: CustomMemoryService()
```

Register the factory before `create_memory_service` is called, then set `MEMORY_BACKEND=custom`. A remote service can own embedding generation itself; it need not use our SQLite schema or `embeddings/`.

The built-in factory registry contains only `local`; `none` disables memory. The application starts the selected service and injects it directly into the agent through `memory_service`. Custom harnesses can likewise pass a started service to `MemoryEngine(service=...)` or `Agent(memory_service=...)`. No separate vector-store interface or compatibility adapter is involved.

## Adding an embedding provider

`embeddings/embedding.py` defines the independent `EmbeddingModel` protocol. Follow `embeddings/openai.py`: expose an immutable `EmbeddingProfile`, implement `embed_document(text)` and `embed_query(text)` returning numeric vectors, and implement repeatable `close()` to release clients. The adapter must remain usable after close by lazily recreating clients. Return vectors matching the advertised dimensions; built-in memory validates finite, nonzero vectors before indexing.

Register a configuration factory in `embeddings.config.EMBEDDING_FACTORIES` and select it with `EMBEDDING_PROVIDER`. The profile fingerprint includes compatibility-affecting settings; bump input/adapter configuration when output semantics change. Changing only adapter implementation version does not create a new embedding space.

Memory owns its title/content input format. Another application module can use the embedding adapters and shared profile metadata without depending on the memory engine.

See the [architecture plan](../discussions/built-in-memory.md) for schema rationale and future migration work.
