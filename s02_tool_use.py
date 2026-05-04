#!/usr/bin/env python3
# Harness: tool dispatch -- expanding what the model can reach.
"""
s02_tool_use.py - Tool dispatch + message normalization
The agent loop from s01 didn't change. We added tools to the dispatch map,
and a normalize_messages() function that cleans up the message list before
each API call.
Key insight: "The loop didn't change at all. I just added tools."

s02_tool_use.py - 工具分派 + 消息规范化
s01 中的 agent 循环没有改变。
我们添加了工具到 dispatch map，以及一个 normalize_messages() 函数，
用于在每次 API 调用之前清理消息列表。
关键洞察：“循环没有丝毫改变。我只是添加了工具。”
"""
import os
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

# 系统提示词，用于告诉模型它的角色和任务。
# 你是一个编码代理，位于 {WORKDIR}。使用工具解决问题。行动，不要解释。
SYSTEM = f"You are a coding agent at {WORKDIR}. Use tools to solve tasks. Act, don't explain."

# 返回安全的路径，确保始终在当前目录下工作
# 这就是路径沙箱机制，**防止模型套出工作区**。
# 在进行任何输入 / 输出操作前，系统会解析每个请求的路径，并对照工作目录进行检查
def safe_path(p: str) -> Path:
    # 获取当前的工作目录
    path = (WORKDIR / p).resolve()
    # 如果路径不在当前工作目录下，则抛出错误。
    if not path.is_relative_to(WORKDIR):
        raise ValueError(f"Path escapes workspace: {p}")
    # 返回安全的路径。  
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

# 读取文件内容
def run_read(path: str, limit: int = None) -> str:
    try:
        # 读取文件内容
        text = safe_path(path).read_text()
        # 将文件内容按行分割
        lines = text.splitlines()
        # 如果 limit 小于行数，则截取前 limit 行，并添加 "... (剩余行数) more lines"
        if limit and limit < len(lines):
            lines = lines[:limit] + [f"... ({len(lines) - limit} more lines)"]
        # 将行连接起来，并截取前 50000 个字符。
        return "\n".join(lines)[:50000]
    # 如果读取文件内容失败，则返回错误。
    except Exception as e:
        # 返回错误信息。
        return f"Error: {e}"

# 写入文件内容
def run_write(path: str, content: str) -> str:
    try:
        fp = safe_path(path)
        # 创建文件的父目录
        fp.parent.mkdir(parents=True, exist_ok=True)
        # 写入文件内容
        fp.write_text(content)
        # 返回写入的字节数统计信息文本。
        return f"Wrote {len(content)} bytes to {path}"
    # 写入失败，直接访问错误信息文本
    except Exception as e:
        return f"Error: {e}"


# 编辑文件内容
def run_edit(path: str, old_text: str, new_text: str) -> str:
    try:
        # 确保路径安全
        fp = safe_path(path)
        content = fp.read_text()
        if old_text not in content:
            return f"Error: Text not found in {path}"
        fp.write_text(content.replace(old_text, new_text, 1))
        return f"Edited {path}"
    except Exception as e:
        return f"Error: {e}"


# -- Concurrency safety classification --
# 并发安全分类：
# Read-only tools can safely run in parallel; mutating tools must be serialized.
# 并发安全分类：只读工具可以安全地并行运行；修改工具必须串行执行。
# 这里所谓的 CONCURRENCY_ 实际控制的是到底是串行执行还是可并行执行。
# read_file 可以串行(SAFE)，编辑和写文件都必须串行（UNSAFE）
CONCURRENCY_SAFE = {"read_file"}
CONCURRENCY_UNSAFE = {"write_file", "edit_file"}

# -- The dispatch map: {tool_name: handler} --
# 工具处理程序映射：{工具名称: 处理程序}。
# 处理程序是一个函数，接收一个字典参数，返回一个字符串。
# 调度映射将工具名称与处理程序关联起来。
# 这就是整个路由层 —— 没有 if / elif 链式判断，也没有类层级结构，只有一个字典。
TOOL_HANDLERS = {
    # bash 工具的处理程序：执行 bash 命令。
    "bash": lambda **kw: run_bash(kw["command"]),
    # read_file 工具的处理程序：读取文件内容。
    "read_file": lambda **kw: run_read(kw["path"], kw.get("limit")),
    # write_file 工具的处理程序：写入文件内容。
    "write_file": lambda **kw: run_write(kw["path"], kw["content"]),
    # edit_file 工具的处理程序：编辑文件内容。
    "edit_file": lambda **kw: run_edit(kw["path"], kw["old_text"], kw["new_text"]),
}

