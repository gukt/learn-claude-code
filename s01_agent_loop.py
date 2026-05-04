#!/usr/bin/env python3
# Harness: the loop -- keep feeding real tool results back into the model.
"""
s01_agent_loop.py - The Agent Loop
This file teaches the smallest useful coding-agent pattern:
    user message
      -> model reply
      -> if tool_use: execute tools
      -> write tool_result back to messages
      -> continue
It intentionally keeps the loop small, but still makes the loop state explicit
so later chapters can grow from the same structure.

s01_agnet_loop.py - 智能体（Agent）循环
本文教我们一个最简单且有用的 coding-agent 的模式：
  用户消息（query)
  -> 模型回复
  -> 如果是工具调用 tool_use ：执行工具
  -> 将工具结果写回消息
  -> 继续
它故意保持循环简单，但仍然显式地定义循环状态，以便后续章节可以基于相同的结构增长。
"""
import os
import subprocess
from dataclasses import dataclass

try:
    import readline

    # #143 UTF-8 backspace fix for macOS libedit
    readline.parse_and_bind("set bind-tty-special-chars off")
    readline.parse_and_bind("set input-meta on")
    readline.parse_and_bind("set output-meta on")
    readline.parse_and_bind("set convert-meta off")
    readline.parse_and_bind("set enable-meta-keybindings on")
except ImportError:
    pass
from anthropic import Anthropic
from dotenv import load_dotenv

# 加载 .env 文件中的环境变量，并用 .env 中的值覆盖已存在的同名环境变量。
# 这确保代码在运行时始终使用 .env 文件配置的参数。
load_dotenv(override=True)

# 如果 ANTHROPIC_BASE_URL 存在，则删除 ANTHROPIC_AUTH_TOKEN 环境变量。
if os.getenv("ANTHROPIC_BASE_URL"):
    os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)

# 创建 Anthropic 客户端，使用 .env 文件中配置的 ANTHROPIC_BASE_URL。
client = Anthropic(base_url=os.getenv("ANTHROPIC_BASE_URL"))

# 获取 .env 文件中配置的 MODEL_ID。 用于指定要使用的模型。
MODEL = os.environ["MODEL_ID"]

# 定义系统提示（system prompt），用于告诉模型它的角色和任务。
# 系统提示词中还提供了当前工作目录 os.getcwd()。
SYSTEM = (
    f"You are a coding agent at {os.getcwd()}. "
    "Use bash to inspect and change the workspace. Act first, then report clearly." # 使用 bash 检查和更改工作空间。先行动，然后清晰地报告。
)
# 定义工具（tools），用于告诉模型它可以使用的工具。
TOOLS = [
    {
        "name": "bash",  # 工具名称
        "description": "Run a shell command in the current workspace.",  # 工具描述
        # 这接近于 Anthropic Claude LLM 工具（tool_use）API 的标准定义，符合其开放工具接口的设计规范。
        # 相关官方文档可参考：
        # - https://platform.claude.com/docs/zh-CN/agents-and-tools/tool-use/strict-tool-use
        # - https://developers.openai.com/api/docs/guides/function-calling
        # 重点关注 input_schema 的写法，以及 properties、required 字段的定义方式。
        "input_schema": {  # 工具输入模式
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
        },
    }
]

# 定义循环状态（LoopState），用于存储循环的状态。
@dataclass
class LoopState:
    # The minimal loop state: history, loop count, and why we continue.
    # 最小的循环状态：历史记录、循环计数和为什么我们继续。
    messages: list
    turn_count: int = 1
    # 流转原因：为什么我们继续。
    transition_reason: str | None = None


def run_bash(command: str) -> str:
    # 定义危险命令，用于防止执行危险命令。
    dangerous = ["rm -rf /", "sudo", "shutdown", "reboot", "> /dev/"]
    # 如果命令包含危险字符，则返回错误。
    if any(item in command for item in dangerous):
        return "Error: Dangerous command blocked"
    try:
        # gukt: 打印正在执行的命令名称
        print(f"Executing command: {command}")

        # 执行命令，并捕获输出。
        # subprocess.run() 用于在 Python 中执行外部 shell 命令，并等待其完成。
        # 这里将命令的执行结果（如输出和错误）保存到 result 变量中，方便后续处理。
        # 这样可以安全、同步地调用系统命令并获取其结果。
        result = subprocess.run(
            command,
            shell=True,
            cwd=os.getcwd(),
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        return "Error: Timeout (120s)"
    except (FileNotFoundError, OSError) as e:
        return f"Error: {e}"
    # 将标准输出和标准错误合并，并去除空白字符。
    output = (result.stdout + result.stderr).strip()
    # 如果输出为空，则返回 "(no output)"。
    # 如果输出超过 50000 字符，则截取前 50000 字符。
    return output[:50000] if output else "(no output)"

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

# 执行工具调用，用于执行模型回复中的工具调用。
# 如果 block.type 不是 tool_use，则继续。
# 如果 block.type 是 tool_use，则执行命令，并添加到 results 列表中。
# https://platform.claude.com/docs/zh-CN/agents-and-tools/tool-use/overview
def execute_tool_calls(response_content) -> list[dict]:
    results = []
    for block in response_content:
        if block.type != "tool_use":
            continue
        command = block.input["command"]
        print(f"\033[33m$ {command}\033[0m")
        output = run_bash(command)
        print(output[:200])
        results.append(
            {
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": output,
            }
        )
    return results

# 执行一轮和 LLM 交互
def run_one_turn(state: LoopState) -> bool:
    # 调用 LLM，生成响应。
    response = client.messages.create(
        model=MODEL,
        system=SYSTEM, # 使用传入系统提示词
        messages=state.messages, # 传入历史消息
        tools=TOOLS, # 指定工具列表
        max_tokens=8000, # 指定最大可输出的 Token 数
    )
    # 将模型回复添加到历史消息中。
    state.messages.append({"role": "assistant", "content": response.content}) 
    if response.stop_reason != "tool_use":
        state.transition_reason = None
        return False
    results = execute_tool_calls(response.content)
    if not results:
        state.transition_reason = None
        return False
    state.messages.append({"role": "user", "content": results})
    state.turn_count += 1
    state.transition_reason = "tool_result"
    return True


def agent_loop(state: LoopState) -> None:
    while run_one_turn(state):
        pass

# 示例
# 1. 创建一个 hello.py 随便写点什么
# 2. 我当前在哪个分支下？
# 3. 列出此目录下所有的 Python 文件
# 4. 创建一个名为 test_output 的目录，并在其中写入 3 个文件
if __name__ == "__main__":
    history = []
    while True:
        try:
            query = input("\033[36ms01 >> \033[0m")
        except (EOFError, KeyboardInterrupt):
            break
        if query.strip().lower() in ("q", "exit", ""):
            break
        history.append({"role": "user", "content": query})
        state = LoopState(messages=history)
        agent_loop(state)
        final_text = extract_text(history[-1]["content"])
        if final_text:
            print(final_text)
        print()
