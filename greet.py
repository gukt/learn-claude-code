def greet(name: str) -> str:
    """
    返回一条个性化的问候语。

    参数:
        name (str): 被问候者的名字

    返回:
        str: 格式化后的问候字符串
    """
    if not name or not name.strip():
        raise ValueError("名字不能为空")
    return f"Hello, {name.strip()}! 👋 欢迎你！"


if __name__ == "__main__":
    # 示例调用
    print(greet("Alice"))
    print(greet("世界"))
