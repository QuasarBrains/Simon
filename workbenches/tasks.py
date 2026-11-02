from dataclasses import dataclass
from pathlib import Path
from typing import Any, List

from application.events import PubSub
from application.terminal import console
from tools.index import Tool, ToolResult
from workbenches.workbench import Workbench


@dataclass
class Task:
    id: str
    description: str
    requirements: List[str]
    completed: bool
    notes: str


class Tasks(Workbench):
    name = "tasks"

    pubsub: PubSub
    tasks: List[Task]
    silence_actions: bool

    def __init__(self, pubsub: PubSub, silence_actions: bool) -> None:
        super().__init__()
        self.pubsub = pubsub
        self.tasks = []
        self.silence_actions = silence_actions

    def get_tools(self) -> list[Tool]:
        return [
            self.disclosure_tool(),
            self.create_task_tool(),
            self.complete_task_tool(),
            self.modify_task_notes_tool(),
            self.modify_task_requirements_tool(),
            self.export_tool(),
        ]

    def has_pending_work(self) -> bool:
        return self.has_incomplete_tasks()

    def active_task_ids(self) -> list[str]:
        return [task.id for task in self.get_incomplete_tasks()]

    def get_new_task_id(self) -> str:
        return f"task_{len(self.tasks) + 1}"

    def get_incomplete_tasks(self) -> List[Task]:
        return [t for t in self.tasks if not t.completed]

    def get_completed_tasks(self) -> List[Task]:
        return [t for t in self.tasks if t.completed]

    def has_incomplete_tasks(self) -> bool:
        return any(not t.completed for t in self.tasks)

    def get_tasks_described(self, tasks: List[Task]):
        description = ""
        for task in tasks:
            description += "<task>\n"
            description += f"Task ID: {task.id}\n"
            description += f"Description: {task.description}\n"
            description += "Requirements: {}\n".format(
                "\n".join([f"- {req}" for req in task.requirements])
            )
            description += f"Notes: {task.notes}\n"
            description += f"Completed: {task.completed}\n\n"
            description += "</task>\n"
        return description

    def build_view(self) -> str:
        incomplete_tasks = self.get_incomplete_tasks()
        completed_tasks = self.get_completed_tasks()
        task_view = (
            f"Incomplete tasks: {len(incomplete_tasks)}\n"
            f"Completed tasks: {len(completed_tasks)}\n"
        )
        if not self.is_open():
            return task_view

        task_view += self.get_tasks_described(incomplete_tasks)
        if completed_tasks:
            task_view += "Completed task summary:\n"
            task_view += "\n".join(
                f"- {task.id}: {task.description}" for task in completed_tasks
            )
            task_view += "\n"
        return task_view

    def get_incomplete_tasks_described(self):
        return self.get_tasks_described(self.get_incomplete_tasks())

    def create_task(
        self, description: str, requirements: List[str], completed=False
    ) -> bool:
        try:
            id = self.get_new_task_id()
            task = Task(
                id=id,
                description=description,
                requirements=requirements,
                completed=completed,
                notes="",
            )
            self.tasks.append(task)
            if not self.silence_actions:
                console.print(
                    f"Created task: [italic]{description}[/italic]",
                    style="green",
                )
            self.pubsub.publish("task_created", task)
            return True
        except Exception as e:
            console.print(f"Error creating task: {e}", style="bold red")
            return False

    def complete_task(self, task_id: str) -> bool:
        try:
            for task in self.tasks:
                if task.id == task_id:
                    task.completed = True
                    self.pubsub.publish("task_completed", task)
                    if not self.silence_actions:
                        console.print(
                            f"Completed task: [italic]{task.description}[/italic]",
                            style="green",
                        )
                    return True
            console.print(f"Task {task_id} not found.", style="bold red")
            return False
        except Exception as e:
            console.print(f"Error completing task: {e}", style="bold red")
            return False

    def modify_task_notes(self, task_id: str, notes: str) -> bool:
        try:
            for task in self.tasks:
                if task.id == task_id:
                    task.notes = notes
                    self.pubsub.publish("task_notes_modified", task)
                    if not self.silence_actions:
                        console.print(
                            f"Modified notes for task: [italic]{task.description}[/italic]",
                            style="green",
                        )
                    return True
            console.print(f"Task {task_id} not found.", style="bold red")
            return False
        except Exception as e:
            console.print(f"Error modifying notes: {e}", style="bold red")
            return False

    def modify_task_requirements(self, task_id: str, requirements: List[str]) -> bool:
        try:
            for task in self.tasks:
                if task.id == task_id:
                    task.requirements = requirements
                    self.pubsub.publish("task_requirements_modified", task)
                    if not self.silence_actions:
                        console.print(
                            f"Modified requirements for task: [italic]{task.description}[/italic]",
                            style="green",
                        )
                    return True
            console.print(f"Task {task_id} not found.", style="bold red")
            return False
        except Exception as e:
            console.print(f"Error modifying requirements: {e}", style="bold red")
            return False

    def export(
        self,
        path: str,
        mode: str = "file",
        overwrite: bool = False,
        create_parents: bool = False,
    ) -> ToolResult:
        if not path.strip():
            raise ValueError("An export path is required.")
        if mode not in {"file", "directory"}:
            raise ValueError("Export mode must be 'file' or 'directory'.")
        destination = Path(path).expanduser().absolute()

        def render_task(task: Task, level: int) -> str:
            heading = "#" * (level + 1)
            requirements = "\n".join(f"- {item}" for item in task.requirements)
            return (
                f"{'#' * level} {task.id}\n\n"
                f"{task.description}\n\n"
                f"Completed: {task.completed}\n\n"
                f"{heading} Requirements\n\n{requirements}\n\n"
                f"{heading} Notes\n\n{task.notes}\n"
            )

        if mode == "file":
            tasks = "\n".join(render_task(task, 2) for task in self.tasks)
            exports = {destination: f"# Tasks\n\n{tasks}"}
        else:
            if destination.exists() and not destination.is_dir():
                raise ValueError(f"Not a directory: {destination}")
            exports = {
                destination / f"{task.id}.md": render_task(task, 1)
                for task in self.tasks
            }

        for target in exports:
            if target.is_symlink():
                raise ValueError(f"Refusing to export over a symbolic link: {target}")
            if target.exists():
                if not target.is_file():
                    raise ValueError(f"Not a regular file: {target}")
                if not overwrite:
                    raise FileExistsError(f"File exists; set overwrite=true: {target}")

        if mode == "directory":
            destination.mkdir(parents=create_parents, exist_ok=True)
        elif create_parents:
            destination.parent.mkdir(parents=True, exist_ok=True)

        bytes_written = 0
        for target, content in exports.items():
            data = content.encode("utf-8")
            with target.open("wb" if overwrite else "xb") as stream:
                stream.write(data)
            bytes_written += len(data)
        return ToolResult(
            content=f"Tasks exported to {destination}.",
            metadata={
                "path": str(destination),
                "mode": mode,
                "files": [str(target) for target in exports],
                "task_count": len(self.tasks),
                "completed_count": len(self.get_completed_tasks()),
                "incomplete_count": len(self.get_incomplete_tasks()),
                "bytes_written": bytes_written,
            },
        )

    def export_tool(self) -> Tool:
        def run(args: Any, pubsub: PubSub):
            return self.export(**args)

        return Tool(
            name="tasks.export",
            description=(
                "Export all incomplete and completed tasks, including IDs, descriptions, "
                "requirements, completion status, and notes, as UTF-8 Markdown: one combined "
                "file or a directory containing task_<number>.md files. Existing files "
                "require overwrite=true; missing parent directories require "
                "create_parents=true. Does not change tasks or disclosure state."
            ),
            function=run,
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "minLength": 1, "maxLength": 4096},
                    "mode": {
                        "type": "string",
                        "enum": ["file", "directory"],
                        "default": "file",
                    },
                    "overwrite": {"type": "boolean", "default": False},
                    "create_parents": {"type": "boolean", "default": False},
                },
                "required": ["path"],
                "additionalProperties": False,
            },
        )

    def create_task_tool(self) -> Tool:
        def run(args: Any, pubsub: PubSub):
            description = args.get("description")
            requirements = args.get("requirements")
            completed = args.get("completed") or False
            id = self.get_new_task_id()

            if not description:
                return "Error creating task: No description provided."
            if not requirements:
                return "Error creating task: No requirements provided."
            if not isinstance(requirements, list):
                return "Error creating task: Requirements must be a list."

            worked = self.create_task(description, requirements, completed)
            if not worked:
                return "Error creating task."
            return f"Task created with id {id}."

        return Tool(
            name="create_task",
            description="Use this tool to create a new task.",
            function=run,
            parameters={
                "type": "object",
                "properties": {
                    "description": {
                        "type": "string",
                        "description": "A description of the overall task.",
                    },
                    "requirements": {
                        "type": "array",
                        "description": "A list of requirements for the task.",
                        "items": {"type": "string"},
                    },
                    "completed": {
                        "type": "boolean",
                        "description": "Whether the task is currently completed. Defaults to False.",
                    },
                },
                "required": ["description", "requirements"],
            },
        )

    def complete_task_tool(self) -> Tool:
        def run(args: Any, pubsub: PubSub):
            task_id = args.get("task_id")
            if task_id is None:
                return f"Task with id {task_id} not found."
            worked = self.complete_task(task_id)
            if not worked:
                return f"Error completing task with id {task_id}."
            return f"Task with id {task_id} marked as complete."

        return Tool(
            name="complete_task",
            description="Use this to mark a task complete.",
            function=run,
            parameters={
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "The id of the task to complete.",
                    },
                },
                "required": ["task_id"],
            },
        )

    def modify_task_notes_tool(self) -> Tool:
        def run(args: Any, pubsub: PubSub):
            task_id = args.get("task_id")
            notes = args.get("notes")
            if task_id is None:
                return f"Task with id {task_id} not found."
            worked = self.modify_task_notes(task_id, notes)
            if not worked:
                return f"Error modifying notes for task with id {task_id}."
            return f"Notes for task with id {task_id} modified."

        return Tool(
            name="modify_task_notes",
            description="Use this to modify the notes for a task.",
            function=run,
            parameters={
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "The id of the task to modify.",
                    },
                    "notes": {
                        "type": "string",
                        "description": "The new notes for the task.",
                    },
                },
                "required": ["task_id", "notes"],
            },
        )

    def modify_task_requirements_tool(self) -> Tool:
        def run(args: Any, pubsub: PubSub):
            task_id = args.get("task_id")
            requirements = args.get("requirements")
            if task_id is None:
                return f"Task with id {task_id} not found."
            if not requirements:
                return "Error modifying task: No requirements provided."
            if not isinstance(requirements, list):
                return "Error modifying task: Requirements must be a list."
            worked = self.modify_task_requirements(task_id, requirements)
            if not worked:
                return f"Error modifying requirements for task with id {task_id}."
            return f"Requirements for task with id {task_id} modified."

        return Tool(
            name="modify_task_requirements",
            description="Use this to modify the requirements for a task.",
            function=run,
            parameters={
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "The id of the task to modify.",
                    },
                    "requirements": {
                        "type": "array",
                        "description": "The new requirements for the task.",
                        "items": {"type": "string"},
                    },
                },
                "required": ["task_id", "requirements"],
            },
        )
