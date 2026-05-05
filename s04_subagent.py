#!/usr/bin/env python3
# Harness: context isolation -- protecting the model's clarity of thought.
"""
## 引言：
想象你问你的智能体 “这个项目使用什么测试框架？”。为了回答这个问题，它会读取五个文件，解析配置块，并对比导入语句。所有这些探索在当下都是有用的 —— 但一旦答案是 “pytest”，你就绝对不希望这五个文件的内容一直留在对话中。此后的每一次 API 调用都会带着这些无用信息，消耗 token，还会干扰模型。你需要一种能在独立环境中提出附带问题，且只带回答案的方法。

## 问题：
随着智能体运行，其messages数组会不断增长。每读取一个文件、每输出一次 bash 命令的结果，都会永久保留在上下文里。像 “这是什么测试框架？” 这样简单的问题，可能需要读取五个文件，但父节点只需返回一个词：“pytest”。如果没有隔离机制，这些中间产物会在整个会话期间一直留在上下文中，在后续的每一次 API 调用中都浪费令牌，还会干扰模型的注意力。会话运行的时间越长，问题就越严重 —— 上下文会被与当前任务无关的探索冗余信息填满。

## 解决方案
父智能体将子任务委派给子智能体，子智能体以空的messages=[]开始执行。子智能体负责完成所有复杂的探索工作，之后只有其最终的文本摘要会返回。子智能体的完整历史记录会被丢弃。

s04_subagent.py - Subagents
Spawn a child agent with fresh messages=[]. The child works in its own
context, sharing the filesystem, then returns only a summary to the parent.

s04_subagent.py - 子智能体（Subagent）
使用 fresh messages=[] 创建一个子智能体。子智能体在自己的上下文中工作，
共享文件系统，然后只返回一个摘要到父智能体。
    Parent agent                     Subagent
    +------------------+             +------------------+
    | messages=[...]   |             | messages=[]      |  <-- fresh
    |                  |  dispatch   |                  |
    | tool: task       | ---------->| while tool_use:  |
    |   prompt="..."   |            |   call tools     |
    |   description="" |            |   append results |
    |                  |  summary   |                  |
    |   result = "..." | <--------- | return last text |
    +------------------+             +------------------+
              |
    Parent context stays clean.
    Subagent context is discarded.
Key insight: "Fresh messages=[] gives context isolation. The parent stays clean."
Note: Real Claude Code also uses in-process isolation (not OS-level process
forking). The child runs in the same process with a fresh message array and
isolated tool context -- same pattern as this teaching implementation.

关键洞察："新鲜的 messages=[] 提供了上下文隔离。父级保持干净。"
注意：真实的 Claude Code 也使用进程内隔离（而不是 OS 级进程 fork）。
子进程在同一个进程中运行，使用 fresh messages=[] 消息数组和隔离的工具上下文 -- 与这个教学实现的模式相同。

    与真实的 Claude 代码的比较：
    Comparison with real Claude Code:
    +-------------------+------------------+----------------------------------+
    | Aspect            | This demo        | Real Claude Code                 |
    +-------------------+------------------+----------------------------------+
    | Backend           | in-process only  | 5 backends: in-process, tmux,    |
    |                   |                  | iTerm2, fork, remote             |
    | Context isolation | fresh messages=[]| createSubagentContext() isolates  |
    |                   |                  | ~20 fields (tools, permissions,  |
    |                   |                  | cwd, env, hooks, etc.)           |
    | Tool filtering    | manually curated | resolveAgentTools() filters from |
    |                   |                  | parent pool; allowedTools         |
    |                   |                  | replaces all allow rules         |
    | Agent definition  | hardcoded system | .claude/agents/*.md with YAML    |
    |                   | prompt           | frontmatter (AgentTemplate)      |
    +-------------------+------------------+----------------------------------+

每个 Sub-agent 都有一个全选的上下文。
！！！sub-agent 本质上是一种上下文边界，而非流程技巧。
"""
import os
import re
import subprocess
from pathlib import Path
from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv(override=True)
if os.getenv("ANTHROPIC_BASE_URL"):
    os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)
WORKDIR = Path.cwd()
client = Anthropic(base_url=os.getenv("ANTHROPIC_BASE_URL"))
MODEL = os.environ["MODEL_ID"]

# 系统提示词：
# 你是一个编码智能体，位于 {WORKDIR}。使用 task 工具来委托探索或子任务。
SYSTEM = f"You are a coding agent at {WORKDIR}. Use the task tool to delegate exploration or subtasks."

# 子智能体系统提示词：
# 你是一个编码子智能体，位于 {WORKDIR}。完成给定的任务，然后总结你的发现。
SUBAGENT_SYSTEM = f"You are a coding subagent at {WORKDIR}. Complete the given task, then summarize your findings."


