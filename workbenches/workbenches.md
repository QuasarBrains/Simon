# Workbenches

Workbenches are session scoped tool classes that expose working state through the agent's environment on every reasoning iteration. They are ephemeral by default; durable information belongs in memory. A workbench can wrap another application if it exposes the same interface.

## Standard interface

Implement `Workbench` from `workbenches/workbench.py`:

- `name`: a unique workbench identifier.
- `state`: workbench-owned state, including an `open` disclosure flag. Extend `WorkbenchState` for additional view state.
- `build_view() -> str`: the current environment view. Open workbenches show working details; closed workbenches show a minimized overview, not an empty or deleted workbench.
- `get_tools() -> list[Tool]`: tools that manipulate or read workbench state. Include `self.disclosure_tool()` to expose `workbench.<name>.set_open(open)`.
- `has_pending_work() -> bool`: optional lifecycle hook, false by default. Pending work keeps the loop active unless the agent explicitly waits.
- `active_task_ids() -> list[str]`: optional cost-attribution hook, empty by default. A single active task receives inference costs; otherwise costs are shared.

`open()`, `close()`, and `is_open()` manage disclosure only. Closing a workbench does not delete its state, disable tools, complete tasks, or pause the agent.

## Lifecycle integration

`Agent.workbenches` holds the session's workbench instances. Default initialization registers each workbench's tools independently of roles. On every prompt build, the agent appends current workbench views to the `Environment` section. The loop consults workbench hooks for pending work and inference cost attribution; it does not need workbench-specific logic.

To add a workbench, implement the interface and add an instance to `Agent.workbenches` during construction. No environment subscriptions, custom prompt sections, or agent-loop branches are required.

## Built-in workbenches

### Tasks

Replaces the former standalone agency module. Open views show incomplete tasks with requirements and notes, plus a compact completed-task summary. Closed views show counts. Existing task tool names (`create_task`, `complete_task`, `modify_task_notes`, `modify_task_requirements`) and task events are preserved. Incomplete tasks sustain active work but never override an explicit wait.

`tasks.export(path, mode="file", overwrite=false, create_parents=false)` writes all incomplete and completed tasks as UTF-8 Markdown, including IDs, descriptions, requirements, completion status, and notes. File mode creates one combined document; `mode="directory"` creates one `task_<number>.md` file per task. Export works while the workbench is closed and does not change task state or pending work. Relative paths use the process working directory and `~` is expanded. Existing files require `overwrite=true`, missing parent directories require `create_parents=true`, and symbolic-link output files are rejected. Unrelated files are left alone. Results report exported paths, task/completion counts, and bytes written. Directory exports check collisions before writing but are not transactional if an I/O failure occurs mid-export.

```json
{ "path": "tasks.md" }
```

```json
{ "path": "exports/tasks", "mode": "directory", "create_parents": true }
```

### Notepad

Stores session-local pages and notes. The open view shows a table of contents and the selected page; the closed view shows only the table of contents. Creating a page selects it. `notepad.set_page` changes the visible page; `notepad.read_page` reads another page without changing selection or disclosure. Notes expose stable IDs for reading, editing, and deletion. Merely retaining notes does not keep the agent awake.

Tools: `notepad.new_page`, `notepad.set_page`, `notepad.read_page`, `notepad.write_note`, `notepad.read_note`, `notepad.edit_note`, `notepad.delete_note`, and `notepad.export`.

`notepad.export(path, mode="file", overwrite=false, create_parents=false)` writes all pages and notes as UTF-8 Markdown without changing session state. File mode produces one combined document; `mode="directory"` creates a destination directory with one `page_<number>.md` file per page. Page titles and note IDs are included, and note content is preserved. Export is independent of page selection and disclosure state. Relative paths use the process working directory; `~` is expanded. Existing files require `overwrite=true`; missing parent directories require `create_parents=true`. Symbolic-link output files are rejected, and unrelated directory contents are left alone. Results report exported paths, page/note counts, and bytes written. Directory exports preflight file collisions but are not transactional: an I/O failure during writing can leave a partial export.

Examples:

```json
{ "path": "notes.md" }
```

```json
{ "path": "exports/research", "mode": "directory", "create_parents": true }
```
