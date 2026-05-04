#!/usr/bin/env python3
# Harness: planning -- keep the current session plan outside the model's head.
"""
s03_todo_write.py - Session Planning with TodoWrite
This chapter is about a lightweight session plan, not a durable task graph.
The model can rewrite its current plan, keep one active step in focus, and get
nudged if it stops refreshing the plan for too many rounds.

s03_todo_write.py - 会话计划（Session Planing）与 TodoWrite
这个章节是关于一个轻量级的会话计划 (session plan)，而不是一个持久化的任务图 
(gukt: 是临时的，而不是持久化保存的 tasks， 本次 Session 结束 plan 也就丢弃了)。
模型可以重写（更新）它的当前计划，始终保持聚焦在某个活跃的任务上，
如果连续多轮停止更新计划，就会收到提醒。

本课目标：
当任务变得复杂时，清晰的计划能让智能体保持在正轨上。
(gukt: 也就是说，对于复杂的任务，先列计划，然后逐个技术执行，并更新计划状态，这样会让 Agent 更能稳定地处理好该复杂任务)

核心要点：
循环不应关心工具的内部工作原理。它只需要一个从工具名称到处理程序的可靠路由。
（gukt: 核心的循环仍然不用动，动的事内部的工具映射中加入一个 TodoManager 工具，用以列计划并更新计划）

我们将会学到：
- How session planning keeps the model on track during multi-step tasks
会话规划如何让模型在多步骤任务中保持正轨

- How a structured todo list with status tracking replaces fragile free-form plans
带状态追踪的结构化待办清单如何替代脆弱的自由形式计划

- How gentle reminders (nag injection) pull the model back when it drifts
当模型偏离方向时，温和的提醒（提醒式注入）如何将其拉回正轨
"""
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv(override=True)
if os.getenv("ANTHROPIC_BASE_URL"):
    os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)
WORKDIR = Path.cwd()
client = Anthropic(base_url=os.getenv("ANTHROPIC_BASE_URL"))
MODEL = os.environ["MODEL_ID"]

# 计划提醒间隔：如果连续多轮停止更新计划，就会收到提醒。
PLAN_REMINDER_INTERVAL = 3

# 系统提示词：
# 你是一个编码代理，位于 {WORKDIR}。
# 使用 todo 工具进行多步骤工作。
# 在有多个步骤的任务中，保持 exactly one step in_progress（严格保证只有一个步骤处于进行中）。
# （gukt: 意思是，当有多步骤任务时，严格保证每次只执行一个任务）
# 随着工作进展，刷新计划。优先使用工具，而不是 prose（自由文本描述）。
# （gukt: 使用自有文本指令控制 Agent 更新 plan 不太稳定和靠谱，应该使用工具来更新 plan 中的任务状态）
SYSTEM = f"""You are a coding agent at {WORKDIR}.
Use the todo tool for multi-step work.
Keep exactly one step in_progress when a task has multiple steps.
Refresh the plan as work advances. Prefer tools over prose."""

# 计划条目（PlanItem）类型：内容、状态、活跃形式（active_form）
@dataclass
class PlanItem:
    content: str
    status: str = "pending"
    active_form: str = ""

# 计划状态（PlanningState）类型：计划条目列表、连续未更新轮数
@dataclass
class PlanningState:
    items: list[PlanItem] = field(default_factory=list)
    # 连续未更新轮数
    # （gukt: 如果连续多轮停止更新计划，就会收到提醒。）
    rounds_since_update: int = 0

