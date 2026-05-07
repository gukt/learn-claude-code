#!/usr/bin/env python3
# Harness: persistent tasks -- goals that outlive any single conversation.
"""
# 任务系统
https://learn.shareai.run/en/s12/
持久化工作图
待办清单助力单个会话；持久任务图则可协调超出会话生命周期的工作。

## 你将学到
- 如何将扁平清单升级为带有显式依赖关系的任务图
- blockedBy 和 blocks 边如何表示顺序和并行性
- 状态转换（pending -> in_progress -> completed）如何驱动自动解除阻塞
- 将任务持久化到磁盘如何使它们在压缩和重新启动中幸存下来

在第三季中，你为智能体添加了一个待办事项写入工具 —— 这是一个简单的清单，用于追踪已完成和未完成的事项。这种方式对于单次专注的会话来说效果不错。但实际工作是有结构的。任务 B 依赖于任务 A，任务 C 和 D 可以并行执行，任务 E 则需等待 C 和 D 都完成。简单的清单无法表达任何这类关系。而且由于该清单仅存储在内存中，上下文压缩（第六季）会将其完全清除。在本章中，你将用一个合适的任务图替换这个清单，它能理解依赖关系、持久化到磁盘，并将成为后续所有操作的协调核心。

## 问题
想象你让你的智能体重构一个代码库：解析抽象语法树（AST）、转换节点、生成新代码并运行测试。解析步骤必须在转换和生成开始前完成。转换和生成可以并行运行。测试必须等待前两者全部完成。借助 s03 版本的扁平待办事项写入功能，智能体无法表达这些关系。它可能会在解析完成前尝试转换，或者在任何操作准备就绪前就运行测试。该功能既没有顺序控制，也没有依赖跟踪，除了 “完成或未完成” 外也没有其他状态标识。更糟糕的是，如果上下文窗口溢出并触发压缩，整个计划就会消失。

## 解决方案
将清单转换为持久化到磁盘的任务图。每个任务都是一个包含状态、依赖关系（blockedBy）和被依赖关系（blocks）的 JSON 文件。该图可在任意时刻回答三个问题：哪些任务已就绪、哪些任务被阻塞、哪些任务已完成。

上述结构是一个有向无环图（DAG），即有向无环图，意味着任务向前推进且永远不会循环。该任务图将成为后续章节的协调核心：后台执行（第 13 章）、智能体团队（第 15 章及以后）以及工作树隔离（第 18 章）均基于相同的持久任务结构构建。

## 工作原理
步骤 1。 创建一个TaskManager，该管理器为每个任务存储一个 JSON 文件，并支持增删改查操作与依赖关系图。
步骤 2。实现依赖项解析。当一个任务完成时，从其他所有任务的blockedBy列表中清除其 ID，自动解除依赖项的阻塞状态。
步骤 3。在update方法中连接状态转换和依赖项边。当任务的状态更改为completed时，步骤 2 中的依赖项清除逻辑将自动触发。
步骤 4。在调度映射中注册四个任务工具，让智能体完全控制任务的创建、更新、列出和检查。

s12_task_system.py - Tasks
Tasks persist as JSON files in .tasks/ so they survive context compression.
Each task carries a small dependency graph:
- blockedBy: what must finish first
- blocks: what this task unlocks later
    .tasks/
      task_1.json  {"id":1, "subject":"...", "status":"completed", ...}
      task_2.json  {"id":2, "blockedBy":[1], "status":"pending", ...}
      task_3.json  {"id":3, "blockedBy":[2], "blocks":[], ...}
    Dependency resolution:
    +----------+     +----------+     +----------+
    | task 1   | --> | task 2   | --> | task 3   |
    | complete |     | blocked  |     | blocked  |
    +----------+     +----------+     +----------+
         |                ^
         +--- completing task 1 removes it from task 2's blockedBy
Key idea: task state survives compression because it lives on disk, not only
inside the conversation.
These are durable work-graph tasks, not transient runtime execution slots.
  Read this file in this order:
1. TaskManager: what a TaskRecord looks like on disk.
2. TOOL_HANDLERS / TOOLS: how task operations enter the same loop as normal tools.
3. agent_loop: how persistent work state is exposed back to the model.
Most common confusion:
- a task record is a durable work item
- it is not a thread, background slot, or worker process
Teaching boundary:
this chapter teaches the durable work graph first.
Runtime execution slots and schedulers arrive later.

s12_task_system.py - 任务
任务以 JSON 文件形式持久化存储在.tasks/ 目录下，从而能够在上下文压缩后保留不丢失。
每个任务都携带一个小型依赖图：
- blockedBy：哪些任务必须先完成
- blocks：哪些任务在此任务完成后解锁
依赖解析：
+----------+     +----------+     +----------+
| task 1   | --> | task 2   | --> | task 3   |
| complete |     | blocked  |     | blocked  |
+----------+     +----------+     +----------+
          |                ^
          +--- completing task 1 removes it from task 2's blockedBy
关键思想：任务状态在压缩后幸存，因为它存储在磁盘上，而不仅仅存在于对话中。
这些是持久型工作图任务，而非临时的运行时执行槽。

阅读此文件的顺序：
1. TaskManager：磁盘上的 TaskRecord 是什么样子的。
2. TOOL_HANDLERS / TOOLS：如何让任务操作进入与普通工具相同的循环。
3. agent_loop：持久化工作状态如何回传给模型。

最常见的混淆：
- 任务记录 (TaskRecord) 是一个持久的工作项
- 它不是一个线程、后台槽 (background slot) 或工作进程 (worker process)  
教学边界：
本章首先教授持久工作图。运行时执行槽 (runtime execution slot) 和调度器 (scheduler) 稍后到达。
"""
import json
import os
import subprocess
from pathlib import Path
from anthropic import Anthropic
from dotenv import load_dotenv

