#!/usr/bin/env python3
# Harness: compression -- keep the active context small enough to keep working.
"""
## 引言
你来自 s05 的智能体能力很强。它能读取文件、运行命令、编辑代码，还能分配子任务。
但不妨试试更有挑战性的事 —— 让它重构一个涉及 30 个文件的模块。在读取完所有文件并执行 20 条 Shell 命令后，你会发现它的回复质量开始下降。模型会忘记已经读取过的内容，重复执行任务，最终 API 会直接拒绝你的请求。你已经触达了上下文窗口的限制，如果没有对应的解决方案，你的智能体就会陷入困境。

## 问题
对模型的每次 API 调用都会包含目前为止的完整对话：每一条用户消息、每一次助手回复、每一次工具调用及其结果。模型的上下文窗口（它能同时在工作内存中容纳的文本总量）是有限的。对一个包含 1000 行代码的源文件进行一次read_file操作大约会消耗 4000 个标记（以单词为单位的片段 —— 一个 1000 行的文件约需 4000 个标记）。读取 30 个文件并执行 20 个 bash 命令，你就会消耗掉 10 万多个标记。此时上下文已占满，但工作才完成一半。

简单粗暴的修复方法 —— 直接截断旧消息 —— 会丢弃智能体后续可能需要的信息。更聪明的做法是有策略地压缩：保留关键内容，将冗长细节转移到磁盘中，并在对话过长时进行总结。本章正是围绕这一思路展开。

## 解决方案
我们运用四个控制手段，分别作用于流程的不同阶段，从输出时的过滤到完整的对话摘要生成。
详见官方文档：https://learn.shareai.run/en/s06/
```

## 工作原理
详见官方文档：https://learn.shareai.run/en/s06/

s06_context_compact.py - Context Compact
This teaching version keeps the compact model intentionally small:
1. Large tool output is persisted to disk and replaced with a preview marker.
2. Older tool results are micro-compacted into short placeholders.
3. When the whole conversation gets too large, the agent summarizes it and continues from that summary.
The goal is not to model every production branch. The goal is to make the
active-context idea explicit and teachable.

s06_context_compact.py - 上下文压缩
这个教学版本故意保持紧凑模型的小巧：
1. 大型工具输出被持久化到磁盘，并替换为预览标记。
2. 旧的工具结果被微型压缩为短占位符。
3. 当整个对话过长时，智能体总结它并从该总结继续工作。
我们的目标并非为每个生产分支都建立模型，而是要把主动上下文这一理念阐释清晰、使之易于传授。
"""
import json
import os
import subprocess
import time
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

# 系统提示词：
# 你是一个编程智能体，工作在 {WORKDIR} 目录下。
# 保持逐步工作，如果对话过长，使用 compact 工具进行压缩。
SYSTEM = (
    f"You are a coding agent at {WORKDIR}. "
    "Keep working step by step, and use compact if the conversation gets too long."
)
# 上下文窗口限制：50000 个 tokens
CONTEXT_LIMIT = 10000
# 保留最近的工具结果：3 个
KEEP_RECENT_TOOL_RESULTS = 3
# 持久化阈值：30000 个 tokens
PERSIST_THRESHOLD = 10000
# 预览字符数：2000 个字符
PREVIEW_CHARS = 1000
# 转录目录：.transcripts
TRANSCRIPT_DIR = WORKDIR / ".transcripts"
# 工具结果目录：.task_outputs/tool-results
TOOL_RESULTS_DIR = WORKDIR / ".task_outputs" / "tool-results"

# 状态类：CompactState
@dataclass
class CompactState:
    # 是否已经压缩过
    has_compacted: bool = False
    # 最后一次总结
    last_summary: str = ""
    # 最近访问的文件
    recent_files: list[str] = field(default_factory=list)


# 估计上下文大小
def estimate_context_size(messages: list) -> int:
    # 返回消息的字符串长度
    return len(str(messages))


def track_recent_file(state: CompactState, path: str) -> None:
    if path in state.recent_files:
        state.recent_files.remove(path)
    state.recent_files.append(path)
    if len(state.recent_files) > 5:
        state.recent_files[:] = state.recent_files[-5:]


def safe_path(path_str: str) -> Path:
    path = (WORKDIR / path_str).resolve()
    if not path.is_relative_to(WORKDIR):
        raise ValueError(f"Path escapes workspace: {path_str}")
    return path


def persist_large_output(tool_use_id: str, output: str) -> str:
    if len(output) <= PERSIST_THRESHOLD:
        return output
    TOOL_RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stored_path = TOOL_RESULTS_DIR / f"{tool_use_id}.txt"
    if not stored_path.exists():
        stored_path.write_text(output)
    preview = output[:PREVIEW_CHARS]
    rel_path = stored_path.relative_to(WORKDIR)
    return (
        "<persisted-output>\n"
        f"Full output saved to: {rel_path}\n"
        "Preview:\n"
        f"{preview}\n"
        "</persisted-output>"
    )