# 定义工具列表
# 包括：bash、read_file、write_file、edit_file。
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

# 规范化消息列表
def normalize_messages(messages: list) -> list:
    """Clean up messages before sending to the API.
    Three jobs:
    1. Strip internal metadata fields the API doesn't understand
    2. Ensure every tool_use has a matching tool_result (insert placeholder if missing)
    3. Merge consecutive same-role messages (API requires strict alternation)
    """
    cleaned = []
    for msg in messages:
        clean = {"role": msg["role"]}
        if isinstance(msg.get("content"), str):
            clean["content"] = msg["content"]
        elif isinstance(msg.get("content"), list):
            clean["content"] = [
                {k: v for k, v in block.items() if not k.startswith("_")}
                for block in msg["content"]
                if isinstance(block, dict)
            ]
        else:
            clean["content"] = msg.get("content", "")
        cleaned.append(clean)
    # Collect existing tool_result IDs
    existing_results = set()
    for msg in cleaned:
        if isinstance(msg.get("content"), list):
            for block in msg["content"]:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    existing_results.add(block.get("tool_use_id"))
    # Find orphaned tool_use blocks and insert placeholder results
    for msg in cleaned:
        if msg["role"] != "assistant" or not isinstance(msg.get("content"), list):
            continue
        for block in msg["content"]:
            if not isinstance(block, dict):
                continue
            if (
                block.get("type") == "tool_use"
                and block.get("id") not in existing_results
            ):
                cleaned.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": block["id"],
                                "content": "(cancelled)",
                            }
                        ],
                    }
                )
    # Merge consecutive same-role messages
    if not cleaned:
        return cleaned
    merged = [cleaned[0]]
    for msg in cleaned[1:]:
        if msg["role"] == merged[-1]["role"]:
            prev = merged[-1]
            prev_c = (
                prev["content"]
                if isinstance(prev["content"], list)
                else [{"type": "text", "text": str(prev["content"])}]
            )
            curr_c = (
                msg["content"]
                if isinstance(msg["content"], list)
                else [{"type": "text", "text": str(msg["content"])}]
            )
            prev["content"] = prev_c + curr_c
        else:
            merged.append(msg)
    return merged


def agent_loop(messages: list):
    while True:
        response = client.messages.create(
            model=MODEL,
            system=SYSTEM,
            messages=normalize_messages(messages),
            tools=TOOLS,
            max_tokens=8000,
        )
        messages.append({"role": "assistant", "content": response.content})
        if response.stop_reason != "tool_use":
            return
        results = []
        for block in response.content:
            if block.type == "tool_use":
                # 在循环中，按名称查找处理程序。
                # 循环体本身与 s01 中的相比没有变化 —— 只有调度行是新增的。
                # 在 s01 示例中，我们只支持一个 bash 工具，但实际场景中，我们会有很多工具的分发调用
                # 工具分发调用就是通过这个 TOOL_HANDLERS 字典来实现的。
                # 添加一个工具，就在 TOOL_HANDLERS 里添加一个映射条目，原来的主循环永远不变。
                handler = TOOL_HANDLERS.get(block.name)
                output = (
                    handler(**block.input) if handler else f"Unknown tool: {block.name}"
                )
                print(f"> {block.name}:")
                print(output[:200])
                results.append(
                    {"type": "tool_result", "tool_use_id": block.id, "content": output}
                )
        messages.append({"role": "user", "content": results})


# 示例：
# 1. 读取 pyproject.toml 文件
# 2. 创建一个名为 greet.py 的文件，并在其中编写一个 greet(name) 函数
# 3. 查看 greet.py 以确认修改生效
if __name__ == "__main__":
    history = []
    while True:
        try:
            query = input("\033[36ms02 >> \033[0m")
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
