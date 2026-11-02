MEMORY_SCHEMA = (
    """CREATE TABLE memories (
        id INTEGER PRIMARY KEY, namespace TEXT NOT NULL DEFAULT 'default',
        title TEXT NOT NULL, content TEXT NOT NULL,
        type TEXT NOT NULL CHECK(type IN ('semantic','episodic')),
        importance TEXT NOT NULL CHECK(importance IN ('low','medium','high','extreme')),
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL, metadata_json TEXT NOT NULL
    )""",
    """CREATE TABLE memory_embedding_indexes (
        id INTEGER PRIMARY KEY, profile_id INTEGER NOT NULL UNIQUE REFERENCES embedding_profiles(id),
        table_name TEXT NOT NULL UNIQUE, implementation TEXT NOT NULL,
        implementation_version TEXT NOT NULL, metric TEXT NOT NULL
    )""",
    """CREATE TABLE memory_embeddings (
        id INTEGER PRIMARY KEY, memory_id INTEGER NOT NULL REFERENCES memories(id),
        profile_id INTEGER NOT NULL REFERENCES embedding_profiles(id),
        input_hash TEXT NOT NULL, created_at TEXT NOT NULL, adapter_version TEXT NOT NULL,
        UNIQUE(memory_id, profile_id)
    )""",
    """CREATE TABLE memory_settings (
        id INTEGER PRIMARY KEY CHECK(id = 1),
        active_profile_id INTEGER NOT NULL REFERENCES embedding_profiles(id)
    )""",
)

MEMORY_REMOVAL_SCHEMA = ("ALTER TABLE memories ADD COLUMN removed_at TEXT",)

MEMORY_MARKDOWN_SCHEMA = (
    """CREATE TABLE memory_sync_roots (
        id INTEGER PRIMARY KEY, directory TEXT NOT NULL UNIQUE, token TEXT NOT NULL,
        initialized INTEGER NOT NULL DEFAULT 0
    )""",
    """CREATE TABLE memory_markdown_sync (
        root_id INTEGER NOT NULL REFERENCES memory_sync_roots(id),
        memory_id INTEGER NOT NULL REFERENCES memories(id),
        path TEXT, file_hash TEXT, memory_hash TEXT NOT NULL,
        PRIMARY KEY(root_id, memory_id), UNIQUE(root_id, path)
    )""",
)
