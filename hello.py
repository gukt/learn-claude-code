# Hello World - 我的第一个 Python 文件

def greet(name: str) -> str:
    """返回一个问候语"""
    return f"Hello, {name}! 👋"

def main():
    names = ["Claude", "Python", "World"]
    for name in names:
        print(greet(name))

if __name__ == "__main__":
    main()
