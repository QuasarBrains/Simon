# Adding a workbench

Workbenches are session-scoped tool classes that expose working state through the agent's environment on every reasoning iteration. They are ephemeral by default; durable information belongs in memory. A workbench can wrap another application if it exposes the same interface.

Use a workbench when operations share editable session state that should remain visible to the agent. Use a [tool library](adding-a-tool.md) for independent operations, or [memory](adding-memory.md) for durable information and automatic recall.

## Standard interface

Extend [`Workbench`](../workbenches/workbench.py):

- `name`: a unique workbench identifier. It also forms part of the disclosure tool's name, so use a provider-safe identifier such as `scratchpad`.
- `state`: workbench-owned state, including an `open` disclosure flag. Extend `WorkbenchState` for additional view or working state. Keep mutable state on the instance, not on the class.
- `build_view() -> str`: the current environment view. Open workbenches show working details; closed workbenches show a minimized overview, not an empty or deleted workbench.
- `get_tools() -> list[Tool]`: tools that read or manipulate the instance's state. Include `self.disclosure_tool()` to expose `workbench.<name>.set_open(open)`.
- `has_pending_work() -> bool`: optional lifecycle hook, false by default. Pending work keeps the loop active unless the agent explicitly waits.
- `active_task_ids() -> list[str]`: optional cost-attribution hook, empty by default. A single active task ID across workbenches receives inference costs; otherwise costs are shared. Use stable IDs for actual tasks, not arbitrary stored items.

`open()`, `close()`, and `is_open()` manage disclosure only. Closing a workbench does not delete its state, disable tools, complete tasks, or pause the agent. Retaining data alone should not imply pending work.

## Define a workbench

For example, create `workbenches/scratchpad.py`:

```python
from dataclasses import dataclass

from tools.index import Tool, ToolResult
from workbenches.workbench import Workbench, WorkbenchState


@dataclass
class ScratchpadState(WorkbenchState):
    content: str = ""


class Scratchpad(Workbench):
    name = "scratchpad"

    def __init__(self) -> None:
        super().__init__()
        self.state = ScratchpadState()

    def build_view(self) -> str:
        if not self.is_open():
            return f"Scratchpad closed ({len(self.state.content)} characters stored)."
        return self.state.content or "Scratchpad is empty."

    def get_tools(self) -> list[Tool]:
        def replace(arguments, pubsub):
            self.state.content = arguments["content"]
            return ToolResult(content="Scratchpad content replaced.")

        return [
            self.disclosure_tool(),
            Tool(
                name="scratchpad.replace",
                description="Replace the session scratchpad's full content.",
                function=replace,
                parameters={
                    "type": "object",
                    "properties": {"content": {"type": "string"}},
                    "required": ["content"],
                    "additionalProperties": False,
                },
            ),
        ]
```

Tool callables receive `(arguments, pubsub)` and can close over the workbench instance. Return a string or `ToolResult`; use explicit error statuses for expected failures. Tool names must be unique across the active toolbox, including shared operations and role tools. See [Adding a tool](adding-a-tool.md) for schemas, result metadata, approval policies, and naming restrictions.

Keep `build_view()` free of mutations and external side effects: it is called on every prompt build. Keep closed views compact, and avoid repeating large state in every iteration. Override lifecycle hooks only if the workbench has pending work or task attribution to contribute; this scratchpad needs neither.

## Register it with the agent

In [`agent/agent.py`](../agent/agent.py), import the class:

```python
from workbenches.scratchpad import Scratchpad
```

Include an instance in the existing `Agent.__init__` workbench list:

```python
self.workbenches = [
    Tasks(pubsub=pubsub, silence_actions=silence_actions),
    Notepad(),
    Scratchpad(),
]
```

Construct each workbench before `initialize_default_tools()` runs. Pass only the dependencies it needs; PubSub and the LLM are not required by the base interface. There is no automatic module discovery or separate registration configuration.

`Agent.workbenches` holds the session's instances. Initialization registers each workbench's tools independently of roles, so they remain available across role changes. On each prompt build, the agent appends nonempty views under `Environment`. The loop consults the pending-work and cost-attribution hooks without needing workbench-specific branches. No environment subscriptions or custom prompt sections are required for this integration.

## Validate the integration

Before using a new workbench, check that:

- A fresh instance has independent state and can build an empty-state view.
- Tools register and execute through `Toolbox`, including schema validation and error handling.
- Mutations appear in the next open view; closed views remain compact without losing state or tool availability.
- Role changes leave the shared workbench tools registered.
- Pending work and explicit waits interact as intended, if those hooks are overridden.
- Any export or persistence operation has explicit file-writing safeguards and does not silently change session state.

## Built-in workbenches

### Tasks

[`Tasks`](../workbenches/tasks.py) replaces the former standalone agency module. Open views show incomplete tasks with requirements and notes, plus a compact completed-task summary. Closed views show counts. Existing task tool names (`create_task`, `complete_task`, `modify_task_notes`, `modify_task_requirements`) and task events are preserved. Incomplete tasks sustain active work but never override an explicit wait.

`tasks.export(path, mode="file", overwrite=false, create_parents=false)` writes all incomplete and completed tasks as UTF-8 Markdown, including IDs, descriptions, requirements, completion status, and notes. File mode creates one combined document; `mode="directory"` creates one `task_<number>.md` file per task. Export works while the workbench is closed and does not change task state or pending work. Results report exported paths, task/completion counts, and bytes written.

```json
{ "path": "tasks.md" }
```

```json
{ "path": "exports/tasks", "mode": "directory", "create_parents": true }
```

### Notepad

[`Notepad`](../workbenches/notepad.py) stores session-local pages and notes. The open view shows a table of contents and the selected page; the closed view shows only the table of contents. Creating a page selects it. `notepad.set_page` changes the visible page; `notepad.read_page` reads another page without changing selection or disclosure. Notes expose stable IDs for reading, editing, and deletion. Merely retaining notes does not keep the agent awake.

Tools: `notepad.new_page`, `notepad.set_page`, `notepad.read_page`, `notepad.write_note`, `notepad.read_note`, `notepad.edit_note`, `notepad.delete_note`, and `notepad.export`.

`notepad.export(path, mode="file", overwrite=false, create_parents=false)` writes all pages and notes as UTF-8 Markdown without changing session state. File mode produces one combined document; `mode="directory"` creates one `page_<number>.md` file per page. Page titles and note IDs are included, and note content is preserved. Export is independent of page selection and disclosure state. Results report exported paths, page/note counts, and bytes written.

```json
{ "path": "notes.md" }
```

```json
{ "path": "exports/research", "mode": "directory", "create_parents": true }
```

### Export safeguards

Both export tools resolve relative paths from the process working directory and expand `~`. Existing files require `overwrite=true`; missing parent directories require `create_parents=true`. Symbolic-link output files are rejected, and unrelated directory contents are left alone. Directory exports check file collisions before writing but are not transactional: an I/O failure during writing can leave a partial export.
