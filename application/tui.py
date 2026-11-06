"""Fullscreen Textual interface for an interactive Sprocket session."""

import json
from queue import Empty, Queue
from typing import Callable

from rich.markdown import Markdown
from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Header, Input, RichLog, Static

from application.approval import ApprovalBroker, ApprovalRequest
from application.events import PubSub
from application.lib.formatting import dollars


class ApprovalScreen(ModalScreen[bool]):
    """Modal confirmation for a tool call that requires user approval."""

    BINDINGS = [("escape", "deny", "Deny")]

    def __init__(self, request: ApprovalRequest):
        super().__init__()
        self.request = request

    def compose(self) -> ComposeResult:
        tool = self.request.tool
        with Container(id="approval-dialog"):
            yield Static(f"Approval required · {tool.name}", id="approval-title")
            yield Static(
                json.dumps(self.request.arguments, indent=2, ensure_ascii=False),
                id="approval-arguments",
                markup=False,
            )
            yield Static(tool.description, id="approval-description", markup=False)
            with Horizontal(id="approval-buttons"):
                yield Button("Approve once", id="approve", variant="success")
                yield Button("Deny", id="deny", variant="error")

    @on(Button.Pressed)
    def handle_button(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "approve")

    def action_deny(self) -> None:
        self.dismiss(False)


