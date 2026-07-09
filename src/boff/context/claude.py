"""Claude Code context provider.

Claude keys a project's runtime state by its absolute path, dash-encoded into a
directory under ``~/.claude/projects/``. Moving that state to another machine
requires re-keying: recompute the encoded directory from the target path and
rewrite the absolute path embedded in transcripts. Plans live globally in
``~/.claude/plans/`` and are bound to a project by scanning its transcripts for
references to plan files.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

from boff.context.base import ContextProvider, project_identity, read_lenient, workspace_root
from boff.context.digest import DigestFacts, render_digest
from boff.context.ir import (
    ContextBundle,
    HandoffDigest,
    MemoryDoc,
    Plan,
    SessionRecord,
    TodoList,
)
from boff.jsonutil import as_json_array, as_json_object
from boff.platform_layout import CLAUDE_LAYOUT, PlatformLayout
from boff.types import FileOperation, MergeStrategy, Operation, Scope

_EDIT_TOOLS = {"Edit", "Write", "NotebookEdit", "MultiEdit"}
_MAX_GOALS = 6
_MAX_EDITS = 25


@dataclass
class _ScanState:
    """Mutable accumulator threaded through transcript scanning."""

    facts: DigestFacts
    prefix: str
    plans_marker: str
    collect_goals: bool = True
    plan_paths: set[str] = field(default_factory=set[str])
    edited: list[str] = field(default_factory=list[str])
    seen_edits: set[str] = field(default_factory=set[str])
    todo_items: list[str] = field(default_factory=list[str])


def encode_project_dir(abs_path: str) -> str:
    """Encode an absolute project path the way Claude Code names its project dir.

    Every non-alphanumeric character becomes ``-`` (e.g. ``/home/caf/x`` becomes
    ``-home-caf-x``). This must match Claude's own scheme so resume works.
    """
    return re.sub(r"[^A-Za-z0-9]", "-", abs_path)


def _iter_text_blocks(content: object) -> list[str]:
    """Pull plain-text fragments out of a message ``content`` field."""
    if isinstance(content, str):
        return [content]
    blocks = as_json_array(content)
    if blocks is None:
        return []
    out: list[str] = []
    for raw_block in blocks:
        block = as_json_object(raw_block)
        if block is not None and block.get("type") == "text":
            text = block.get("text")
            if isinstance(text, str):
                out.append(text)
    return out


class ClaudeContextProvider(ContextProvider):
    """Collect and materialize Claude Code's project context and plans."""

    name: ClassVar[str] = "claude"
    layout: ClassVar[PlatformLayout] = CLAUDE_LAYOUT
    supports_memory: ClassVar[bool] = True

    def _claude_home(self) -> Path:
        """Resolve the Claude configuration home directory."""
        return Path.home() / ".claude"

    def _session_dir(self, abs_path: str) -> Path:
        """Resolve the session directory for a project path in Claude."""
        return self._claude_home() / "projects" / encode_project_dir(abs_path)

    def collect(self, *, scope: Scope, full: bool = False) -> ContextBundle:
        """Read Claude's project state into a neutral bundle."""
        root = workspace_root(scope)
        identity = project_identity(root)
        session_dir = self._session_dir(identity.abs_path)
        plans_dir = self._claude_home() / "plans"

        instructions = self._collect_instructions(root)
        memory = self._collect_memory(session_dir)
        facts, plan_paths, todo_items = self._scan_transcripts(session_dir, identity.abs_path)
        plans = self._collect_plans(plan_paths, plans_dir)
        todos = self._collect_todos(session_dir)

        facts.project_path = identity.abs_path
        facts.branch = identity.branch
        facts.plans = [plan.name for plan in plans]
        facts.todos = todo_items

        sessions: list[SessionRecord] = []
        if full:
            sessions = self._collect_sessions(session_dir)

        return ContextBundle(
            platform=self.name,
            project=identity,
            instructions=instructions,
            plans=plans,
            memory=memory,
            todos=todos,
            sessions=sessions,
            summary=HandoffDigest(text=render_digest(facts)),
        )

    def _collect_memory(self, session_dir: Path) -> list[MemoryDoc]:
        """Collect Claude's auto-memory files from the project's ``memory`` directory."""
        memory_dir = session_dir / "memory"
        docs: list[MemoryDoc] = []
        for path in sorted(memory_dir.rglob("*.md")):
            docs.append(
                MemoryDoc(
                    relative_path=str(path.relative_to(memory_dir)), content=read_lenient(path)
                )
            )
        return docs

    def _collect_plans(self, plan_paths: set[str], plans_dir: Path) -> list[Plan]:
        """Collect plan files under ``plans_dir`` referenced by the session transcripts."""
        plans: list[Plan] = []
        for raw in sorted(plan_paths):
            path = Path(raw)
            if path.parent == plans_dir and path.is_file():
                plans.append(
                    Plan(name=path.stem, content=read_lenient(path), origin_path=str(path))
                )
        return plans

    def _collect_todos(self, session_dir: Path) -> list[TodoList]:
        """Collect the todo lists belonging to this project's sessions."""
        todos_dir = self._claude_home() / "todos"
        if not todos_dir.is_dir():
            return []
        session_ids = {path.stem for path in session_dir.glob("*.jsonl")}
        todos: list[TodoList] = []
        for path in sorted(todos_dir.glob("*.json")):
            if any(sid in path.name for sid in session_ids):
                todos.append(TodoList(name=path.name, content=read_lenient(path)))
        return todos

    def _collect_sessions(self, session_dir: Path) -> list[SessionRecord]:
        """Collect raw session transcripts, skipping the ``memory`` subtree."""
        if not session_dir.is_dir():
            return []
        records: list[SessionRecord] = []
        for path in sorted(session_dir.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(session_dir)
            if rel.parts and rel.parts[0] == "memory":
                continue
            records.append(SessionRecord(relative_path=str(rel), content=read_lenient(path)))
        return records

    def _scan_transcripts(
        self, session_dir: Path, abs_path: str
    ) -> tuple[DigestFacts, set[str], list[str]]:
        """Scan Claude transcripts for a project."""
        state = _ScanState(
            facts=DigestFacts(),
            prefix=abs_path.rstrip("/") + "/",
            plans_marker=f"{Path.home()}/.claude/plans/",
        )
        if not session_dir.is_dir():
            return state.facts, state.plan_paths, state.todo_items

        for transcript in sorted(session_dir.rglob("*.jsonl")):
            # Subagent transcripts hold delegated prompts, not the user's own
            # goals: scan them for file activity but not for goal text.
            state.collect_goals = "subagents" not in transcript.parts
            for raw_line in read_lenient(transcript).splitlines():
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                self._scan_record(record, state)

        state.facts.edited_files = state.edited[:_MAX_EDITS]
        return state.facts, state.plan_paths, state.todo_items

    def _scan_record(self, record: dict[str, Any], state: _ScanState) -> None:
        """Process a single message record from a Claude transcript."""
        kind = record.get("type")
        message: dict[str, Any] = as_json_object(record.get("message")) or {}
        if kind == "user" and state.collect_goals:
            for text in _iter_text_blocks(message.get("content")):
                stripped = text.strip()
                if (
                    stripped
                    and not stripped.startswith("<")
                    and len(state.facts.user_goals) < _MAX_GOALS
                ):
                    state.facts.user_goals.append(stripped)
        elif kind == "assistant":
            blocks: list[Any] = as_json_array(message.get("content")) or []
            for raw_block in blocks:
                block = as_json_object(raw_block)
                if block is not None and block.get("type") == "tool_use":
                    self._scan_tool_use(block, state)

    def _scan_tool_use(self, block: dict[str, Any], state: _ScanState) -> None:
        """Process a tool use block from a Claude transcript."""
        tool = block.get("name")
        data: dict[str, Any] = as_json_object(block.get("input")) or {}
        file_path = data.get("file_path")
        if isinstance(file_path, str):
            if file_path.startswith(state.plans_marker):
                state.plan_paths.add(file_path)
            elif tool in _EDIT_TOOLS and file_path.startswith(state.prefix):
                rel = file_path[len(state.prefix) :]
                if rel not in state.seen_edits:
                    state.seen_edits.add(rel)
                    state.edited.append(rel)
        if tool == "TodoWrite":
            todos: list[Any] = as_json_array(data.get("todos")) or []
            for raw_item in todos:
                item = as_json_object(raw_item)
                content = item.get("content") if item is not None else None
                if isinstance(content, str):
                    state.todo_items.append(content)

    def materialize(self, bundle: ContextBundle, *, scope: Scope) -> list[Operation]:
        """Plan operations to write ``bundle`` onto this machine's Claude state."""
        root = workspace_root(scope)
        target_abs = str(root.resolve())
        target_session_dir = self._session_dir(target_abs)
        memory_dir = target_session_dir / "memory"
        plans_dir = self._claude_home() / "plans"
        todos_dir = self._claude_home() / "todos"
        source_abs = bundle.project.abs_path

        ops: list[Operation] = []
        for doc in bundle.instructions:
            ops.append(self._instruction_op(doc, root))
        for plan in bundle.plans:
            ops.append(
                FileOperation(
                    target=plans_dir / f"{plan.name}.md",
                    content=plan.content,
                    merge=MergeStrategy.OVERWRITE,
                    description=f"claude plan {plan.name}",
                )
            )
        for mem in bundle.memory:
            ops.append(
                FileOperation(
                    target=memory_dir / mem.relative_path,
                    content=mem.content,
                    merge=MergeStrategy.OVERWRITE,
                    description=f"claude memory {mem.relative_path}",
                )
            )
        for todo in bundle.todos:
            ops.append(
                FileOperation(
                    target=todos_dir / todo.name,
                    content=todo.content,
                    merge=MergeStrategy.OVERWRITE,
                    description=f"claude todo {todo.name}",
                )
            )
        for session in bundle.sessions:
            content = self._rewrite_session(session.content, source_abs, target_abs)
            ops.append(
                FileOperation(
                    target=target_session_dir / session.relative_path,
                    content=content,
                    merge=MergeStrategy.OVERWRITE,
                    description=f"claude session {session.relative_path}",
                )
            )
        return ops