# 首先加载 .env 文件中的环境变量，并用 .env 中的值覆盖已存在的同名环境变量。
load_dotenv(override=True)
#
# 如果 ANTHROPIC_BASE_URL 存在，则删除 ANTHROPIC_AUTH_TOKEN 环境变量。
# 这样做是为了避免在自定义 Anthropic 服务（即设置了 ANTHROPIC_BASE_URL）时，
# 依然使用默认的 API Token（ANTHROPIC_AUTH_TOKEN）。
# 有些自定义服务可能不需要或不兼容官方的 Token，提前移除可以防止认证冲突或异常。
if os.getenv("ANTHROPIC_BASE_URL"):
    os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)
WORKDIR = Path.cwd()
client = Anthropic(base_url=os.getenv("ANTHROPIC_BASE_URL"))
# 使用的模型 ID
MODEL = os.environ["MODEL_ID"]
# 任务目录
TASKS_DIR = WORKDIR / ".tasks"

# 系统提示词：
# 你是一个编码智能体，位于 {WORKDIR}。使用任务工具来计划和追踪工作。
SYSTEM = f"You are a coding agent at {WORKDIR}. Use task tools to plan and track work."


# -- TaskManager: CRUD for a persistent task graph --
# -- 任务管理器：持久化任务图的 CRUD --
class TaskManager:
    """Persistent TaskRecord store.
    Think "work graph on disk", not "currently running worker".
    
    持久化任务记录存储
    不要把任务记录当成当前正在运行的工作线程。
    """
    def __init__(self, tasks_dir: Path):
        self.dir = tasks_dir
        self.dir.mkdir(exist_ok=True)
        self._next_id = self._max_id() + 1

    def _max_id(self) -> int:
        # 作用是从任务目录中查找所有以 "task_" 开头、".json" 结尾的文件，提取文件名中的任务ID（数字），并组成一个整数列表。
        # 这样可以获取已有所有任务的ID，用于后续计算最大ID等操作
        ids = [int(f.stem.split("_")[1]) for f in self.dir.glob("task_*.json")]
        # 从任务 ID 列表中获取最大的 ID，如果没有任务则返回 0
        return max(ids) if ids else 0

    # 先定义两个通用的私有方法：_load 和 _save
    # _load 方法用于从磁盘加载任务定义 JSON 文件，并返回一个字典
    # _save 方法用于将任务定义字典保存到磁盘，文件名为 "task_{task_id}.json"
    
    def _load(self, task_id: int) -> dict:
        # 根据任务 ID 构建文件路径，并检查文件是否存在
        path = self.dir / f"task_{task_id}.json"
        # 如果文件不存在，则抛出错误
        if not path.exists():
            raise ValueError(f"Task {task_id} not found")
        # 读取文件内容（任务定义 JSON 文件），并返回一个字典
        return json.loads(path.read_text())

    def _save(self, task: dict):
        path = self.dir / f"task_{task['id']}.json"
        path.write_text(json.dumps(task, indent=2))
        
    # 然后定义四个公共 CRUD 方法：create、get、update、list_all

    def create(self, subject: str, description: str = "") -> str:
        task = {
            "id": self._next_id,
            "subject": subject,
            "description": description,
            "status": "pending",
            "blockedBy": [],
            "blocks": [],
            "owner": "",
        }
        self._save(task)
        self._next_id += 1
        return json.dumps(task, indent=2)

    def get(self, task_id: int) -> str:
        return json.dumps(self._load(task_id), indent=2)

    # update 方法用于更新任务的状态、所有者或依赖关系
    def update(
        self,
        task_id: int,
        status: str = None,
        owner: str = None,
        add_blocked_by: list = None,
        add_blocks: list = None,
    ) -> str:
        # 首先根据任务 ID 加载任务（字典）
        task = self._load(task_id)
        # 如果 owner 不为空，则更新 TaskRecord 的 Owner
        if owner is not None:
            task["owner"] = owner
        
        # 如果提供了任务的新状态
        # - 如果新状态不合法，则抛出错误
        if status:
            if status not in ("pending", "in_progress", "completed", "deleted"):
                raise ValueError(f"Invalid status: {status}")
            # 使用新状态更新任务状态
            task["status"] = status
            # When a task is completed, remove it from all other tasks' blockedBy
            # 当任务的状态更改为 completed 时，从所有其他任务的 blockedBy 列表中删除该任务的 ID
            if status == "completed":
                self._clear_dependency(task_id)
        if add_blocked_by:
            task["blockedBy"] = list(set(task["blockedBy"] + add_blocked_by))
        if add_blocks:
            task["blocks"] = list(set(task["blocks"] + add_blocks))
            # Bidirectional: also update the blocked tasks' blockedBy lists
            for blocked_id in add_blocks:
                try:
                    blocked = self._load(blocked_id)
                    if task_id not in blocked["blockedBy"]:
                        blocked["blockedBy"].append(task_id)
                        self._save(blocked)
                except ValueError:
                    pass
        self._save(task)
        return json.dumps(task, indent=2)

    # _clear_dependency 方法用于清除任务的依赖关系
    # 参数 completed_id 是已完成的任务 ID
    def _clear_dependency(self, completed_id: int):
        # 从所有其他任务的 blockedBy 列表中删除 completed_id
        # 遍历任务目录中的所有的任务定义文件，加载每个任务的 JSON 定义，并检查其 blockedBy 列表是否包含 completed_id
        # 如果有，则将其他任务的 blockedBy 列表中删除 completed_id
        # （gukt: 因为任务完成，所有就将该 task_id 从其他任务的 blockedBy 列表中删除）
        """Remove completed_id from all other tasks' blockedBy lists."""
        for f in self.dir.glob("task_*.json"):
            task = json.loads(f.read_text())
            if completed_id in task.get("blockedBy", []):
                task["blockedBy"].remove(completed_id)
                self._save(task)

    # list_all 方法用于列出所有任务
    def list_all(self) -> str:
        tasks = []
        for f in sorted(self.dir.glob("task_*.json")):
            tasks.append(json.loads(f.read_text()))
        if not tasks:
            return "No tasks."
        lines = []
        for t in tasks:
            marker = {
                "pending": "[ ]",
                "in_progress": "[>]",
                "completed": "[x]",
                "deleted": "[-]",
            }.get(t["status"], "[?]")
            blocked = f" (blocked by: {t['blockedBy']})" if t.get("blockedBy") else ""
            owner = f" owner={t['owner']}" if t.get("owner") else ""
            lines.append(f"{marker} #{t['id']}: {t['subject']}{owner}{blocked}")
        return "\n".join(lines)


