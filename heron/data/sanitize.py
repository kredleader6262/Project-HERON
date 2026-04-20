"""Adversarial input sanitization for scraped text.

All scraped text (news, filings, PDFs) is treated as adversarial.
Strip HTML, control chars, zero-width chars, prompt injection patterns.
See Project-HERON.md Section 4.1.1.
"""

import html
import re

# Zero-width and invisible Unicode characters
_INVISIBLE_RE = re.compile(
    "[\u200b\u200c\u200d\u200e\u200f"   # zero-width spaces, joiners, direction marks
    "\u2060\u2061\u2062\u2063\u2064"     # word joiner, invisible operators
    "\ufeff"                              # BOM / zero-width no-break space
    "\u00ad"                              # soft hyphen
    "\u034f"                              # combining grapheme joiner
    "\u061c"                              # Arabic letter mark
    "\u115f\u1160"                        # Hangul fillers
    "\u17b4\u17b5"                        # Khmer vowel inherent
    "\u180e"                              # Mongolian vowel separator
    "\uffa0"                              # halfwidth Hangul filler
    "\ufff0-\ufff8"                       # specials
    "]+"
)

# HTML tags
_HTML_TAG_RE = re.compile(r"<[^>]+>")

# Control characters (keep \n \t)
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# Collapse whitespace runs (but preserve newlines)
_MULTI_SPACE_RE = re.compile(r"[^\S\n]+")
_MULTI_NEWLINE_RE = re.compile(r"\n{3,}")


def sanitize(text):
    """Clean untrusted text for safe downstream use."""
    if not text:
        return ""

    # Strip HTML tags FIRST (before unescaping, so &lt;tag&gt; doesn't become <tag> and get stripped)
    text = _HTML_TAG_RE.sub("", text)

    # Decode HTML entities
    text = html.unescape(text)

    # Remove invisible Unicode
    text = _INVISIBLE_RE.sub("", text)

    # Remove control characters
    text = _CONTROL_RE.sub("", text)

    # Normalize whitespace
    text = _MULTI_SPACE_RE.sub(" ", text)
    text = _MULTI_NEWLINE_RE.sub("\n\n", text)

    return text.strip()


def sanitize_headline(text):
    """Stricter sanitization for headlines — single line, length-capped."""
    s = sanitize(text)
    s = s.replace("\n", " ").strip()
    return s[:500] if s else ""