def collect_tool_result_blocks(messages: list) -> list[tuple[int, int, dict]]:
    blocks = []
    for message_index, message in enumerate(messages):
        content = message.get("content")
        if message.get("role") != "user" or not isinstance(content, list):
            continue
        for block_index, block in enumerate(content):
            if isinstance(block, dict) and block.get("type") == "tool_result":
                blocks.append((message_index, block_index, block))
    return blocks


def micro_compact(messages: list) -> list:
    tool_results = collect_tool_result_blocks(messages)
    if len(tool_results) <= KEEP_RECENT_TOOL_RESULTS:
        return messages
    for _, _, block in tool_results[:-KEEP_RECENT_TOOL_RESULTS]:
        content = block.get("content", "")
        if not isinstance(content, str) or len(content) <= 120:
            continue
        block["content"] = (
            "[Earlier tool result compacted. Re-run the tool if you need full detail.]"
        )
    return messages


def write_transcript(messages: list) -> Path:
    TRANSCRIPT_DIR.mkdir(parents=True, exist_ok=True)
    path = TRANSCRIPT_DIR / f"transcript_{int(time.time())}.jsonl"
    with path.open("w") as handle:
        for message in messages:
            handle.write(json.dumps(message, default=str) + "\n")
    return path


def summarize_history(messages: list) -> str:
    conversation = json.dumps(messages, default=str)[:80000]
    prompt = (
        "Summarize this coding-agent conversation so work can continue.\n"
        "Preserve:\n"
        "1. The current goal\n"
        "2. Important findings and decisions\n"
        "3. Files read or changed\n"
        "4. Remaining work\n"
        "5. User constraints and preferences\n"
        "Be compact but concrete.\n\n"
        f"{conversation}"
    )
    response = client.messages.create(
        model=MODEL,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=2000,
    )
    return response.content[0].text.strip()


def compact_history(
    messages: list, state: CompactState, focus: str | None = None
) -> list:
    transcript_path = write_transcript(messages)
    print(f"[transcript saved: {transcript_path}]")
    summary = summarize_history(messages)
    if focus:
        summary += f"\n\nFocus to preserve next: {focus}"
    if state.recent_files:
        recent_lines = "\n".join(f"- {path}" for path in state.recent_files)
        summary += f"\n\nRecent files to reopen if needed:\n{recent_lines}"
    state.has_compacted = True
    state.last_summary = summary
    return [
        {
            "role": "user",
            "content": (
                "This conversation was compacted so the agent can continue working.\n\n"
                f"{summary}"
            ),
        }
    ]


def run_bash(command: str, tool_use_id: str) -> str:
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
    output = (result.stdout + result.stderr).strip() or "(no output)"
    return persist_large_output(tool_use_id, output)


def run_read(
    path: str, tool_use_id: str, state: CompactState, limit: int | None = None
) -> str:
    try:
        track_recent_file(state, path)
        lines = safe_path(path).read_text().splitlines()
        if limit and limit < len(lines):
            lines = lines[:limit] + [f"... ({len(lines) - limit} more lines)"]
        output = "\n".join(lines)
        return persist_large_output(tool_use_id, output)
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
        "name": "compact",
        "description": "Summarize earlier conversation so work can continue in a smaller context.",
        "input_schema": {
            "type": "object",
            "properties": {
                "focus": {"type": "string"},
            },
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


def execute_tool(block, state: CompactState) -> str:
    if block.name == "bash":
        return run_bash(block.input["command"], block.id)
    if block.name == "read_file":
        return run_read(block.input["path"], block.id, state, block.input.get("limit"))
    if block.name == "write_file":
        return run_write(block.input["path"], block.input["content"])
    if block.name == "edit_file":
        return run_edit(
            block.input["path"], block.input["old_text"], block.input["new_text"]
        )
    if block.name == "compact":
        return "Compacting conversation..."
    return f"Unknown tool: {block.name}"


def agent_loop(messages: list, state: CompactState) -> None:
    while True:
        messages[:] = micro_compact(messages)
        if estimate_context_size(messages) > CONTEXT_LIMIT:
            print("[auto compact]")
            messages[:] = compact_history(messages, state)
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
        manual_compact = False
        compact_focus = None
        for block in response.content:
            if block.type != "tool_use":
                continue
            output = execute_tool(block, state)
            if block.name == "compact":
                manual_compact = True
                compact_focus = (block.input or {}).get("focus")
            print(f"> {block.name}: {str(output)[:200]}")
            results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": str(output),
                }
            )
        messages.append({"role": "user", "content": results})
        if manual_compact:
            print("[manual compact]")
            messages[:] = compact_history(messages, state, focus=compact_focus)

# 试一试
# - Read every Python file in the current work directory one by one 
# - 依次读取工作目录下的每一个 Python 文件
#   - 注意微型紧凑替换旧结果
#   - 持续读取文件，直到压缩功能自动触发
#   - 使用精简工具手动压缩对话
if __name__ == "__main__":
    history = []
    compact_state = CompactState()
    while True:
        try:
            query = input("\033[36ms06 >> \033[0m")
        except (EOFError, KeyboardInterrupt):
            break
        if query.strip().lower() in ("q", "exit", ""):
            break
        history.append({"role": "user", "content": query})
        agent_loop(history, compact_state)
        final_text = extract_text(history[-1]["content"])
        if final_text:
            print(final_text)
        print()
