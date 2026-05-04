"""
utils.py - Common utility functions.
"""


def greet(name: str) -> str:
    """Return a greeting string for the given name.

    Args:
        name: The name of the person to greet.

    Returns:
        A greeting string.

    Raises:
        ValueError: If name is empty.

    Examples:
        >>> greet("Alice")
        'Hello, Alice!'
    """
    if not name or not name.strip():
        raise ValueError("name must not be empty")
    return f"Hello, {name.strip()}!"


def add(a: float, b: float) -> float:
    """Return the sum of two numbers.

    Args:
        a: First operand.
        b: Second operand.

    Returns:
        The sum a + b.

    Examples:
        >>> add(1, 2)
        3
        >>> add(0.1, 0.2)
        0.30000000000000004
    """
    return a + b


def is_palindrome(text: str) -> bool:
    """Check whether a string is a palindrome (case-insensitive, ignores spaces).

    Args:
        text: The string to check.

    Returns:
        True if the string is a palindrome, False otherwise.

    Examples:
        >>> is_palindrome("racecar")
        True
        >>> is_palindrome("A man a plan a canal Panama")
        True
        >>> is_palindrome("hello")
        False
    """
    cleaned = "".join(text.lower().split())
    return cleaned == cleaned[::-1]
