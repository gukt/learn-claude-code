#!/usr/bin/env python3
# Harness: extensibility -- injecting behavior without touching the loop.
"""
## 引言
s07 中的代理有一个权限系统，控制允许它做什么。
但是权限是一个 yes/no gate —— 它们不允许您添加新的行为。
假设您希望每个 bash 命令都记录到审计文件中，或者您希望每个文件写入后 linter 自动运行，
或者您希望自定义安全扫描程序在工具输入执行之前检查它们。
您可以在主循环中为每个循环添加 if/else 分支，但这会将您的干净循环变成特殊情况的混乱。
您真正想要的是一种从外部扩展代理行为的方法，而无需修改循环本身。

## 问题
你在团队环境中运行你的智能体。不同的团队有着不同的需求：
安全团队希望扫描每一个 bash 命令，
质量保证团队希望在文件编辑后自动运行测试，
运维团队则希望记录下每一次工具调用的审计轨迹。
如果这些需求都需要对智能体的循环逻辑进行代码修改，最终你会得到一堆杂乱无章的条件判断代码，无人能够维护。
更糟糕的是，每一个新需求都意味着需要重新部署智能体。
你需要一种方式，让各团队能够在预先定义好的节点接入各自的逻辑 —— 而无需修改核心代码。

## 解决方案
智能体循环暴露了三个固定的扩展点（生命周期事件）。在每个节点，它都会运行名为 hooks 的外部 shell 命令。
每个 hook 通过退出代码传达其意图：静默继续、阻止操作或向对话中注入消息。

## 工作原理
步骤 1。 定义三个生命周期事件。
- SessionStart 在智能体启动时触发一次 —— 可用于初始化、日志记录或环境检查。
- PreToolUse 在每次工具调用前触发，且是**唯一能阻止执行的事件**。
- PostToolUse 在每次工具调用后触发，**可对结果进行标注但无法撤销**。

步骤 2。 在工作区根目录的外部 .hooks.json 文件中配置钩子。
每个钩子指定一个要运行的 shell 命令。可选的 matcher 字段按工具名称进行过滤 —— 如果没有匹配器，该钩子会针对每个工具触发。

步骤 3。实现退出码协议。这是钩子系统的核心
三种退出码对应三种含义。该协议特意设计得很简单，以便任何语言或脚本都能参与。
用 bash、Python、Ruby 等语言编写你的钩子即可 —— 只要能以正确的退出码退出就行。
- Success
- Block
- Inject
详见文档：https://learn.shareai.run/en/s08/

步骤 4。 通过环境变量将上下文传递给钩子。
钩子需要了解发生了什么 —— 是哪个事件触发了它们、正在调用哪个工具以及输入是什么样的。
对于 PostToolUse 钩子，还可以获取工具的输出。

步骤 5。将钩子集成到智能体循环中。
集成过程简洁明了：在执行前运行前置钩子，检查是否有被阻塞的情况，执行工具，运行后置钩子，并收集所有注入的消息。
循环仍拥有控制流 —— 钩子仅在指定时刻进行观察、阻塞或标注。

s08_hook_system.py - Hook System
Hooks are extension points around the main loop.
They let readers add behavior without rewriting the loop itself.
Teaching version:
  - SessionStart
  - PreToolUse
  - PostToolUse
Teaching exit-code contract:
  - 0 -> continue
  - 1 -> block
  - 2 -> inject a message
This is intentionally simpler than a production system. The goal here is to
teach the extension pattern clearly before introducing event-specific edge
cases.
Key insight: "Extend the agent without touching the loop."
"""
import json
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
# The teaching version keeps only the three clearest events. More complete
# systems can grow the event surface later.
HOOK_EVENTS = ("PreToolUse", "PostToolUse", "SessionStart")
HOOK_TIMEOUT = 30  # seconds
# Real CC timeouts:
#   TOOL_HOOK_EXECUTION_TIMEOUT_MS = 600000 (10 minutes for tool hooks)
#   SESSION_END_HOOK_TIMEOUT_MS = 1500 (1.5 seconds for SessionEnd hooks)
# Workspace trust marker. Hooks only run if this file exists (or SDK mode).
TRUST_MARKER = WORKDIR / ".claude" / ".claude_trusted"


