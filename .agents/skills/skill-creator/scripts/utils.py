"""Shared utilities for skill-creator scripts."""

import re
import yaml
from pathlib import Path


def parse_skill_md(skill_path: Path) -> tuple[dict, str]:
    """Parse a SKILL.md file, returning (frontmatter_dict, body_str).

    frontmatter_dict contains the parsed YAML fields (e.g. "name",
    "description", "evals", …).  body_str is everything after the closing
    frontmatter delimiter.

    Raises ValueError if the file is missing or has no valid frontmatter.
    """
    skill_file = skill_path / "SKILL.md"
    content = skill_file.read_text()

    match = re.match(r"^---\n(.*?)\n---\n?(.*)", content, re.DOTALL)
    if not match:
        raise ValueError(f"SKILL.md at {skill_file} is missing valid frontmatter (expected opening and closing ---)")

    frontmatter_str, body = match.group(1), match.group(2)
    frontmatter: dict = yaml.safe_load(frontmatter_str) or {}
    return frontmatter, body
