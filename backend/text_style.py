"""Text clean-up shared by AI-written, customer-facing text."""

import re


def replace_dashes(text: str) -> str:
    """Replace em/en dashes, which models use freely despite being asked not to."""
    text = re.sub(r"(\d)\s*[–—]\s*(\d)", r"\1-\2", text)  # 10–14 -> 10-14
    text = re.sub(r"(\w)–(\w)", r"\1 to \2", text)  # April–September -> April to September
    text = re.sub(r"\s*[—–]\s*", ", ", text)
    return re.sub(r",\s*([,.;:!?])", r"\1", text)  # "word, ." left by a dash before punctuation
