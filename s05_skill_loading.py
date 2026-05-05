#!/usr/bin/env python3
# Harness: on-demand knowledge -- discover skills cheaply, load them only when needed.
"""
## 引言
你不会去记住自己所有食谱书里的每一道菜谱。你只知道每本食谱书放在哪个架子上，只有真的要做那道菜时，才会把它取下来。智能体的领域知识也是同样的道理。你可能拥有针对 Git 工作流、测试模式、代码评审清单、PDF 处理等数十个主题的专业知识库文件。在每次请求中把所有知识都加载到系统提示词里，就好比在连一个鸡蛋都没敲之前，就把所有食谱书从头到尾读了一遍。对于任何一项具体任务来说，这些知识中的大部分都是无关的。

## 问题
你希望你的智能体遵循特定领域的工作流程：git 规范、测试最佳实践、代码审查清单。简单的做法是将所有内容都放入系统提示中。但如果有 10 项技能，每项技能占用 2000 个标记，那么每次 API 调用就会有 20000 个标记的指令 —— 其中大部分与当前问题毫无关联。每一轮对话你都需要为这些标记付费，更糟糕的是，所有这些无关文本都会与真正重要的内容争夺模型的注意力。

## 解决方案
将知识拆分为两个层级。第一层存在于系统提示词中，成本较低：仅包含技能名称和一行描述（每个技能约 100 个 tokens）。第二层是完整的技能主体，仅当模型判断需要该知识时，才通过工具调用按需加载。

s05_skill_loading.py - Skills
This chapter teaches a two-layer skill model:
1. Put a cheap skill catalog in the system prompt.
2. Load the full skill body only when the model asks for it.
That keeps the prompt small while still giving the model access to reusable,
task-specific guidance.

s05_skill_loading.py - 技能
本章教我们一个两层级的技能模型：
1. 在系统提示词中放置一个廉价的技能目录。
2. 仅当模型请求时，才加载完整的技能主体。
这保持了提示词较小，同时仍为模型提供可重用、任务特定的指导。
"""
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv(override=True)
if os.getenv("ANTHROPIC_BASE_URL"):
    os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)
WORKDIR = Path.cwd()
client = Anthropic(base_url=os.getenv("ANTHROPIC_BASE_URL"))
MODEL = os.environ["MODEL_ID"]
SKILLS_DIR = WORKDIR / "skills"

# 技能清单（manifest）
@dataclass
class SkillManifest:
    name: str
    description: str
    path: Path

# 技能文档 = manifest（SkillManifest） + body(str)
@dataclass
class SkillDocument:
    manifest: SkillManifest
    body: str


# Skill 注册表（SkillRegistry）
class SkillRegistry:
    def __init__(self, skills_dir: Path):
        # 技能目录
        self.skills_dir = skills_dir
        # 技能文档字典：key 是技能名称，value 是技能文档（SkillDocument）。
        self.documents: dict[str, SkillDocument] = {}
        # 开始加载所有技能
        self._load_all()

    # 加载所有技能
    def _load_all(self) -> None:
        # 如果技能目录不存在，则返回。
        if not self.skills_dir.exists():
            return
        # 在技能目录里，找到所有名为 SKILL.md 的文件，并按字母顺序排序。
        # (gukt: 这里的 rglob 是不支持软连接的)
        for path in sorted(self.skills_dir.rglob("SKILL.md")):
            # 解析技能文件的 frontmatter 和 body。
            # frontmatter 是技能文件的元数据，body 是技能文件的内容。
            meta, body = self._parse_frontmatter(path.read_text())
            # 技能名称
            name = meta.get("name", path.parent.name)
            # 技能描述
            description = meta.get("description", "No description")
            # 创建技能清单（SkillManifest）
            manifest = SkillManifest(name=name, description=description, path=path)
            # 将技能文档（SkillDocument）添加到技能文档字典中。
            self.documents[name] = SkillDocument(manifest=manifest, body=body.strip())

    # 解析技能文件的 frontmatter 和 body。
    # frontmatter 是技能文件的元数据，body 是技能文件的内容。
    def _parse_frontmatter(self, text: str) -> tuple[dict, str]:
        match = re.match(r"^---\n(.*?)\n---\n(.*)", text, re.DOTALL)
        if not match:
            return {}, text
        meta = {}
        for line in match.group(1).strip().splitlines():
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            meta[key.strip()] = value.strip()
        return meta, match.group(2)

    # 描述所有可用的技能
    def describe_available(self) -> str:
        # 如果技能文档字典为空，则返回提示。
        if not self.documents:
            return "(no skills available)"
        lines = []
        for name in sorted(self.documents):
            manifest = self.documents[name].manifest
            lines.append(f"- {manifest.name}: {manifest.description}")
        return "\n".join(lines)

    def load_full_text(self, name: str) -> str:
        document = self.documents.get(name)
        if not document:
            known = ", ".join(sorted(self.documents)) or "(none)"
            return f"Error: Unknown skill '{name}'. Available skills: {known}"
        return (
            f'<skill name="{document.manifest.name}">\n' f"{document.body}\n" "</skill>"
        )


SKILL_REGISTRY = SkillRegistry(SKILLS_DIR)
SYSTEM = f"""You are a coding agent at {WORKDIR}.
Use load_skill when a task needs specialized instructions before you act.
Skills available:
{SKILL_REGISTRY.describe_available()}
"""


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


TOOL_HANDLERS = {
    "bash": lambda **kw: run_bash(kw["command"]),
    "read_file": lambda **kw: run_read(kw["path"], kw.get("limit")),
    "write_file": lambda **kw: run_write(kw["path"], kw["content"]),
    "edit_file": lambda **kw: run_edit(kw["path"], kw["old_text"], kw["new_text"]),
    # 这里多添加了 load_skill 工具，用于加载技能主体。
    "load_skill": lambda **kw: SKILL_REGISTRY.load_full_text(kw["name"]),
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
        "name": "load_skill",
        "description": "Load the full body of a named skill into the current context.",
        "input_schema": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
    },
]


def extract_text(content) -> str:
    if not isinstance(content, list):
        return ""
    texts = []
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
            print(f"> {block.name}: {str(output)[:200]}")
            results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": str(output),
                }
            )
        messages.append({"role": "user", "content": results})

# 示例：
# - What skills are available?
# 有哪些技能可选？
#
# - Load the agent-builder skill and follow its instructions
# 加载 agent-builder 技能并遵循其说明
#
# I need to do a code review -- load the relevant skill first
# 我需要进行代码审查——先加载相关技能
#
# Build an MCP server using the mcp-builder skill
# 使用 mcp-builder 技能构建一个 MCP 服务器
if __name__ == "__main__":
    history = []
    while True:
        try:
            query = input("\033[36ms05 >> \033[0m")
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
