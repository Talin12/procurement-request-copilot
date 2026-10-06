"""Output filter for instruction-shaped text.

Detecting an injection is only half the job. If the copilot then quotes the
requester's prose back into its own evidence or recommendation, the instruction
is live again in every downstream reader - a reviewer skim-reading the panel, a
ticket description, or another model consuming the JSON.

So the same patterns the scanner uses are applied once more on the way out, over
every field of the finished decision. Matches are replaced with a visible
marker. The requester's original text is still shown verbatim in the UI's
request panel, clearly labelled as untrusted input; what is filtered is the
copilot's own output.

This runs in code, after the reasoner, so no prompt and no model - hosted or
offline - can opt out of it.
"""
from __future__ import annotations

from src.tools.procurement_tools import _COMPILED

MARKER = "[instruction-like text removed]"


def scrub(text: str | None) -> str | None:
    """Replace instruction-shaped spans in a single string."""
    if not text:
        return text
    cleaned = text
    for _name, pattern in _COMPILED:
        cleaned = pattern.sub(MARKER, cleaned)
    return cleaned


def scrub_all(values: list[str]) -> list[str]:
    return [scrub(v) or v for v in values]
