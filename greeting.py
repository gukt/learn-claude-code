def greet(name: str) -> str:
    """返回一句问候语"""
    return f"Hello, {name}! 👋 Welcome to the world of Python!"


def greet_by_time(name: str) -> str:
    """根据当前时间返回不同的问候语"""
    from datetime import datetime

    hour = datetime.now().hour

    if 6 <= hour < 12:
        period = "Good morning"
    elif 12 <= hour < 18:
        period = "Good afternoon"
    elif 18 <= hour < 22:
        period = "Good evening"
    else:
        period = "Good night"

    return f"{period}, {name}! 🌟"


if __name__ == "__main__":
    names = ["Alice", "Bob", "Charlie"]

    print("=== Simple Greetings ===")
    for name in names:
        print(greet(name))

    print("\n=== Time-based Greetings ===")
    for name in names:
        print(greet_by_time(name))
