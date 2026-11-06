import hashlib
import json
import os
import re
import tempfile
import threading
import uuid
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import frontmatter

from memory.service import (
    Memory,
    MemoryConflictError,
    MemoryDraft,
    MemoryServiceError,
    MemorySyncReport,
    MemoryUpdate,
    MemoryValidationError,
)

if TYPE_CHECKING:
    from memory.sqlite import SQLiteMemoryService


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def memory_hash(memory: Memory) -> str:
    values = asdict(memory)
    values.pop("created_at")
    values.pop("updated_at")
    return digest(json.dumps(values, sort_keys=True, allow_nan=False).encode())


@dataclass
class Document:
    path: str
    file_hash: str
    memory_id: int | None
    draft: MemoryDraft
    removed_at: str | None
    canonical_hash: str

    @property
    def removed(self) -> bool:
        return self.removed_at is not None


def document_hash(document: Document, memory_id: int | str) -> str:
    return memory_hash(
        Memory(
            id=memory_id,
            **asdict(document.draft),
            removed_at=document.removed_at,
        )
    )


def parse_document(path: str, data: bytes) -> Document:
    post = frontmatter.loads(data.decode("utf-8-sig").replace("\r\n", "\n"))
    canonical = frontmatter.Post(post.content)
    canonical.metadata = dict(post.metadata)
    for name in ("created_at", "updated_at", "removed_at"):
        if isinstance(canonical.get(name), date):
            canonical[name] = canonical[name].isoformat()
    canonical_hash = digest((frontmatter.dumps(canonical) + "\n").encode())
    body = post.content.strip()
    heading = re.match(r"\A#\s+([^\n]+)(?:\n|$)", body)
    title = post.get("title") or (
        heading.group(1).strip() if heading else Path(path).stem
    )
    if heading and (not post.get("title") or heading.group(1).strip() == title):
        body = body[heading.end() :].strip()
    memory_id = post.get("id")
    if memory_id is not None:
        if (
            isinstance(memory_id, bool)
            or not str(memory_id).isdecimal()
            or int(memory_id) <= 0
        ):
            raise MemoryValidationError(
                "Frontmatter id must be a positive memory ID, or omitted for a new memory"
            )
        memory_id = int(memory_id)
    metadata = post.get("metadata", {})
    if not isinstance(metadata, dict):
        raise MemoryValidationError("Frontmatter metadata must be an object")
    metadata = dict(metadata)
    reserved = {
        "id",
        "title",
        "type",
        "importance",
        "namespace",
        "metadata",
        "created_at",
        "updated_at",
        "removed_at",
    }
    metadata.update(
        {key: value for key, value in post.metadata.items() if key not in reserved}
    )
    metadata = json.loads(json.dumps(metadata, allow_nan=False))
    namespace = post.get("namespace", "default")
    if namespace != "default":
        raise MemoryValidationError("Only the default namespace is supported")
    removed_at = post.get("removed_at")
    if removed_at is not None and not isinstance(removed_at, (str, date, datetime)):
        raise MemoryValidationError("removed_at must be a timestamp, or omitted")
    if isinstance(removed_at, str):
        try:
            datetime.fromisoformat(removed_at.replace("Z", "+00:00"))
        except ValueError as error:
            raise MemoryValidationError(
                "removed_at must be an ISO timestamp, or omitted"
            ) from error
    return Document(
        path,
        digest(data),
        memory_id,
        MemoryDraft(
            title,
            body,
            post.get("type", "semantic"),
            post.get("importance", "medium"),
            namespace,
            metadata,
        ),
        removed_at.isoformat() if isinstance(removed_at, date) else removed_at,
        canonical_hash,
    )


def render_memory(memory: Memory) -> bytes:
    multiline_title = "\n" in memory.title or "\r" in memory.title
    post = frontmatter.Post(
        memory.content if multiline_title else f"# {memory.title}\n\n{memory.content}"
    )
    post.metadata = {
        "id": memory.id,
        "type": memory.type,
        "importance": memory.importance,
        "namespace": memory.namespace,
        "created_at": memory.created_at,
        "updated_at": memory.updated_at,
    }
    if memory.metadata:
        post["metadata"] = memory.metadata
    if multiline_title:
        post["title"] = memory.title
    if memory.removed_at is not None:
        post["removed_at"] = memory.removed_at
    return (frontmatter.dumps(post) + "\n").encode()


