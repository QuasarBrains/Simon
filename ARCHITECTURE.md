# Sprocket's Architecture & Development

Sprocket's architecture diverges from many modern harnesses in that it treats the agent's context as primary, with a "perception" that acts like a stage for anything that might be useful for the agent to solve its task. This is detailed below. The codebase as a whole is designed to be approachable, maintainable, and simple. This means that in some cases functionality will be sacrificed in the interest of keeping the codebase lightweight and general-purpose. Highly specific or complex functionality is the domain of forks that build off of the baseline. However, the core will evolve over time with improvements to quality and architecture, to make it more useful for those looking to extend Sprocket's functionality. The goal is that a human should be able to put the entire codebase in their own head, ideally in a weekend or shorter if they wanted to.

## Application Interface

Currently the only interface is a 1:1 CLI -> Agent interface where a lightweight CLI activates the agent loop, and messages to the agent are processed accordingly. This is an area of ongoing development, and while the CLI is currently limited, it is the next major area of development. Therefore, current growth directions here are:

1. Decouple the CLI from the application control flow so that the backend can be interfaced with from multiple applications
2. Allow multiple agents to run in parallel through an agent manager

After that, we'll also be exploring adding a REST API around the new backend, allowing for web interfaces and other external integrations to take shape. For now, the CLI uses `click` and `rich` to provide the experience, but this will update.

## The Agent Loop

The overall direction of the project is to provide a metacognitive shell around the core reasoning engine, the LLM. This isn't to pretend that an LLM is like a person in the way it thinks, but rather to allow optimal context engineering through treating the Perception layer as a stage. Accordingly, Sprocket is designed as a loop with three abstracted phases for each "beat" of the agent's run:

First, **Perception** is the context layer, which determines everything the model sees about its environment: memory, workspace state, and the results of its own actions. The goal of this module is to have fine-grained control at every beat of the PRA loop as to what enters the model's context window, and therefore has influence on the reasoning process. Sub-modules of perception include:

- **Roles:** the agent's sense of its inherent domain and general instructions for performing tasks (e.g Developer, Researcher, Helpful Assistant)
- **Tools:** a constrained set of tools deterministically filtered by the current Role, as well as system utilities for default modules
- **Workbenches:** session stateful tools analogous to mini-apps for the agent to use to complete its task (e.g. Todo list, Notepad w/ pages)
- **User Messages:** every message is added to a pool, unseen messages are pulled upon a new beat
- **Environmental Stimuli:** any additional environmental stimuli can be piped into the model's context at discretion
- **Memory:** a semantic memory system where different memories are explicitly stored and retrieved based on context

Second, `Reasoning` is pretty much as simple as passing the "Perception" prompt to the LLM provider, and recieving tool calls back. This is where the LLM provider flow is managed, such as how many tokens to send, how to manage the rolling context window, etc. Right now, there is no concept of compression, but this will change. I don't favor raw compression systems because once the checkpoint is reached, it eliminates classes of context that might very well be useful in future iterations. I have some ideas for a different type of compression that I think will be more viable here, but the reasoning stage is where that gets handled.

Third, the `Action` part of the loop takes the outputs from the `Reasoning` stage—exclusively tool calls–and runs them. These tool calls are the working end of the model, and can be called in parallel given the agents' discretion. These tool calls are synchronously executed so that their results can be seen in the next beat of agent inference.

## Event Sourced Control Flow

A global `PubSub` event orchestration module is integrated throughout the agent's lifecycle and application layer. The perception layer subscribes to events such as `new_environment_stimulus`, which are piped into the context of the next agent beat. Event-sourced architecture is particularly good for modeling non-linear relationships between the moving parts of the agent's foundation. For example, every tool has access to this `PubSub` and can dispatch events as part of its execution, which can then affect agent behavior. For example the `run` definition of the `send_message_to_user` tool:

```py
def run(args: Any, ps: PubSub):
    if not args:
        return "Error running send_message_to_user: No message provided."
    ps.publish("new_agent_message", args.get("content"))
    return "Message sent to user."
```

On the application side, this is how agent messages are displayed in the CLI:

```py
PUBSUB.subscribe("new_agent_message", on_new_agent_message)

def on_new_agent_message(message: str):
    terminal.message(message)
    write_to_file(os.path.join(log_directory, "last_thread.md"), f"Agent: {message}")
```

This pattern is designed to make hooking into every component of the agent lifecycle exceedingly simple, and extensible. As you can see here, a new agent message has a top-level handler where the message is displayed, the message is logged in the log file, and anything else one might be interested in adding is available. Other hooks exist by default:

```py
PUBSUB.subscribe("new_agent_message", on_new_agent_message)
PUBSUB.subscribe("new_agent_prompt", on_new_agent_message_with_prompt)
PUBSUB.subscribe("new_agent_perception", on_new_agent_perception)
PUBSUB.subscribe("agent.waiting", on_agent_waiting)
PUBSUB.subscribe("agent.resumed", on_agent_resumed)
PUBSUB.subscribe("exit_signal", handle_exit)
```

Event channels do not require any explicit registration before subscriptions can be defined and events pushed to it, which is meant to make events simple to use.

## Codebase Organization

The organization separates agent behavior from application wiring:

- `agent/` handles lifecycle and control. The perception → inference → action loop, environment updates, waiting/resuming, and tool orchestration. `agent/tokens.py` keeps a trailing context window within configured model/input boundaries without compaction; see [context and cost policies](documentation/llm-adapters.md).
- `workbenches/` is for session-local mini-applications for the agent to use in its work. See [Adding a workbench](documentation/adding-a-workbench.md).
- `memory/` is a built-in durable memory module that uses SQLite with `sqlite-vss` to bring a semantic memory system directly into Sprocket. `memory/engine.py` handles recall, prompt formatting, and memory tools; service contracts and backends live alongside it. See [Adding memory](documentation/adding-memory.md) for more details.
- `roles/` defines role identities, prompt guidance, and tool selections. `tools/` defines shared tool contracts, registration, execution policies, and capability libraries.
- `application/` assembles the runtime and owns its CLI-facing concerns and shared event transport (`application/events.py`). `application/cli.py` contains the existing CLI implementation and runtime wiring; the root `sprocket.py` is a thin launcher that uses `runpy` to execute `application.cli`. `application/config.py` loads configuration; `application/terminal.py`, `application/cli_input.py`, and `application/runlog.py` provide presentation, interactive input/approval, and session logging.
- `llms/`, `embeddings/`, and `storage/` remain unchanged: chat-provider adapters, independent embedding adapters, and shared database lifecycle/migrations, respectively.

## Memory Control

One of the most important parts of the agent implementation is the ability to edit and control memory quite directly via synchronization. Thus, memory can be optionally linked to a directory where memories will be bidirectionally linked to markdown files, where each memory is its own file in an ever-growing knowledge base. Edits to files will be synchronized to the SQLite-based knowledge base and therefore inline granular management of the agent's "memory" is possible and encouraged to shape performance.
