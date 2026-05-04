"""
hello.py — 简单的问候示例模块。

演示内容：
  - 模块级文档字符串
  - 函数类型提示（参数 & 返回值）
  - 函数文档字符串（Google 风格）
  - 主程序保护块（if __name__ == "__main__"）
"""


def greet(name: str) -> str:
    """生成对指定姓名的问候语。

    Args:
        name: 被问候的人或事物的名称。

    Returns:
        格式化后的问候字符串，例如 "Hello, Claude! 👋"。

    Raises:
        ValueError: 当 name 为空字符串时抛出。

    Examples:
        >>> greet("Claude")
        'Hello, Claude! 👋'
        >>> greet("World")
        'Hello, World! 👋'
    """
    if not name:
        raise ValueError("name 不能为空字符串")
    return f"Hello, {name}! 👋"


def main() -> None:
    """程序入口：依次问候预设名单中的每个名称并打印结果。"""
    names: list[str] = ["Claude", "Python", "World"]
    for name in names:
        print(greet(name))


if __name__ == "__main__":
    main()
