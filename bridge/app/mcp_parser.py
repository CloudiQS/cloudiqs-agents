"""
Shared MCP response parser.

Used by ceo_briefing, ace_hygiene, ace_sync, ace_funding, and any other module
that calls Partner Central MCP and needs clean, human-readable text.

Public API:
  parse_mcp_response(result)               -> str
  parse_pipe_rows(text, expected_fields)   -> list[list[str]]
  strip_narrative(text)                    -> str
  truncate(text, max_chars)               -> str
  extract_facts(text)                      -> list[dict]
"""

import json
import logging
import re

logger = logging.getLogger("bridge")

# Start-of-line narrative phrases — line is dropped if it STARTS with one of these
NARRATIVE_PREFIXES = [
    "I'll help", "I will help", "Let me", "Now let me",
    "I'll analyze", "I will analyze", "I need to", "I can see",
    "I've", "I have ", "I found", "Perfect!", "Great!",
    "Working on it", "Preparing", "Fetching", "Analyzing",
    "Based on my analysis", "Based on my", "Sure!", "Absolutely", "Of course",
    "Here's", "Here is", "Looking at", "Checking", "Searching",
    "I'll ", "I'm ", "I am ",
    "I appreciate", "Would you like", "This request requires",
    "I'm the", "I specialize", "However,", "I can help",
    "Please note", "The deal_progression_advisor",
]

_FILLER_LINES = {
    "sure!", "absolutely!", "of course!", "great question!",
    "great!", "perfect!", "working on it.", "certainly!",
}

# Substrings that disqualify a line when found anywhere within it.
# Used by parse_pipe_rows() to reject chatbot commentary mixed into pipe rows.
_CHATBOT_SUBSTRINGS: list[str] = [
    "I'll ", "Let me ", "I need to", "I appreciate", "Would you like",
    "This request requires", "I'm the ", "I specialize", "However,",
    "I can help", "Please note", "Here's ", "Based on my",
    "I will ", "I am the ", "I have found", "I found that",
    "The deal_progression_advisor", "This request ",
]

# Regex to detect column-header tokens (ALL_CAPS_UNDERSCORE, e.g. OPP_ID, COMPANY)
_HEADER_FIELD_RE = re.compile(r'^[A-Z][A-Z0-9_]+$')


def parse_mcp_response(result: dict) -> str:
    """Extract clean text from an MCP send_message response.

    Handles two layouts:
      1. result['text'] is a JSON string containing {"content": [...]} blocks
         with ASSISTANT_RESPONSE entries (standard send_message response)
      2. result['text'] is already plain text (fallback from older parsers)

    Strips narrative, returns empty string "" on failure.
    """
    if not result:
        return ""
    raw = result.get("text", "")
    if not raw:
        return ""

    # Attempt to parse as JSON (standard MCP send_message response format)
    try:
        inner = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        # Not JSON — treat the raw string as the response text
        return strip_narrative(raw.strip())

    content = inner.get("content", [])
    if not isinstance(content, list):
        # Some responses have a top-level "text" key
        return strip_narrative(str(inner.get("text", raw)).strip())

    parts = []
    for block in content:
        if not isinstance(block, dict):
            continue
        block_type = block.get("type", "")
        if block_type == "ASSISTANT_RESPONSE":
            c = block.get("content", {})
            if isinstance(c, dict):
                text = c.get("text", "")
            elif isinstance(c, str):
                text = c
            else:
                text = ""
            if text:
                parts.append(text)
        elif block_type == "text":
            text = block.get("text", "")
            if text:
                parts.append(text)

    if not parts:
        # No recognised blocks — fall back to raw text
        return strip_narrative(raw.strip())

    return strip_narrative("\n".join(parts).strip())


def parse_pipe_rows(text: str, expected_fields: int) -> list[list[str]]:
    """Parse pipe-delimited data rows from MCP response text.

    Keeps only lines that have exactly `expected_fields` pipe-separated fields,
    are not chatbot commentary, and are not column-header rows.

    Args:
        text:            Raw MCP response text (may contain mixed narrative).
        expected_fields: Number of fields expected per row (e.g. 4 for
                         "OPP_ID | COMPANY | ISSUE | DAYS").

    Returns:
        List of field lists (each field already stripped of whitespace).
        Returns [] if no valid data rows are found.

    Example:
        parse_pipe_rows(
            "Let me check.\\nO123 | Acme | Missing date | 5\\nDone.",
            expected_fields=4,
        )
        # -> [["O123", "Acme", "Missing date", "5"]]
    """
    if not text:
        return []

    rows: list[list[str]] = []
    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue

        # Reject lines containing chatbot substrings (anywhere in the line)
        if any(phrase in stripped for phrase in _CHATBOT_SUBSTRINGS):
            continue

        # Check pipe count — must produce exactly expected_fields parts
        parts = stripped.split("|")
        if len(parts) != expected_fields:
            continue

        # Strip each field
        cleaned = [p.strip() for p in parts]

        # Reject rows with any empty field
        if not all(cleaned):
            continue

        # Reject column-header rows (all fields are ALL_CAPS_UNDERSCORE tokens)
        if all(_HEADER_FIELD_RE.match(f) for f in cleaned):
            continue

        rows.append(cleaned)

    return rows


def strip_narrative(text: str) -> str:
    """Remove MCP thinking-out-loud sentences, keep only data lines."""
    lines = text.split("\n")
    clean = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.lower() in _FILLER_LINES:
            continue
        if any(stripped.startswith(p) for p in NARRATIVE_PREFIXES):
            continue
        clean.append(line)

    # Collapse consecutive blank lines
    result: list[str] = []
    prev_blank = False
    for line in clean:
        is_blank = line.strip() == ""
        if is_blank and prev_blank:
            continue
        result.append(line)
        prev_blank = is_blank

    return "\n".join(result).strip()


def truncate(text: str, max_chars: int = 800) -> str:
    """Truncate text to max_chars, breaking on a newline boundary."""
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars]
    # Try to break on a newline so we do not cut mid-sentence
    nl = cut.rfind("\n")
    if nl > max_chars // 2:
        cut = cut[:nl]
    return cut.rstrip() + "\n... [full report via API]"


def strip_pipe_tables(text: str) -> str:
    """Remove markdown pipe-table rows (lines that start and end with |)."""
    lines = text.split("\n")
    clean = [line for line in lines if not line.strip().startswith("|")]
    return "\n".join(clean).strip()


def extract_facts(text: str) -> list:
    """Extract key:value pairs from text for FactSet display.

    Looks for lines of the form "Key: Value" with at most 100 chars.
    Returns at most 10 facts.
    """
    facts = []
    for line in text.split("\n"):
        line = line.strip()
        if ":" not in line or len(line) > 100:
            continue
        parts = line.split(":", 1)
        key   = parts[0].strip().lstrip("- *#")
        value = parts[1].strip()
        if key and value:
            facts.append({"title": key, "value": value})
        if len(facts) >= 10:
            break
    return facts
