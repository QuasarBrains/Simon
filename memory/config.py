from collections.abc import Callable, Mapping
from pathlib import Path

from memory.service import MemoryService


def local_from_config(config: Mapping[str, str]) -> MemoryService:
    from embeddings.config import create_embedding_model
    from memory.sqlite import SQLiteMemoryService
    from storage.database import Database

    service = SQLiteMemoryService(
        Database.from_config(config), create_embedding_model(config)
    )
    directory = config.get("MEMORY_DIRECTORY")
    if directory:
        from memory.markdown import MarkdownMemoryService

        path = Path(directory).expanduser()
        if not path.is_absolute():
            path = Path(__file__).resolve().parent.parent / path
        return MarkdownMemoryService(service, path)
    return service


MEMORY_FACTORIES: dict[str, Callable[[Mapping[str, str]], MemoryService]] = {
    "local": local_from_config,
}


def create_memory_service(config: Mapping[str, str]) -> MemoryService | None:
    choice = (config.get("MEMORY_BACKEND") or "none").strip() or "none"
    if choice == "none":
        return None
    if choice not in MEMORY_FACTORIES:
        raise ValueError(f"Unknown MEMORY_BACKEND: {choice}")
    namespace = config.get("MEMORY_NAMESPACE") or "default"
    if namespace != "default":
        raise ValueError("Memory currently supports only MEMORY_NAMESPACE=default")
    return MEMORY_FACTORIES[choice](config)
