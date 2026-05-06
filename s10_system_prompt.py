#!/usr/bin/env python3
# Harness: assembly -- the system prompt is a pipeline, not a string.
"""
# 系统提示词
将输入组装为流水线
模型看到的是一个构建好的输入管道，而非一个庞大的静态字符串。

## 你将学到
- 如何从独立的模块而非单一硬编码字符串来构建系统提示词
- 稳定内容（角色、规则）与动态内容（日期、当前工作目录、逐轮提醒）之间的边界
- CLAUDE.md 文件如何分层添加指令且互不覆盖
- 为何必须通过提示词流水线重新注入记忆，才能真正引导智能体

## 引言
当你的智能体只有一个工具、一项任务时，一段硬编码的提示字符串就能正常工作。
但看看你的框架目前积累的所有内容：角色描述、工具定义、加载的技能、保存的记忆、CLAUDE.md 指令文件，还有每一轮的运行时上下文。
如果你把所有这些内容都塞进一个大字符串里，没人 —— 包括你自己 —— 能说清每一部分的来源、存在的原因，也无法安全地修改它。
解决办法是不再将提示视为一个整体，而是将其视为一个组装流水线。

## 问题
想象你想给你的智能体添加一个新工具。你打开系统提示词，滚动浏览角色段落、安全规则、三项技能描述以及记忆模块，然后在中间某个位置粘贴工具描述。
下周，其他人添加了一个 CLAUDE.md 加载器，并将其输出附加到同一个字符串中。
一个月后，这个提示词长达 6000 个字符，其中一半内容已经过时，而且没人记得每一轮需要修改哪些行，哪些行在整个会话中都应保持固定。
这并非一个假设场景 —— 这是每一个将提示词存储在单个变量中的智能体的必然发展轨迹。

## 解决方案
将提示词构建转化为流水线模式。每个环节仅有一个来源和一项职责。
构建器对象按固定顺序将它们组合起来，在保持稳定的部分与每次迭代都会变化的部分之间形成清晰界限。
1. core identity and rules
2. tool catalog
3. skills
4. memory
5. CLAUDE.md instruction chain
6. dynamic runtime context
然后进行组装：
core
+ tools
+ skills
+ memory
+ claude_md
+ dynamic_context
= final model input

## 工作原理
步骤 1. 定义构建器。 每个方法仅对应一个内容来源。
```python
class SystemPromptBuilder:
    def build(self) -> str:
        parts = []
        parts.append(self._build_core())
        parts.append(self._build_tools())
        parts.append(self._build_skills())
        parts.append(self._build_memory())
        parts.append(self._build_claude_md())
        parts.append(self._build_dynamic())
        return "\n\n".join(p for p in parts if p)
```
这就是本章的核心思想。每个 _build_* 方法都仅从一个数据源获取信息：_build_tools() 读取工具列表，_build_memory() 读取记忆库，以此类推。
如果你想知道提示词中的某一行源自何处，只需查看负责生成它的那一个方法即可。

第二步：将稳定内容与动态内容分离。 这是整个流程中最重要的边界。
稳定内容在整个会话期间很少或从不发生变化：

- 角色描述
- 工具契约（工具列表及其架构）
- 长期安全规则
- 项目指令链（CLAUDE.md 文件）

动态内容每一轮或每几轮就会发生变化：
- 当前日期
- 当前工作目录
- 当前模式（规划模式、代码模式等）
- 每轮的警告或提醒

将这些内容混合在一起，意味着模型会重新读取数千个未发生变化的稳定 tokens，而少数发生变化的 tokens 则被埋在中间某个位置。
一个实际的系统会用边界标记将它们分隔开，这样稳定的前缀就可以在不同轮次中被缓存，从而节省提示词 tokens。

第三步。分层编写 CLAUDE.md 说明。
CLAUDE.md 不同于记忆，也不同于技能。它是一种分层式说明源 —— 这意味着有多个文件参与其中，且后续层级是对先前层级进行补充，而非替换：

- 用户级指令文件（~/.claude/CLAUDE.md）
- 项目根目录指令文件（<project>/CLAUDE.md）
- 更深层级子目录下的指令文件

关键不在于文件名本身，而在于指令源可以分层叠加，而非被覆盖。

步骤 4. 重新注入记忆。
保存记忆（在 s09 中）只是该机制的一半。如果记忆永远不会重新进入模型输入，它实际上无法指导智能体。
因此，记忆自然应纳入提示词流程：

save durable facts in s09 在s09中保存持久化事实
re-inject them through the prompt builder in s10
在s10中通过提示构建器重新注入它们

步骤 5：单独附加每轮提醒。 有些信息的存在时间甚至比 “动态上下文” 更短 —— 它只在当前这一轮有效，不应污染稳定的系统提示。一条system-reminder类的用户消息可将这些临时信号完全置于构建器之外：

- 仅适用于本轮的指令
- 临时通知
- 临时恢复指引

s10_system_prompt.py - System Prompt Construction
This chapter teaches one core idea:
the system prompt should be assembled from clear sections, not written as one
giant hardcoded blob.
Teaching pipeline:
1. core instructions
2. tool listing
3. skill metadata
4. memory section
5. CLAUDE.md chain
6. dynamic context
The builder keeps stable information separate from information that changes
often. A simple DYNAMIC_BOUNDARY marker makes that split visible.
Per-turn reminders are even more dynamic. They are better injected as a
separate user-role system reminder than mixed blindly into the stable prompt.
Key insight: "Prompt construction is a pipeline with boundaries, not one
big string."

s10_system_prompt.py - 系统提示词构建
本章教授一个核心思想：
系统提示词应该从清晰的模块而非单一的硬编码字符串构建。
教学流水线：
1. 核心指令
2. 工具列表
3. 技能元数据
4. 记忆部分
5. CLAUDE.md 链
6. 动态上下文
构建器将稳定信息与频繁变化的信息分开。一个简单的 DYNAMIC_BOUNDARY 标记使其可见。
每轮提醒甚至更动态。它们最好作为单独的 system-reminder 用户消息注入，而不是盲目地混合到稳定的提示中。
关键洞察：“提示词构建是一个有边界的水管，而非一个巨大的静态字符串。”
"""
import datetime
import json
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
DYNAMIC_BOUNDARY = "=== DYNAMIC_BOUNDARY ==="


