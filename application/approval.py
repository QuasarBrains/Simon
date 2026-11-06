"""Coordinate tool approval requests between the agent worker and UI."""

from dataclasses import dataclass, field
from queue import Empty, Queue
from threading import Event, Lock


@dataclass(eq=False)
class ApprovalRequest:
    tool: object
    arguments: dict
    done: Event = field(default_factory=Event)
    approved: bool = False


class ApprovalBroker:
    """Block tool execution until the UI approves or denies the request."""

    def __init__(self):
        self._queue: Queue[ApprovalRequest] = Queue()
        self._lock = Lock()
        self._pending: set[ApprovalRequest] = set()
        self._closed = False

    def approve(self, tool, arguments: dict) -> bool:
        request = ApprovalRequest(tool, arguments)
        with self._lock:
            if self._closed:
                return False
            self._pending.add(request)
            self._queue.put(request)
        try:
            request.done.wait()
            return request.approved
        finally:
            with self._lock:
                self._pending.discard(request)

    def next_request(self) -> ApprovalRequest | None:
        while True:
            try:
                request = self._queue.get_nowait()
            except Empty:
                return None
            if not request.done.is_set():
                return request

    def resolve(self, request: ApprovalRequest, approved: bool) -> None:
        with self._lock:
            if request.done.is_set():
                return
            request.approved = bool(approved) and not self._closed
            request.done.set()

    def close(self) -> None:
        """Deny outstanding approvals and release blocked agent workers."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            for request in self._pending:
                request.approved = False
                request.done.set()