class AgentTemplate:
    """
    Parse agent definition from markdown frontmatter.
    Real Claude Code loads agent definitions from .claude/agents/*.md.
    Frontmatter fields: name, tools, disallowedTools, skills, hooks,
    model, effort, permissionMode, maxTurns, memory, isolation, color,
    background, initialPrompt, mcpServers.
    3 sources: built-in, custom (.claude/agents/), plugin-provided.

    从 markdown 的 frontmatter 中解析 Agent 定义。
    Real Claude Code 从 .claude/agents/*.md 中加载 Agent 定义。
    Frontmatter name, tools, disallowedTools, skills, hooks,
    model, effort, permissionMode, maxTurns, memory, isolation, color,
    background, initialPrompt, mcpServers
    3 个来源：内置、自定义 (.claude/agents/)、插件提供。
    """

    def __init__(self, path):
        self.path = Path(path)
        self.name = self.path.stem
        self.config = {}
        self.system_prompt = ""
        self._parse()

    # 这个 _parse 方法用于从指定路径的 markdown 文件中解析 agent 的定义。
    # 它会读取文件内容，优先按 frontmatter 格式（--- 包裹的键值对）
    # 提取配置和 system prompt，
    # 否则将整个文件作为 prompt。
    # 主要作用是初始化 config 字典、system_prompt 和 name 属性。
    def _parse(self):
        text = self.path.read_text()
        match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)", text, re.DOTALL)
        if not match:
            self.system_prompt = text
            return
        for line in match.group(1).splitlines():
            if ":" in line:
                k, _, v = line.partition(":")
                self.config[k.strip()] = v.strip()
        self.system_prompt = match.group(2).strip()
        self.name = self.config.get("name", self.name)


# -- Tool implementations shared by parent and child --
# -- 共享的工具实现：父智能体和子智能体都可以使用。--

# 确保路径在工作目录内。
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
    except (FileNotFoundError, OSError) as e:
        return f"Error: {e}"


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
        content = fp.read_text()
        if old_text not in content:
            return f"Error: Text not found in {path}"
        fp.write_text(content.replace(old_text, new_text, 1))
        return f"Edited {path}"
    except Exception as e:
        return f"Error: {e}"

# 主 Agent 的 tool -> handler 映射。
TOOL_HANDLERS = {
    "bash": lambda **kw: run_bash(kw["command"]),
    "read_file": lambda **kw: run_read(kw["path"], kw.get("limit")),
    "write_file": lambda **kw: run_write(kw["path"], kw["content"]),
    "edit_file": lambda **kw: run_edit(kw["path"], kw["old_text"], kw["new_text"]),
}
# Child gets all base tools except task (no recursive spawning)
# 子智能体获取所有基础工具，除了 task（避免递归调用）。
# 后面会有设定父级的 TOOLS = CHILD_TOOLS + task 工具
# 这可防止递归生成 —— 子级无法创建自身的子级（这是我们在本例中期望的）
CHILD_TOOLS = [
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
]


# -- Subagent: fresh context, filtered tools, summary-only return --
# -- 子智能体：新鲜的上下文，过滤后的工具，只返回摘要。--

# 运行子智能体。
# 创建一个新鲜的上下文，并设置一个安全限制。
def run_subagent(prompt: str) -> str:
    sub_messages = [{"role": "user", "content": prompt}]  # fresh context
    for _ in range(30):  # safety limit
        response = client.messages.create(
            model=MODEL,
            system=SUBAGENT_SYSTEM,
            messages=sub_messages,
            tools=CHILD_TOOLS,
            max_tokens=8000,
        )
        sub_messages.append({"role": "assistant", "content": response.content})
        if response.stop_reason != "tool_use":
            break
        results = []
        for block in response.content:
            if block.type == "tool_use":
                handler = TOOL_HANDLERS.get(block.name)
                output = (
                    handler(**block.input) if handler else f"Unknown tool: {block.name}"
                )
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": str(output)[:50000],
                    }
                )
        sub_messages.append({"role": "user", "content": results})
    # Only the final text returns to the parent -- child context is discarded
    return (
        "".join(b.text for b in response.content if hasattr(b, "text"))
        or "(no summary)"
    )


# -- Parent tools: base tools + task dispatcher --
# -- 主智能体工具：基础工具 + task 调度器 --
PARENT_TOOLS = CHILD_TOOLS + [
    {
        "name": "task",
        "description": "Spawn a subagent with fresh context. It shares the filesystem but not conversation history.",
        "input_schema": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string"},
                "description": {
                    "type": "string",
                    "description": "Short description of the task",
                },
            },
            "required": ["prompt"],
        },
    },
]


def agent_loop(messages: list):
    while True:
        response = client.messages.create(
            model=MODEL,
            system=SYSTEM,
            messages=messages,
            tools=PARENT_TOOLS,
            max_tokens=8000,
        )
        messages.append({"role": "assistant", "content": response.content})
        if response.stop_reason != "tool_use":
            return
        results = []
        for block in response.content:
            if block.type == "tool_use":
                if block.name == "task":
                    desc = block.input.get("description", "subtask")
                    prompt = block.input.get("prompt", "")
                    print(f"> task ({desc}): {prompt[:80]}")
                    output = run_subagent(prompt)
                else:
                    handler = TOOL_HANDLERS.get(block.name)
                    output = (
                        handler(**block.input)
                        if handler
                        else f"Unknown tool: {block.name}"
                    )
                print(f"  {str(output)[:200]}")
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": str(output),
                    }
                )
        messages.append({"role": "user", "content": results})

# 用户 Query 示例：
# - 使用子任务来找出该项目使用的测试框架
# - 委托：读取所有 .py 文件并总结每个文件的功能
# - 使用任务创建新模块，然后在此处进行验证
if __name__ == "__main__":
    history = []
    while True:
        try:
            query = input("\033[36ms04 >> \033[0m")
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