class SystemPromptBuilder:
    """
    Assemble the system prompt from independent sections.
    The teaching goal here is clarity:
    each section has one source and one responsibility.
    That makes the prompt easier to reason about, easier to test, and easier
    to evolve as the agent grows new capabilities.
    """

    def __init__(self, workdir: Path = None, tools: list = None):
        self.workdir = workdir or WORKDIR
        self.tools = tools or []
        self.skills_dir = self.workdir / "skills"
        self.memory_dir = self.workdir / ".memory"

    # -- Section 1: Core instructions --
    def _build_core(self) -> str:
        return (
            f"You are a coding agent operating in {self.workdir}.\n"
            "Use the provided tools to explore, read, write, and edit files.\n"
            "Always verify before assuming. Prefer reading files over guessing."
        )

    # -- Section 2: Tool listings --
    def _build_tool_listing(self) -> str:
        if not self.tools:
            return ""
        lines = ["# Available tools"]
        for tool in self.tools:
            props = tool.get("input_schema", {}).get("properties", {})
            params = ", ".join(props.keys())
            lines.append(f"- {tool['name']}({params}): {tool['description']}")
        return "\n".join(lines)

    # -- Section 3: Skill metadata (layer 1 from s05 concept) --
    def _build_skill_listing(self) -> str:
        if not self.skills_dir.exists():
            return ""
        skills = []
        for skill_dir in sorted(self.skills_dir.iterdir()):
            skill_md = skill_dir / "SKILL.md"
            if not skill_md.exists():
                continue
            text = skill_md.read_text()
            # Parse frontmatter for name + description
            match = re.match(r"^---\s*\n(.*?)\n---", text, re.DOTALL)
            if not match:
                continue
            meta = {}
            for line in match.group(1).splitlines():
                if ":" in line:
                    k, _, v = line.partition(":")
                    meta[k.strip()] = v.strip()
            name = meta.get("name", skill_dir.name)
            desc = meta.get("description", "")
            skills.append(f"- {name}: {desc}")
        if not skills:
            return ""
        return "# Available skills\n" + "\n".join(skills)

    # -- Section 4: Memory content --
    def _build_memory_section(self) -> str:
        if not self.memory_dir.exists():
            return ""
        memories = []
        for md_file in sorted(self.memory_dir.glob("*.md")):
            if md_file.name == "MEMORY.md":
                continue
            text = md_file.read_text()
            match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)", text, re.DOTALL)
            if not match:
                continue
            header, body = match.group(1), match.group(2).strip()
            meta = {}
            for line in header.splitlines():
                if ":" in line:
                    k, _, v = line.partition(":")
                    meta[k.strip()] = v.strip()
            name = meta.get("name", md_file.stem)
            mem_type = meta.get("type", "project")
            desc = meta.get("description", "")
            memories.append(f"[{mem_type}] {name}: {desc}\n{body}")
        if not memories:
            return ""
        return "# Memories (persistent)\n\n" + "\n\n".join(memories)

    # -- Section 5: CLAUDE.md chain --
    def _build_claude_md(self) -> str:
        """
        Load CLAUDE.md files in priority order (all are included):
        1. ~/.claude/CLAUDE.md (user-global instructions)
        2. <project-root>/CLAUDE.md (project instructions)
        3. <current-subdir>/CLAUDE.md (directory-specific instructions)
        """
        sources = []
        # User-global
        user_claude = Path.home() / ".claude" / "CLAUDE.md"
        if user_claude.exists():
            sources.append(
                ("user global (~/.claude/CLAUDE.md)", user_claude.read_text())
            )
        # Project root
        project_claude = self.workdir / "CLAUDE.md"
        if project_claude.exists():
            sources.append(("project root (CLAUDE.md)", project_claude.read_text()))
        # Subdirectory -- in real CC, this walks from cwd up to project root
        # Teaching: check cwd if different from workdir
        cwd = Path.cwd()
        if cwd != self.workdir:
            subdir_claude = cwd / "CLAUDE.md"
            if subdir_claude.exists():
                sources.append(
                    (f"subdir ({cwd.name}/CLAUDE.md)", subdir_claude.read_text())
                )
        if not sources:
            return ""
        parts = ["# CLAUDE.md instructions"]
        for label, content in sources:
            parts.append(f"## From {label}")
            parts.append(content.strip())
        return "\n\n".join(parts)

    # -- Section 6: Dynamic context --
    def _build_dynamic_context(self) -> str:
        lines = [
            f"Current date: {datetime.date.today().isoformat()}",
            f"Working directory: {self.workdir}",
            f"Model: {MODEL}",
            f"Platform: {os.uname().sysname}",
        ]
        return "# Dynamic context\n" + "\n".join(lines)

    # -- Assemble all sections --
    def build(self) -> str:
        """
        Assemble the full system prompt from all sections.
        Static sections (1-5) are separated from dynamic (6) by
        the DYNAMIC_BOUNDARY marker. In real CC, the static prefix
        is cached across turns to save prompt tokens.
        """
        sections = []
        core = self._build_core()
        if core:
            sections.append(core)
        tools = self._build_tool_listing()
        if tools:
            sections.append(tools)
        skills = self._build_skill_listing()
        if skills:
            sections.append(skills)
        memory = self._build_memory_section()
        if memory:
            sections.append(memory)
        claude_md = self._build_claude_md()
        if claude_md:
            sections.append(claude_md)
        # Static/dynamic boundary
        sections.append(DYNAMIC_BOUNDARY)
        dynamic = self._build_dynamic_context()
        if dynamic:
            sections.append(dynamic)
        return "\n\n".join(sections)