# 待办事项管理器（TodoManager）类：管理计划状态和更新计划
class TodoManager:
    # 初始化计划状态
    def __init__(self):
        # 该 TodoManager 实例的内部状态，包括计划条目列表和连续未更新轮数
        self.state = PlanningState()

    # 更新计划：接收计划条目列表，并更新计划状态。
    def update(self, items: list) -> str:
        # 如果计划条目列表超过 12 个，则抛出错误。
        # 告诉 LLM，单个 session 里的计划任务最大不能超过 12 条
        if len(items) > 12:
            raise ValueError("Keep the session plan short (max 12 items)")
        normalized = []
        # 正在进行的任务数量
        in_progress_count = 0
        # 遍历计划条目列表，并更新计划状态。
        for index, raw_item in enumerate(items):
            # 获取任务内容
            content = str(raw_item.get("content", "")).strip()
            # 获取任务状态
            status = str(raw_item.get("status", "pending")).lower()
            # 获取任务活跃形式
            active_form = str(raw_item.get("activeForm", "")).strip()
            # 如果任务内容为空，则抛出错误。
            if not content:
                # 抛出异常，告诉 LLM，任务内容不能为空
                raise ValueError(f"Item {index}: content required")
            if status not in {"pending", "in_progress", "completed"}:
                # 如果任务状态不是 pending、in_progress 或 completed，则抛出错误。
                raise ValueError(f"Item {index}: invalid status '{status}'")
            # 如果任务状态为 in_progress，则增加正在进行的任务数量。
            if status == "in_progress":
                in_progress_count += 1
            # 往归一化的 PlanItem 列表中添 PlanItem 对象。
            normalized.append(
                PlanItem(
                    content=content,
                    status=status,
                    active_form=active_form,
                )
            )
        # 由于我们不希望同时进行多个任务，所以如果正在进行的任务数量大于 1，则抛出错误。
        if in_progress_count > 1:
            raise ValueError("Only one plan item can be in_progress")

        # PlanItem 列表归一化处理完成，将其设置到 TodoManager 的 内部状态变量 state 中。
        self.state.items = normalized
        self.state.rounds_since_update = 0
        # 渲染计划，并返回计划字符串。
        return self.render()

    # 记录没有更新计划状态的次数
    def note_round_without_update(self) -> None:
        self.state.rounds_since_update += 1

    def reminder(self) -> str | None:
        # 如果计划条目列表为空，则返回 None。
        if not self.state.items:
            return None
        # 如果连续未更新轮数小于计划提醒间隔，则返回 None。
        if self.state.rounds_since_update < PLAN_REMINDER_INTERVAL:
            # 返回 None，表示不需要提醒。
            return None
        # 返回提醒文本，表示需要刷新当前计划。
        return "<reminder>Refresh your current plan before continuing.</reminder>"

    # 渲染计划
    # 实际上就是将计划条目列表渲染为多行文本的字符串。
    def render(self) -> str:
        # 计划的条目不能为空，否则抛出异常文本 "No session plan yet."
        if not self.state.items:
            return "No session plan yet."
        lines = []
        # 遍历计划条目列表，并渲染计划。
        for item in self.state.items:
            # 根据任务状态，渲染任务的标记。就是前缀显示的任务标记
            marker = {
                "pending": "[ ]",
                "in_progress": "[>]",
                "completed": "[x]",
            }[item.status]
            # 任务行文本内容：任务标记 + 任务内容（文本描述）
            line = f"{marker} {item.content}"
            # 如果任务状态为 in_progress，并且有活跃形式，则添加 active_form 到任务行文本内容的后面。
            # NOTE：active_form 是可选的
            if item.status == "in_progress" and item.active_form:
                line += f" ({item.active_form})"
            # 添加到 lines 列表，供后续返回
            lines.append(line)
        # 统计完成的任务数量，并添加到 lines 列表的末尾。
        completed = sum(1 for item in self.state.items if item.status == "completed")
        # 汇总行文本：显示 n 个 tasks 已经完成的状态文本
        lines.append(f"\n({completed}/{len(self.state.items)} completed)")
        # 拼接成一个多行字符串，并返回。
        return "\n".join(lines)


TODO = TodoManager()


def safe_path(path_str: str) -> Path:
    path = (WORKDIR / path_str).resolve()
    if not path.is_relative_to(WORKDIR):
        raise ValueError(f"Path escapes workspace: {path_str}")
    return path


def run_bash(command: str) -> str:
    dangerous = ["rm -rf /", "sudo", "shutdown", "reboot", "> /dev/"]
    if any(item in command for item in dangerous):
        return "Error: Dangerous command blocked"
    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=WORKDIR,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        return "Error: Timeout (120s)"
    output = (result.stdout + result.stderr).strip()
    return output[:50000] if output else "(no output)"


def run_read(path: str, limit: int | None = None) -> str:
    try:
        lines = safe_path(path).read_text().splitlines()
        if limit and limit < len(lines):
            lines = lines[:limit] + [f"... ({len(lines) - limit} more lines)"]
        return "\n".join(lines)[:50000]
    except Exception as exc:
        return f"Error: {exc}"


def run_write(path: str, content: str) -> str:
    try:
        file_path = safe_path(path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content)
        return f"Wrote {len(content)} bytes to {path}"
    except Exception as exc:
        return f"Error: {exc}"


