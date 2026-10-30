"""Prompt files and data blocks.

Each prompt file starts with a `version: <id>` line; the version is recorded on
every LLM span (ADR 0014). Retrieved text always goes inside a data block, and
the prompts say its content is data, never instructions (ADR 0013).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Prompt:
    name: str
    version: str
    text: str


def load_prompt(name: str, prompts_dir: Path) -> Prompt:
    raw = (prompts_dir / f"{name}.md").read_text(encoding="utf-8")
    first, _, body = raw.partition("\n")
    if not first.startswith("version: "):
        raise ValueError(f"prompts/{name}.md must start with a 'version: ' line")
    return Prompt(name=name, version=first.removeprefix("version: ").strip(), text=body.strip())


def data_block(tag: str, text: str, **attrs: str) -> str:
    """Wrap untrusted text in a tag. A closing tag inside the text is neutralised so it can't end the block early."""
    safe = text.replace(f"</{tag}>", f"[/{tag}]")
    attributes = "".join(f' {k}="{v}"' for k, v in attrs.items())
    return f"<{tag}{attributes}>\n{safe}\n</{tag}>"