class HookManager:
    """
    Load and execute hooks from .hooks.json configuration.
    The hook manager does three simple jobs:
    - load hook definitions
    - run matching commands for an event
    - aggregate block / message results for the caller
    """

    def __init__(self, config_path: Path = None, sdk_mode: bool = False):
        self.hooks = {"PreToolUse": [], "PostToolUse": [], "SessionStart": []}
        self._sdk_mode = sdk_mode
        config_path = config_path or (WORKDIR / ".hooks.json")
        if config_path.exists():
            try:
                config = json.loads(config_path.read_text())
                for event in HOOK_EVENTS:
                    self.hooks[event] = config.get("hooks", {}).get(event, [])
                print(f"[Hooks loaded from {config_path}]")
            except Exception as e:
                print(f"[Hook config error: {e}]")

    def _check_workspace_trust(self) -> bool:
        """
        Check whether the current workspace is trusted.
        The teaching version uses a simple trust marker file.
        In SDK mode, trust is treated as implicit.
        """
        if self._sdk_mode:
            return True
        return TRUST_MARKER.exists()

    def run_hooks(self, event: str, context: dict = None) -> dict:
        """
        Execute all hooks for an event.
        Returns: {"blocked": bool, "messages": list[str]}
          - blocked: True if any hook returned exit code 1
          - messages: stderr content from exit-code-2 hooks (to inject)
        """
        result = {"blocked": False, "messages": []}
        # Trust gate: refuse to run hooks in untrusted workspaces
        if not self._check_workspace_trust():
            return result
        hooks = self.hooks.get(event, [])
        for hook_def in hooks:
            # Check matcher (tool name filter for PreToolUse/PostToolUse)
            matcher = hook_def.get("matcher")
            if matcher and context:
                tool_name = context.get("tool_name", "")
                if matcher != "*" and matcher != tool_name:
                    continue
            command = hook_def.get("command", "")
            if not command:
                continue
            # Build environment with hook context
            env = dict(os.environ)
            if context:
                env["HOOK_EVENT"] = event
                env["HOOK_TOOL_NAME"] = context.get("tool_name", "")
                env["HOOK_TOOL_INPUT"] = json.dumps(
                    context.get("tool_input", {}), ensure_ascii=False
                )[:10000]
                if "tool_output" in context:
                    env["HOOK_TOOL_OUTPUT"] = str(context["tool_output"])[:10000]
            try:
                r = subprocess.run(
                    command,
                    shell=True,
                    cwd=WORKDIR,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=HOOK_TIMEOUT,
                )
                if r.returncode == 0:
                    # Continue silently
                    if r.stdout.strip():
                        print(f"  [hook:{event}] {r.stdout.strip()[:100]}")
                    # Optional structured stdout: small extension point that
                    # keeps the teaching contract simple.
                    try:
                        hook_output = json.loads(r.stdout)
                        if "updatedInput" in hook_output and context:
                            context["tool_input"] = hook_output["updatedInput"]
                        if "additionalContext" in hook_output:
                            result["messages"].append(hook_output["additionalContext"])
                        if "permissionDecision" in hook_output:
                            result["permission_override"] = hook_output[
                                "permissionDecision"
                            ]
                    except (json.JSONDecodeError, TypeError):
                        pass  # stdout was not JSON -- normal for simple hooks
                elif r.returncode == 1:
                    # Block execution
                    result["blocked"] = True
                    reason = r.stderr.strip() or "Blocked by hook"
                    result["block_reason"] = reason
                    print(f"  [hook:{event}] BLOCKED: {reason[:200]}")
                elif r.returncode == 2:
                    # Inject message
                    msg = r.stderr.strip()
                    if msg:
                        result["messages"].append(msg)
                        print(f"  [hook:{event}] INJECT: {msg[:200]}")
            except subprocess.TimeoutExpired:
                print(f"  [hook:{event}] Timeout ({HOOK_TIMEOUT}s)")
            except Exception as e:
                print(f"  [hook:{event}] Error: {e}")
        return result


# -- Tool implementations (same as s02) --
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
SYSTEM = f"You are a coding agent at {WORKDIR}. Use tools to solve tasks."


def agent_loop(messages: list, hooks: HookManager):
    """
    The hook-aware agent loop.
    The teaching version keeps only the clearest integration points:
    SessionStart, PreToolUse, execute tool, PostToolUse.
    """
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
            tool_input = dict(block.input or {})
            ctx = {"tool_name": block.name, "tool_input": tool_input}
            # -- PreToolUse hooks --
            pre_result = hooks.run_hooks("PreToolUse", ctx)
            # Inject hook messages into results
            for msg in pre_result.get("messages", []):
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": f"[Hook message]: {msg}",
                    }
                )
            if pre_result.get("blocked"):
                reason = pre_result.get("block_reason", "Blocked by hook")
                output = f"Tool blocked by PreToolUse hook: {reason}"
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": output,
                    }
                )
                continue
            # -- Execute tool --
            handler = TOOL_HANDLERS.get(block.name)
            try:
                output = handler(**tool_input) if handler else f"Unknown: {block.name}"
            except Exception as e:
                output = f"Error: {e}"
            print(f"> {block.name}: {str(output)[:200]}")
            # -- PostToolUse hooks --
            ctx["tool_output"] = output
            post_result = hooks.run_hooks("PostToolUse", ctx)
            # Inject post-hook messages
            for msg in post_result.get("messages", []):
                output += f"\n[Hook note]: {msg}"
            results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": str(output),
                }
            )
        messages.append({"role": "user", "content": results})


# 试一试
# Watch SessionStart hook fire at startup
# 监听会话启动挂钩在启动时触发

# Ask the agent to run a bash command -- see PreToolUse hook fire
# 让智能体运行一个 bash 命令 —— 查看 PreToolUse 钩子触发情况

# Create a blocking hook (exit 1) and watch it prevent tool execution
# 创建一个阻塞式钩子（退出码 1），并观察它如何阻止工具执行

# Create an injecting hook (exit 2) and watch it add messages to the conversation
# 创建一个注入式钩子（退出码 2），并观察它如何向对话中添加消息
if __name__ == "__main__":
    hooks = HookManager()
    # Fire SessionStart hooks
    hooks.run_hooks("SessionStart", {"tool_name": "", "tool_input": {}})
    history = []
    while True:
        try:
            query = input("\033[36ms08 >> \033[0m")
        except (EOFError, KeyboardInterrupt):
            break
        if query.strip().lower() in ("q", "exit", ""):
            break
        history.append({"role": "user", "content": query})
        agent_loop(history, hooks)
        response_content = history[-1]["content"]
        if isinstance(response_content, list):
            for block in response_content:
                if hasattr(block, "text"):
                    print(block.text)
        print()