def run_edit(path: str, old_text: str, new_text: str) -> str:
    try:
        file_path = safe_path(path)
        content = file_path.read_text()
        if old_text not in content:
            return f"Error: Text not found in {path}"
        file_path.write_text(content.replace(old_text, new_text, 1))
        return f"Edited {path}"
    except Exception as exc:
        return f"Error: {exc}"

# 和前面的示例惟一的不同就是多添加了 todo 工具与处理程序的映射条目。
TOOL_HANDLERS = {
    "bash": lambda **kw: run_bash(kw["command"]),
    "read_file": lambda **kw: run_read(kw["path"], kw.get("limit")),
    "write_file": lambda **kw: run_write(kw["path"], kw["content"]),
    "edit_file": lambda **kw: run_edit(kw["path"], kw["old_text"], kw["new_text"]),
    # 新增一个 todo 工具，用于多步骤工作。
    # 该工具既用于创建 plan，又用于更新 plan 中的某条 plan item 的状态。
    "todo": lambda **kw: TODO.update(kw["items"]),
}
TOOLS = [
    {
        "name": "bash",
        "description": "Run a shell command.",
        "input_schema": {
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
        },
    },
    {
        "name": "read_file",
        "description": "Read file contents.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "limit": {"type": "integer"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": "Write content to a file.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "edit_file",
        "description": "Replace exact text in a file once.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "old_text": {"type": "string"},
                "new_text": {"type": "string"},
            },
            "required": ["path", "old_text", "new_text"],
        },
    },
    {
        "name": "todo",
        "description": "Rewrite the current session plan for multi-step work.",
        "input_schema": {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "content": {"type": "string"},
                            "status": {
                                "type": "string",
                                "enum": ["pending", "in_progress", "completed"],
                            },
                            "activeForm": {
                                "type": "string",
                                "description": "Optional present-continuous label.",
                            },
                        },
                        "required": ["content", "status"],
                    },
                },
            },
            "required": ["items"],
        },
    },
]

# 提取文本，用于提取模型回复中的文本。返回提取的文本。
def extract_text(content) -> str:
    # 如果 content 不是列表，则返回空字符串。
    if not isinstance(content, list):
        return ""
    texts = []
    # 如果 content 是列表，则提取列表中的文本，并添加到 texts 列表中。
    for block in content:
        text = getattr(block, "text", None)
        if text:
            texts.append(text)
    return "\n".join(texts).strip()


def agent_loop(messages: list) -> None:
    while True:
        response = client.messages.create(
            model=MODEL,
            system=SYSTEM,
            messages=messages,
            tools=TOOLS,
            max_tokens=8000,
        )
        messages.append({"role": "assistant", "content": response.content})
        if response.stop_reason != "tool_use":
            return
        results = []
        # 状态标记，用于记录是否使用了 todo 工具。
        used_todo = False
        for block in response.content:
            if block.type != "tool_use":
                continue
            handler = TOOL_HANDLERS.get(block.name)
            try:
                output = (
                    handler(**block.input) if handler else f"Unknown tool: {block.name}"
                )
            except Exception as exc:
                output = f"Error: {exc}"
            # 打印 block（任何 block 哦） 名称和输出结果的前 200 个字符。
            print(f"> {block.name}: {str(output)[:200]}")
            results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": str(output),
                }
            )
            # 如果 block 名称是 todo，则设置状态标记为 True。
            # 表明本 Session 使用到了 todo 工具。
            if block.name == "todo":
                used_todo = True
        # 一旦发现使用了 todo 工具，则重置连续未更新轮数为 0。
        # 否则，则增加连续未更新轮数。
        if used_todo:
            TODO.state.rounds_since_update = 0
        else:
            # 记录没有更新计划状态的次数
            TODO.note_round_without_update()
            # 获取提醒文本
            reminder = TODO.reminder()
            if reminder:
                results.insert(0, {"type": "text", "text": reminder})
        messages.append({"role": "user", "content": results})

# 用户 Query 示例：
# - 重构文件 hello.py：添加类型提示、文档字符串和主程序保护块
# - 创建一个包含 `__init__.py`、`utils.py` 和 `tests/test_utils.py` 的 Python 包
# - 检查所有 Python 文件并修复所有风格问题
if __name__ == "__main__":
    history = []
    while True:
        try:
            query = input("\033[36ms03 >> \033[0m")
        except (EOFError, KeyboardInterrupt):
            break
        if query.strip().lower() in ("q", "exit", ""):
            break
        history.append({"role": "user", "content": query})
        agent_loop(history)
        final_text = extract_text(history[-1]["content"])
        if final_text:
            print(final_text)
        print()