def build_system_reminder(extra: str = None) -> dict:
    """
    Build a system-reminder user message for per-turn dynamic content.
    The teaching version keeps reminders outside the stable system prompt so
    short-lived context does not get mixed into the long-lived instructions.
    """
    parts = []
    if extra:
        parts.append(extra)
    if not parts:
        return None
    content = "<system-reminder>\n" + "\n".join(parts) + "\n</system-reminder>"
    return {"role": "user", "content": content}


# -- Tool implementations --
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
        content = fp.read_text()
        if old_text not in content:
            return f"Error: Text not found in {path}"
        fp.write_text(content.replace(old_text, new_text, 1))
        return f"Edited {path}"
    except Exception as e:
        return f"Error: {e}"


TOOL_HANDLERS = {
    "bash": lambda **kw: run_bash(kw["command"]),
    "read_file": lambda **kw: run_read(kw["path"], kw.get("limit")),
    "write_file": lambda **kw: run_write(kw["path"], kw["content"]),
    "edit_file": lambda **kw: run_edit(kw["path"], kw["old_text"], kw["new_text"]),
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
]
# Global prompt builder
prompt_builder = SystemPromptBuilder(workdir=WORKDIR, tools=TOOLS)


def agent_loop(messages: list):
    """
    Agent loop with assembled system prompt.
    The system prompt is rebuilt each iteration. In real CC, the static
    prefix is cached and only the dynamic suffix changes per turn.
    """
    while True:
        system = prompt_builder.build()
        response = client.messages.create(
            model=MODEL,
            system=system,
            messages=messages,
            tools=TOOLS,
            max_tokens=8000,
        )
        messages.append({"role": "assistant", "content": response.content})
        if response.stop_reason != "tool_use":
            return
        results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            handler = TOOL_HANDLERS.get(block.name)
            try:
                output = (
                    handler(**(block.input or {}))
                    if handler
                    else f"Unknown: {block.name}"
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
    # Show the assembled prompt at startup for educational purposes
    full_prompt = prompt_builder.build()
    section_count = full_prompt.count("\n# ")
    print(
        f"[System prompt assembled: {len(full_prompt)} chars, ~{section_count} sections]"
    )
    # /prompt command shows the full assembled prompt
    history = []
    while True:
        try:
            query = input("\033[36ms10 >> \033[0m")
        except (EOFError, KeyboardInterrupt):
            break
        if query.strip().lower() in ("q", "exit", ""):
            break
        if query.strip() == "/prompt":
            print("--- System Prompt ---")
            print(prompt_builder.build())
            print("--- End ---")
            continue
        if query.strip() == "/sections":
            prompt = prompt_builder.build()
            for line in prompt.splitlines():
                if line.startswith("# ") or line == DYNAMIC_BOUNDARY:
                    print(f"  {line}")
            continue
        history.append({"role": "user", "content": query})
        agent_loop(history)
        response_content = history[-1]["content"]
        if isinstance(response_content, list):
            for block in response_content:
                if hasattr(block, "text"):
                    print(block.text)
        print()
