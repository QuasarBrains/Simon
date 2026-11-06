#!/usr/bin/env python3

import argparse
import os
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.text import Text

from agent.agent import Agent
from application.approval import ApprovalBroker
from application.config import load_config
from application.events import PubSub
from application.runlog import SessionLogger
from application.tui import SprocketApp
from llms.llm import create_llm
from memory.config import create_memory_service
from memory.service import MemoryService, SynchronizableMemoryService
from tools.index import Toolbox

console = Console(highlight=False)

verbose = False
silence_actions = False
log_directory = "sprocket-logs"

script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
system_prompt_path = os.path.join(script_dir, "system_prompt.md")

SYSTEM_PROMPT = Path(system_prompt_path).read_text()

if not SYSTEM_PROMPT:
    console.print(
        "[red]Error:[/red] Could not find system prompt. Please ensure system_prompt.md exists."
    )
    exit(1)

LLM_CHOICES = ("openai", "anthropic", "google")

MEMORY_SERVICE: Optional[MemoryService] = None
RUN_LOGGER: Optional[SessionLogger] = None


agent: Optional[Agent] = None
PUBSUB = PubSub()
APPROVALS = ApprovalBroker()
TOOLBOX = Toolbox(pubsub=PUBSUB, approve=APPROVALS.approve)


def handle_errors():
    def report(message, kind):
        RUN_LOGGER.text("errors.log", str(message))
        RUN_LOGGER.event(kind, {"message": str(message)})

    PUBSUB.subscribe("error", lambda error: report(error, "error"))
    PUBSUB.subscribe("agent_error", lambda error: report(error, "agent.error"))
    PUBSUB.subscribe(
        "agent_exception", lambda data: RUN_LOGGER.event("agent.exception", data)
    )


def write_to_file(file_path, log: str):
    RUN_LOGGER.text(Path(file_path).name, log)


def handle_logs():
    def agent_log(log: str):
        write_to_file(os.path.join(log_directory, "agent.log"), log)

    def general_log(log: str):
        write_to_file(os.path.join(log_directory, "general.log"), log)

    def toolbox_log(log: str):
        write_to_file(os.path.join(log_directory, "toolbox.log"), log)

    def agent_message(message: str):
        write_to_file(os.path.join(log_directory, "last_thread.md"), f"Agent: {message}")

    def agent_perception(perception: str):
        write_to_file(
            os.path.join(log_directory, "last_thread.md"), f"Perception:\n{perception}\n"
        )

    PUBSUB.subscribe("agent_log", agent_log)
    PUBSUB.subscribe("general_log", general_log)
    PUBSUB.subscribe("toolbox_log", toolbox_log)
    PUBSUB.subscribe("new_agent_message", agent_message)
    PUBSUB.subscribe("new_agent_prompt", agent_message)
    PUBSUB.subscribe("new_agent_perception", agent_perception)
    for name in (
        "tool.started",
        "tool.finished",
        "tool.approval",
        "agent.waiting",
        "agent.resumed",
        "agent.thinking",
        "agent.context_trimmed",
    ):
        PUBSUB.subscribe(name, lambda data, event=name: RUN_LOGGER.event(event, data))


def submit_user_message(user_input: str):
    write_to_file(os.path.join(log_directory, "last_thread.md"), f"User: {user_input}")
    PUBSUB.publish("new_user_message", user_input)