class SprocketApp(App[None]):
    """Render session events and collect user input on Textual's event loop."""

    TITLE = "Sprocket"
    CSS = """
    Screen {
        layout: vertical;
    }
    #startup-metadata {
        height: 1;
        padding: 0 2;
        color: $text-muted;
    }
    #conversation {
        height: 1fr;
        border: round $primary;
        padding: 0 1;
        margin: 0 1;
    }
    #status {
        height: 2;
        padding: 0 2;
        color: $text-muted;
    }
    #prompt {
        height: 3;
        margin: 0 1 1 1;
    }
    ApprovalScreen {
        align: center middle;
    }
    #approval-dialog {
        width: 80%;
        max-width: 100;
        height: auto;
        max-height: 85%;
        padding: 1 2;
        border: round $warning;
        background: $panel;
    }
    #approval-title {
        height: auto;
        padding-bottom: 1;
        text-style: bold;
        color: $warning;
    }
    #approval-arguments {
        height: auto;
        max-height: 1fr;
        overflow-y: auto;
        padding: 1;
        border: round $primary;
    }
    #approval-description {
        height: auto;
        max-height: 6;
        padding: 1 0;
        overflow-y: auto;
    }
    #approval-buttons {
        height: 3;
        align-horizontal: right;
    }
    #approval-buttons Button {
        margin-left: 1;
    }
    """

    _UI_EVENTS = (
        "new_agent_message",
        "new_agent_prompt",
        "agent.waiting",
        "agent.resumed",
        "agent.thinking",
        "tool.started",
        "tool.finished",
        "tool.approval",
        "agent_error",
        "error",
        "agent_exception",
        "agent.context_trimmed",
        "llm.retry",
        "llm_usage",
    )

    def __init__(
        self,
        *,
        pubsub: PubSub,
        agent,
        approvals: ApprovalBroker,
        provider: str,
        model: str,
        tracker,
        on_user_message: Callable[[str], None],
        show_actions: bool = True,
        verbose: bool = False,
        startup_notices: list[str] | None = None,
        startup_summary: str = "",
        show_startup_details: bool = False,
    ):
        super().__init__()
        self.pubsub = pubsub
        self.agent = agent
        self.approvals = approvals
        self.provider = provider
        self.model = model
        self.tracker = tracker
        self.on_user_message = on_user_message
        self.show_actions = show_actions
        self.verbose = verbose
        self.startup_notices = startup_notices or []
        self.startup_summary = startup_summary
        self.show_startup_details = show_startup_details
        self._events: Queue[tuple[str, object]] = Queue()
        self._conversation: RichLog
        self._status_widget: Static
        self._prompt_widget: Input
        self._pending_approval: ApprovalRequest | None = None
        self._waiting_mode: str | None = None
        self._agent_started = False
        self._state = "Starting"
        for event_name in self._UI_EVENTS:
            self.pubsub.subscribe(
                event_name,
                lambda data, name=event_name: self._events.put((name, data)),
            )

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        if self.startup_summary and not self.show_startup_details:
            yield Static(
                Text(self.startup_summary, no_wrap=True, overflow="ellipsis"),
                id="startup-metadata",
            )
        yield RichLog(id="conversation", wrap=True, markup=False, auto_scroll=True)
        yield Static("Starting agent…", id="status")
        yield Input(placeholder="Send a message · type 'exit' to quit", id="prompt")
        yield Footer()

    def on_mount(self) -> None:
        self._conversation = self.query_one("#conversation", RichLog)
        self._status_widget = self.query_one("#status", Static)
        self._prompt_widget = self.query_one("#prompt", Input)
        self._conversation.write(
            Text(f"Sprocket · {self.provider} / {self.model}", style="bold blue")
        )
        self._conversation.write(
            Text("Session ready. Type 'exit' or press Ctrl+C to quit.", style="dim")
        )
        if self.show_startup_details:
            for notice in self.startup_notices:
                self._write_notice(notice, error=notice.startswith("Warning ·"))
        self._prompt_widget.focus()
        self.set_interval(0.05, self._drain_events)
        self.set_interval(1.0, self._refresh_status)
        self.agent.start()
        self._agent_started = True
        self._state = "Ready"
        self._refresh_status()

    @on(Input.Submitted)
    def handle_input(self, event: Input.Submitted) -> None:
        value = event.value
        event.input.value = ""
        if not value.strip():
            return
        if value.strip().lower() == "exit":
            self.exit()
            return
        self._write_user_message(value)
        try:
            self.on_user_message(value)
        except Exception as error:
            self._write_notice(f"Could not submit message: {error}", error=True)

    def _write_user_message(self, message: str) -> None:
        self._conversation.write(Text("You", style="bold cyan"))
        self._conversation.write(Text(message))

    def _write_notice(self, message: str, *, error: bool = False) -> None:
        style = "bold red" if error else "dim"
        self._conversation.write(Text(message, style=style))

    def _show_action(self, name: str) -> bool:
        return self.show_actions and (
            self.verbose or not name.startswith("interaction.")
        )

    def _drain_events(self) -> None:
        while True:
            try:
                event_name, data = self._events.get_nowait()
            except Empty:
                break
            self._handle_event(event_name, data)

        if self._agent_started and not self.agent.running:
            self._prompt_widget.disabled = True
            if self._state != "Stopped":
                self._state = "Stopped"
                self._write_notice("Agent stopped. Press Ctrl+C to close the session.")
                self._refresh_status()

        if self._pending_approval is None:
            request = self.approvals.next_request()
            if request is not None:
                self._pending_approval = request
                self.push_screen(ApprovalScreen(request), self._approval_decided)

    def _approval_decided(self, approved: bool | None) -> None:
        request, self._pending_approval = self._pending_approval, None
        if request is not None:
            self.approvals.resolve(request, approved is True)
        self._drain_events()

    def _handle_event(self, event_name: str, data: object) -> None:
        if event_name in ("new_agent_message", "new_agent_prompt"):
            self._conversation.write(Text("Sprocket", style="bold blue"))
            self._conversation.write(Markdown(str(data)))
        elif event_name == "agent.waiting":
            self._waiting_mode = str(data.get("mode", "stimulus")) if isinstance(data, dict) else "stimulus"
            self._state = "Waiting for your reply" if self._waiting_mode == "user" else "Waiting for a stimulus"
        elif event_name == "agent.resumed":
            self._waiting_mode = None
            self._state = "Working"
        elif event_name == "agent.thinking":
            active = isinstance(data, dict) and data.get("active")
            self._state = "Thinking…" if active else "Working"
        elif event_name == "tool.started":
            if isinstance(data, dict):
                name = str(data.get("name", "tool"))
                if self._show_action(name):
                    self._write_notice(f"↳ Running {name}")
                    self._state = f"Running {name}"
        elif event_name == "tool.finished":
            if isinstance(data, dict):
                name = str(data.get("name", "tool"))
                if self._show_action(name):
                    result = "failed" if data.get("is_error") else "finished"
                    self._write_notice(f"↳ {name} {result}")
        elif event_name == "tool.approval":
            if isinstance(data, dict):
                name = str(data.get("name", "invocation"))
                if self._show_action(name):
                    decision = "approved" if data.get("approved") else "denied"
                    self._write_notice(f"↳ Tool {name} {decision}")
        elif event_name in ("agent_error", "error"):
            self._write_notice(f"Agent error: {data}", error=True)
        elif event_name == "agent_exception":
            message = data.get("message", "Unknown agent exception") if isinstance(data, dict) else str(data)
            self._write_notice(f"Agent exception: {message}", error=True)
        elif event_name == "agent.context_trimmed" and isinstance(data, dict):
            self._write_notice(
                f"Context trimmed · removed {data.get('removed_messages', 0)} messages",
            )
        elif event_name == "llm.retry" and isinstance(data, dict):
            self._write_notice(
                f"Output limit reached; retrying with {data.get('max_output_tokens', '?')} tokens"
            )
        self._refresh_status()

    def _refresh_status(self) -> None:
        summary = self.tracker.summary()
        requests = summary.get("requests", 0)
        tokens = summary.get("input_tokens", 0) + summary.get("output_tokens", 0)
        status = f"{self.provider} / {self.model}  ·  {self._state}"
        if requests:
            status += f"  ·  {requests} request{'s' if requests != 1 else ''}"
        if tokens:
            status += f"  ·  {tokens:,} tokens"
        unknown = summary.get("unknown_requests", 0)
        if requests and unknown == requests:
            cost = "Cost unavailable"
        elif not requests and not self.tracker.pricing:
            cost = "Cost unavailable"
        else:
            cost = "Session " + dollars(summary.get("known_cost", "0"))
            if unknown:
                cost += " (partial)"
            elif summary.get("session_budget") is not None:
                cost += " / " + dollars(summary["session_budget"])
        status += f"  ·  {cost}"
        self._status_widget.update(status)
        prompt = self._prompt_widget
        prompt.placeholder = (
            "Reply to Sprocket…" if self._waiting_mode == "user"
            else "Send a stimulus…" if self._waiting_mode == "stimulus"
            else "Send a message · type 'exit' to quit"
        )