class MarkdownMemoryService:
    def __init__(self, service: "SQLiteMemoryService", directory: str | Path):
        self.service = service
        self.directory = Path(directory).expanduser().resolve()
        self.last_sync_report = MemorySyncReport(str(self.directory))
        self._lock = threading.RLock()
        self._root_id = None

    def startup(self):
        with self._lock:
            self.service.startup()
            self.sync()

    def close(self):
        with self._lock:
            self.service.close()

    def search(self, query):
        return self.service.search(query)

    def get(self, memory_id, namespace="default"):
        return self.service.get(memory_id, namespace)

    def add(self, draft):
        with self._lock:
            result = self.service.add(draft)
            self._sync(result.id)
            return result

    def update(self, memory_id, changes, namespace="default"):
        with self._lock:
            result = self.service.update(memory_id, changes, namespace)
            self._sync(result.id)
            return result

    def remove(self, memory_id, namespace="default"):
        with self._lock:
            result = self.service.remove(memory_id, namespace)
            self._sync(result.id)
            return result

    def sync(self) -> MemorySyncReport:
        return self._sync()

    def _root(self):
        marker = self.directory / ".sprocket-memory.json"
        for name in (".klank-memory.json", ".simon-memory.json"):
            if marker.exists() or marker.is_symlink():
                break
            legacy_marker = self.directory / name
            if legacy_marker.exists() or legacy_marker.is_symlink():
                marker = legacy_marker
        with self.service.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM memory_sync_roots WHERE directory=?",
                (str(self.directory),),
            ).fetchone()
            if row is None:
                token = str(uuid.uuid4())
                if marker.exists():
                    allowed = {marker.name, ".memory-sync.lock"}
                    if marker.is_symlink() or any(
                        entry.name not in allowed
                        or entry.is_symlink()
                        or not entry.is_file()
                        for entry in self.directory.iterdir()
                    ):
                        raise MemoryServiceError(
                            "Directory has an unbound memory marker and other files; choose a new directory"
                        )
                    token = json.loads(marker.read_text(encoding="utf-8")).get("token")
                    if not isinstance(token, str) or not token.strip():
                        raise MemoryServiceError(
                            "Memory directory marker has no valid token"
                        )
                root_id = connection.execute(
                    "INSERT INTO memory_sync_roots(directory,token) VALUES (?,?)",
                    (str(self.directory), token),
                ).lastrowid
                row = {"id": root_id, "token": token, "initialized": 0}
        if not row["initialized"]:
            self.directory.mkdir(parents=True, exist_ok=True)
            if not marker.exists():
                with marker.open("x", encoding="utf-8") as handle:
                    json.dump({"token": row["token"]}, handle)
        if not self.directory.is_dir() or marker.is_symlink() or not marker.is_file():
            raise MemoryServiceError(
                "Memory directory or its marker is unavailable; no file deletions were applied"
            )
        if json.loads(marker.read_text())["token"] != row["token"]:
            raise MemoryServiceError(
                "Memory directory marker does not match this database; no deletions were applied"
            )
        with self.service.database.connect() as connection:
            connection.execute(
                "UPDATE memory_sync_roots SET initialized=1 WHERE id=?", (row["id"],)
            )
        self._root_id = row["id"]

    def _scan(self, report):
        documents = []
        blocked = set()
        incomplete = False

        def failed(error):
            nonlocal incomplete
            incomplete = True
            report.errors.append(str(error))

        for directory, folders, files in os.walk(
            self.directory, followlinks=False, onerror=failed
        ):
            for folder in list(folders):
                path = Path(directory) / folder
                if folder == ".memory-conflicts":
                    folders.remove(folder)
                elif path.is_symlink():
                    folders.remove(folder)
                    failed(f"Skipped symlink directory: {path}")
            for name in sorted(files):
                if not name.lower().endswith(".md"):
                    continue
                path = Path(directory) / name
                relative = path.relative_to(self.directory).as_posix()
                try:
                    if path.is_symlink():
                        raise MemoryValidationError(
                            "Markdown symlinks are not synchronized"
                        )
                    documents.append(parse_document(relative, path.read_bytes()))
                except Exception as error:
                    blocked.add(relative)
                    failed(f"{relative}: {error}")
        by_id = {}
        blocked_ids = set()
        for document in documents:
            if document.memory_id is not None:
                by_id.setdefault(document.memory_id, []).append(document)
        for memory_id, copies in by_id.items():
            if len(copies) > 1:
                blocked_ids.add(memory_id)
                blocked.update(document.path for document in copies)
                report.conflicts.append(
                    f"Duplicate id {memory_id}: {', '.join(document.path for document in copies)}"
                )
                incomplete = True
        return (
            [doc for doc in documents if doc.path not in blocked],
            blocked,
            incomplete,
            blocked_ids,
        )

    def _states(self):
        with self.service.database.connect() as connection:
            return {
                row["memory_id"]: dict(row)
                for row in connection.execute(
                    "SELECT * FROM memory_markdown_sync WHERE root_id=?",
                    (self._root_id,),
                )
            }

    def _save(self, memory, path, file_hash):
        with self.service.database.connect() as connection:
            if path is not None:
                connection.execute(
                    "UPDATE memory_markdown_sync SET path=NULL WHERE root_id=? AND path=? AND memory_id!=?",
                    (self._root_id, path, memory.id),
                )
            connection.execute(
                "INSERT INTO memory_markdown_sync(root_id,memory_id,path,file_hash,memory_hash) VALUES (?,?,?,?,?) "
                "ON CONFLICT(root_id,memory_id) DO UPDATE SET path=excluded.path,file_hash=excluded.file_hash,memory_hash=excluded.memory_hash",
                (self._root_id, memory.id, path, file_hash, memory_hash(memory)),
            )

    def _path(self, relative):
        path = self.directory / relative
        symlink = any(
            (self.directory / Path(*Path(relative).parts[:index])).is_symlink()
            for index in range(1, len(Path(relative).parts) + 1)
        )
        if not path.resolve().is_relative_to(self.directory.resolve()) or symlink:
            raise MemoryConflictError(
                f"Path no longer belongs to the memory directory: {relative}"
            )
        return path

    def _write(self, relative, data, expected_hash):
        self._root()
        path = self._path(relative)
        current = digest(path.read_bytes()) if path.exists() else None
        if current != expected_hash:
            raise MemoryConflictError(
                f"{relative} changed while synchronizing; retry sync"
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
                temporary = Path(handle.name)
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            current = digest(path.read_bytes()) if path.exists() else None
            if current != expected_hash:
                raise MemoryConflictError(
                    f"{relative} changed while synchronizing; retry sync"
                )
            os.replace(temporary, path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return digest(data)

    def _export(self, memory, document, state, report):
        relative = (
            document.path
            if document
            else (state["path"] if state and state["path"] else None)
        )
        if relative is None:
            slug = (
                re.sub(r"[^a-z0-9]+", "-", memory.title.lower()).strip("-")[:60]
                or "memory"
            )
            relative = f"{slug}--{memory.id}.md"
        expected = document.file_hash if document else None
        data = render_memory(memory)
        file_hash = self._write(relative, data, expected)
        self._save(memory, relative, file_hash)
        report.exported += 1

    def _conflict(self, memory, reason, report):
        relative = (
            f".memory-conflicts/{memory.id}-database-{memory_hash(memory)[:16]}.md"
        )
        path = self._path(relative)
        if not path.exists():
            self._write(relative, render_memory(memory), None)
        report.conflicts.append(
            f"{reason}; source preserved, database snapshot: {relative}"
        )

    @staticmethod
    def _matches(document, memory):
        return document.draft == MemoryDraft(
            memory.title,
            memory.content,
            memory.type,
            memory.importance,
            memory.namespace,
            memory.metadata,
        ) and document.removed == (memory.removed_at is not None)

    def _reconcile(self, memory, document, state, incomplete, report, mirror_only):
        current_hash = memory_hash(memory)
        if document is not None and self._matches(document, memory):
            if document.memory_id is None or (state and not state["memory_hash"]):
                self._export(memory, document, state, report)
            else:
                self._save(memory, document.path, document.file_hash)
            return
        database_changed = state is None or state["memory_hash"] != current_hash
        file_changed = document is not None and (
            state is None
            or (
                state["file_hash"] != document.file_hash
                and state["file_hash"] != document.canonical_hash
                and state["memory_hash"] != document_hash(document, memory.id)
            )
        )
        if memory.removed_at is not None:
            if document is None:
                self._save(memory, None, None)
            elif file_changed:
                self._conflict(
                    memory,
                    f"{document.path}: memory {memory.id} is retired and cannot be reimported",
                    report,
                )
            else:
                self._export(memory, document, state, report)
            return
        if document is None:
            if state and state["path"]:
                if incomplete:
                    report.errors.append(
                        f"{state['path']}: deletion deferred because the directory scan was incomplete"
                    )
                elif database_changed or not state["memory_hash"]:
                    self._conflict(
                        memory,
                        f"{state['path']}: file missing while database changed or import is pending",
                        report,
                    )
                elif not mirror_only:
                    self._root()
                    if self._path(state["path"]).exists():
                        raise MemoryConflictError(
                            f"{state['path']} reappeared or changed identity during sync"
                        )
                    retired = self.service.remove(memory.id, expected=memory)
                    self._save(retired, None, None)
                    report.removed += 1
            else:
                self._export(memory, None, state, report)
            return
        if file_changed and database_changed:
            self._conflict(
                memory, f"{document.path}: both Markdown and database changed", report
            )
        elif file_changed and not mirror_only:
            if document.removed:
                memory = self.service.remove(memory.id, expected=memory)
                report.removed += 1
            else:
                draft = document.draft
                memory = self.service.update(
                    memory.id,
                    MemoryUpdate(
                        title=draft.title,
                        content=draft.content,
                        type=draft.type,
                        importance=draft.importance,
                        metadata=draft.metadata,
                    ),
                    expected=memory,
                )
                report.updated += 1
            self._export(memory, document, state, report)
        elif database_changed:
            self._export(memory, document, state, report)
        elif file_changed and mirror_only:
            report.errors.append(
                f"{document.path}: file edits are pending; run manual sync"
            )

    def _run(self, report, target):
        documents, blocked, incomplete, blocked_ids = self._scan(report)
        states = self._states()
        by_path = {state["path"]: state for state in states.values() if state["path"]}
        memories = {memory.id: memory for memory in self.service.all_memories()}
        claimed = {}
        imported_ids = set()
        unresolved_pending = any(
            not state["memory_hash"]
            and state["path"] not in {doc.path for doc in documents}
            for state in states.values()
        )
        for document in documents:
            memory_id = document.memory_id
            if memory_id is None and document.path in by_path:
                memory_id = by_path[document.path]["memory_id"]
            if memory_id is not None:
                if memory_id not in memories:
                    report.errors.append(
                        f"{document.path}: unknown id {memory_id}; omit id to create a new memory"
                    )
                else:
                    claimed.setdefault(memory_id, []).append(document)
            elif target is None:
                try:
                    if unresolved_pending:
                        raise MemoryConflictError(
                            "A previous import is missing its source file; restore that file before importing new files"
                        )
                    if document.removed:
                        raise MemoryValidationError(
                            "A new file without an id cannot be marked removed"
                        )
                    memory = self.service.add(
                        document.draft,
                        markdown_origin=(
                            self._root_id,
                            document.path,
                            document.file_hash,
                        ),
                    )
                    imported_ids.add(memory.id)
                    report.imported += 1
                    memories[memory.id] = memory
                    states = self._states()
                    self._export(memory, document, states[memory.id], report)
                except Exception as error:
                    report.errors.append(f"{document.path}: {error}")
        for memory_id, memory in memories.items():
            if memory_id in imported_ids:
                continue
            if target is not None and memory_id != target:
                continue
            state = states.get(memory_id)
            copies = claimed.get(memory_id, [])
            if (
                memory_id in blocked_ids
                or len(copies) > 1
                or (state and state["path"] in blocked)
            ):
                report.conflicts.append(
                    f"Memory {memory_id}: duplicate or invalid source files; no changes applied"
                )
                continue
            if (
                not copies
                and state
                and any(doc.path == state["path"] for doc in documents)
            ):
                self._conflict(
                    memory, f"{state['path']}: file changed memory identity", report
                )
                continue
            try:
                self._reconcile(
                    memory,
                    copies[0] if copies else None,
                    state,
                    incomplete,
                    report,
                    target is not None,
                )
            except MemoryConflictError as error:
                self._conflict(self.service.get(memory_id), str(error), report)
            except Exception as error:
                report.errors.append(f"Memory {memory_id}: {error}")

    def _sync(self, target=None):
        with self._lock:
            report = MemorySyncReport(str(self.directory))
            try:
                self._root()
                import fcntl

                lock_path = self.directory / ".memory-sync.lock"
                if lock_path.is_symlink():
                    raise MemoryServiceError("Memory sync lock must not be a symlink")
                with lock_path.open("a+b") as handle:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    try:
                        self._run(report, target)
                    finally:
                        fcntl.flock(handle, fcntl.LOCK_UN)
            except Exception as error:
                report.errors.append(str(error))
            self.last_sync_report = report
            return report