# 创建一个任务管理器实例
TASKS = TaskManager(TASKS_DIR)


# -- Base tool implementations --
def safe_path(p: str) -> Path:
    path = (WORKDIR / p).resolve()
    if not path.is_relative_to(WORKDIR):
        raise ValueError(f"Path escapes workspace: {p}")
    return path


def run_bash(command: str) -> str:
    dangerous = ["rm -rf /", "sudo", "shutdown", "reboot", "> /dev/"]
    if any(d in command for d in dangerous):
        return "Error: Dangerous command blocked"
    try:
        r = subprocess.run(
            command,
            shell=True,
            cwd=WORKDIR,
            capture_output=True,
            text=True,
            timeout=120,
        )
        out = (r.stdout + r.stderr).strip()
        return out[:50000] if out else "(no output)"
    except subprocess.TimeoutExpired:
        return "Error: Timeout (120s)"


def run_read(path: str, limit: int = None) -> str:
    try:
        lines = safe_path(path).read_text().splitlines()
        if limit and limit < len(lines):
            lines = lines[:limit] + [f"... ({len(lines) - limit} more)"]
        return "\n".join(lines)[:50000]
    except Exception as e:
        return f"Error: {e}"


def run_write(path: str, content: str) -> str:
    try:
        fp = safe_path(path)
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content)
        return f"Wrote {len(content)} bytes"
    except Exception as e:
        return f"Error: {e}"