def show_memory_sync(service, *, print_report: bool = True) -> list[str]:
    if not isinstance(service, SynchronizableMemoryService):
        return []
    report = service.last_sync_report
    notices = [f"Memory sync · {report.directory}", report.summary()]
    notices.extend(f"Warning · {issue}" for issue in report.conflicts + report.errors)
    if print_report:
        console.print(Text(f"{report.directory}\n{report.summary()}", style="dim"))
        for issue in report.conflicts + report.errors:
            console.print(Text(issue, style="yellow"))
    return notices

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the Sprocket chatbot.")
    parser.add_argument(
        "--llm", choices=LLM_CHOICES, help="Override the configured LLM provider."
    )
    parser.add_argument(
        "--verbose", action="store_true", default=None, help="Enable verbose logging."
    )
    parser.add_argument(
        "--silence-actions",
        action="store_true",
        default=None,
        help="Silence actions like function calls and task messages.",
    )
    parser.add_argument(
        "--clear-logs",
        action="store_true",
        help="Explicitly clear existing application logs before starting.",
    )
    parser.add_argument(
        "--show-startup-details",
        action="store_true",
        help=(
            "Show detailed startup metadata in the chat instead of a compact one-line summary."
        ),
    )
    parser.add_argument(
        "--config",
        help="Application config file (defaults to CONFIG_FILE or config.toml).",
    )
    parser.add_argument("--llm-config", help=argparse.SUPPRESS)
    parser.add_argument(
        "--sync-memory",
        action="store_true",
        help="Synchronize the configured Markdown memory directory and exit without starting chat.",
    )
    args = parser.parse_args()

    try:
        config = load_config(args.config or args.llm_config)
        if args.sync_memory:
            service = None
            try:
                service = create_memory_service(config.memory_options())
                if not isinstance(service, SynchronizableMemoryService):
                    parser.error(
                        "Set [memory] backend = 'local' and directory = 'memories' to synchronize Markdown"
                    )
                service.startup()
                show_memory_sync(service)
                raise SystemExit(1 if service.last_sync_report.pending else 0)
            except Exception as error:
                parser.error(f"Memory synchronization failed: {error}")
            finally:
                if service is not None:
                    service.close()
        llm_choice = args.llm or config.llm.provider_name
        settings = config.llm.provider(llm_choice)
        if not settings.api_key or not settings.api_key.get_secret_value():
            parser.error(
                f"Set api_key in [llm.providers.{llm_choice}] in config.toml (or the provider's API-key environment variable)."
            )
        log_directory = str(Path(config.app.log_directory).expanduser())
        os.makedirs(log_directory, exist_ok=True)
        LLM = create_llm(llm_choice, config.llm)
        LLM.startup(SYSTEM_PROMPT)
        RUN_LOGGER = SessionLogger(
            log_directory, LLM.tracker.session_id, clear=args.clear_logs
        )
        RUN_LOGGER.event(
            "session.start",
            {
                "provider": llm_choice,
                "model": LLM.model_name,
                "generation": LLM.provider.settings.generation.model_dump(),
            },
        )
    except Exception as error:
        parser.error(f"LLM configuration failed: {error}")

    def log_usage(event):
        RUN_LOGGER.json("usage.jsonl", event)
        RUN_LOGGER.event(
            "llm.response",
            {
                key: event[key]
                for key in (
                    "id",
                    "request_group_id",
                    "attempt",
                    "provider",
                    "model",
                    "outcome",
                    "response_id",
                    "request_id",
                    "duration_ms",
                    "diagnostics",
                    "error",
                )
            },
        )
        PUBSUB.publish("llm_usage", event)

    def log_llm_event(name, data):
        RUN_LOGGER.event(name, data)
        PUBSUB.publish(name, data)

    LLM.on_usage = log_usage
    LLM.on_event = log_llm_event
    handle_errors()
    handle_logs()
    startup_notices = [f"Logs · {RUN_LOGGER.directory.resolve()}"]
    startup_summary_parts = [f"Logs · {Path(log_directory).name}"]
    if config.legacy_settings:
        startup_notices.append(
            "Config · Legacy settings loaded; see config.example.toml to migrate to one file."
        )
        startup_summary_parts.append("Legacy config")
    if (llm_choice, LLM.model_name) not in LLM.tracker.pricing:
        startup_notices.append(
            "Pricing · Add this model's rates in [[llm.pricing]] for dollar totals; token totals are available now."
        )
        startup_summary_parts.append("Pricing · rates missing")
    else:
        startup_summary_parts.append("Pricing · configured")

    try:
        recall_limit = config.memory.recall_limit
        memory_token_budget = config.memory.context_token_budget
        MEMORY_SERVICE = create_memory_service(config.memory_options())
        if MEMORY_SERVICE is not None:
            MEMORY_SERVICE.startup()
            startup_notices.extend(show_memory_sync(MEMORY_SERVICE, print_report=False))
            if isinstance(MEMORY_SERVICE, SynchronizableMemoryService):
                report = MEMORY_SERVICE.last_sync_report
                startup_summary_parts.append(
                    "Markdown memory · sync needs attention"
                    if report.pending
                    else "Markdown memory · synced"
                )
                RUN_LOGGER.event("memory.sync", asdict(report))
    except Exception as error:
        RUN_LOGGER.text("errors.log", f"Memory startup failed: {error}")
        RUN_LOGGER.event("startup.error", {"message": str(error)})
        if MEMORY_SERVICE is not None:
            MEMORY_SERVICE.close()
        LLM.close()
        parser.error(f"Memory configuration failed: {error}")

    verbose = config.app.verbose if args.verbose is None else args.verbose
    silence_actions = (
        config.app.silence_actions
        if args.silence_actions is None
        else args.silence_actions
    )

    agent = Agent(
        pubsub=PUBSUB,
        llm=LLM,
        toolbox=TOOLBOX,
        verbose=verbose,
        silence_actions=silence_actions,
        memory_service=MEMORY_SERVICE,
        memory_recall_limit=recall_limit,
        memory_token_budget=memory_token_budget,
    )
    try:
        SprocketApp(
            pubsub=PUBSUB,
            agent=agent,
            approvals=APPROVALS,
            provider=llm_choice,
            model=LLM.model_name,
            tracker=LLM.tracker,
            on_user_message=submit_user_message,
            show_actions=not silence_actions,
            verbose=verbose,
            startup_notices=startup_notices,
            startup_summary="  ·  ".join(startup_summary_parts),
            show_startup_details=args.show_startup_details,
        ).run()
    except KeyboardInterrupt:
        pass
    finally:
        agent.stop()
        APPROVALS.close()
        if agent.thread.is_alive():
            agent.thread.join()
        if MEMORY_SERVICE is not None:
            MEMORY_SERVICE.close()
        RUN_LOGGER.event("session.end", {"summary": LLM.tracker.summary()})
        LLM.close()
