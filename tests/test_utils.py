"""
tests/test_utils.py - Unit tests for utils.py
"""

import pytest
from utils import greet, add, is_palindrome


# ---------------------------------------------------------------------------
# greet()
# ---------------------------------------------------------------------------

class TestGreet:
    def test_basic(self):
        assert greet("Alice") == "Hello, Alice!"

    def test_strips_whitespace(self):
        assert greet("  Bob  ") == "Hello, Bob!"

    def test_empty_string_raises(self):
        with pytest.raises(ValueError, match="name must not be empty"):
            greet("")

    def test_whitespace_only_raises(self):
        with pytest.raises(ValueError, match="name must not be empty"):
            greet("   ")


# ---------------------------------------------------------------------------
# add()
# ---------------------------------------------------------------------------

class TestAdd:
    def test_integers(self):
        assert add(1, 2) == 3

    def test_floats(self):
        assert add(0.5, 0.5) == pytest.approx(1.0)

    def test_negative(self):
        assert add(-3, 3) == 0

    def test_zeros(self):
        assert add(0, 0) == 0


# ---------------------------------------------------------------------------
# is_palindrome()
# ---------------------------------------------------------------------------

class TestIsPalindrome:
    def test_simple_palindrome(self):
        assert is_palindrome("racecar") is True

    def test_non_palindrome(self):
        assert is_palindrome("hello") is False

    def test_case_insensitive(self):
        assert is_palindrome("RaceCar") is True

    def test_with_spaces(self):
        assert is_palindrome("A man a plan a canal Panama") is True

    def test_empty_string(self):
        assert is_palindrome("") is True

    def test_single_character(self):
        assert is_palindrome("a") is True