def run_edit(path: str, old_text: str, new_text: str) -> str:
    try:
        fp = safe_path(path)
        c = fp.read_text()
        if old_text not in c:
            return f"Error: Text not found in {path}"
        fp.write_text(c.replace(old_text, new_text, 1))
        return f"Edited {path}"
    except Exception as e:
        return f"Error: {e}"


TOOL_HANDLERS = {
    "bash": lambda **kw: run_bash(kw["command"]),
    "read_file": lambda **kw: run_read(kw["path"], kw.get("limit")),
    "write_file": lambda **kw: run_write(kw["path"], kw["content"]),
    "edit_file": lambda **kw: run_edit(kw["path"], kw["old_text"], kw["new_text"]),
    "task_create": lambda **kw: TASKS.create(kw["subject"], kw.get("description", "")),
    "task_update": lambda **kw: TASKS.update(
        kw["task_id"],
        kw.get("status"),
        kw.get("owner"),
        kw.get("addBlockedBy"),
        kw.get("addBlocks"),
    ),
    "task_list": lambda **kw: TASKS.list_all(),
    "task_get": lambda **kw: TASKS.get(kw["task_id"]),
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
            "properties": {"path": {"type": "string"}, "limit": {"type": "integer"}},
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": "Write content to file.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
            "required": ["path", "content"],
        },
    },
    {
        "name": "edit_file",
        "description": "Replace exact text in file.",
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
        "name": "task_create",
        "description": "Create a new task.",
        "input_schema": {
            "type": "object",
            "properties": {
                "subject": {"type": "string"},
                "description": {"type": "string"},
            },
            "required": ["subject"],
        },
    },
    {
        "name": "task_update",
        "description": "Update a task's status, owner, or dependencies.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "integer"},
                "status": {
                    "type": "string",
                    "enum": ["pending", "in_progress", "completed", "deleted"],
                },
                "owner": {
                    "type": "string",
                    "description": "Set when a teammate claims the task",
                },
                "addBlockedBy": {"type": "array", "items": {"type": "integer"}},
                "addBlocks": {"type": "array", "items": {"type": "integer"}},
            },
            "required": ["task_id"],
        },
    },
    {
        "name": "task_list",
        "description": "List all tasks with status summary.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "task_get",
        "description": "Get full details of a task by ID.",
        "input_schema": {
            "type": "object",
            "properties": {"task_id": {"type": "integer"}},
            "required": ["task_id"],
        },
    },
]


def agent_loop(messages: list):
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
        for block in response.content:
            if block.type == "tool_use":
                handler = TOOL_HANDLERS.get(block.name)
                try:
                    output = (
                        handler(**block.input)
                        if handler
                        else f"Unknown tool: {block.name}"
                    )
                except Exception as e:
                    output = f"Error: {e}"
                print(f"> {block.name}: {str(output)[:200]}")
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": str(output),
                    }
                )
        messages.append({"role": "user", "content": results})


if __name__ == "__main__":
    history = []
    while True:
        try:
            query = input("\033[36ms12 >> \033[0m")
        except (EOFError, KeyboardInterrupt):
            break
        if query.strip().lower() in ("q", "exit", ""):
            break
        history.append({"role": "user", "content": query})
        agent_loop(history)
        response_content = history[-1]["content"]
        if isinstance(response_content, list):
            for block in response_content:
                if hasattr(block, "text"):
                    print(block.text)
        print()
