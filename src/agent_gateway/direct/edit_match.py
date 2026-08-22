"""9-layer fuzzy text matching for file edits.

Ported from OpenCode's edit tool. Tries progressively looser matching
strategies to handle imprecise LLM output while still being accurate.

Layers (from strictest to loosest):
1. Exact match
2. Line-trimmed match
3. Whitespace-normalized match
4. Indentation-flexible match
5. Escape-normalized match
6. Trimmed-boundary match
7. Block-anchor match (first+last lines as anchors)
8. Context-aware match (first+last lines + 50% middle)
9. Multi-occurrence exact match
"""

from __future__ import annotations

import re
import unicodedata
from typing import Iterator


def _normalize_whitespace(s: str) -> str:
    """Collapse all whitespace runs to single space, strip."""
    return re.sub(r"\s+", " ", s).strip()


def _remove_indentation(s: str) -> str:
    """Remove common leading whitespace from all lines."""
    lines = s.split("\n")
    if not lines:
        return s
    # Find minimum indentation (ignoring empty lines)
    min_indent = len(s)
    for line in lines:
        if line.strip():
            indent = len(line) - len(line.lstrip())
            min_indent = min(min_indent, indent)
    if min_indent == 0 or min_indent == len(s):
        return s
    return "\n".join(line[min_indent:] if line.strip() else "" for line in lines)


def _normalize_escapes(s: str) -> str:
    """Unescape common escape sequences."""
    replacements = [
        ("\\n", "\n"),
        ("\\t", "\t"),
        ("\\\\", "\\"),
        ('\\"', '"'),
        ("\\'", "'"),
    ]
    result = s
    for old, new in replacements:
        result = result.replace(old, new)
    return result


def _levenshtein_ratio(a: str, b: str) -> float:
    """Compute similarity ratio between two strings (0.0 to 1.0)."""
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0

    len_a, len_b = len(a), len(b)
    # Use O(min(n,m)) space
    if len_a < len_b:
        a, b = b, a
        len_a, len_b = len_b, len_a

    previous = list(range(len_b + 1))
    for i, ca in enumerate(a):
        current = [i + 1]
        for j, cb in enumerate(b):
            cost = 0 if ca == cb else 1
            current.append(
                min(
                    previous[j + 1] + 1,      # deletion
                    current[j] + 1,            # insertion
                    previous[j] + cost,        # substitution
                )
            )
        previous = current

    distance = previous[len_b]
    max_len = max(len_a, len_b)
    return 1.0 - (distance / max_len) if max_len > 0 else 1.0


def _is_disproportionate(old_string: str, found: str) -> bool:
    """Check if the found match is disproportionately larger than expected."""
    old_lines = old_string.count("\n") + 1
    found_lines = found.count("\n") + 1
    if found_lines >= max(old_lines + 3, old_lines * 2):
        return True
    if old_lines == 1:
        return False
    return len(found.strip()) > max(len(old_string.strip()) + 500, len(old_string.strip()) * 4)


# ---------------------------------------------------------------------------
# Layer implementations
# ---------------------------------------------------------------------------


def _layer_exact(content: str, old_string: str) -> Iterator[str]:
    """Layer 1: Exact string match."""
    idx = content.find(old_string)
    if idx != -1:
        yield old_string


def _layer_line_trimmed(content: str, old_string: str) -> Iterator[str]:
    """Layer 2: Trim each line before matching."""
    old_lines = old_string.split("\n")
    trimmed_old = "\n".join(line.strip() for line in old_lines)
    content_lines = content.split("\n")

    # Build trimmed content for matching
    trimmed_content = "\n".join(line.strip() for line in content_lines)
    idx = trimmed_content.find(trimmed_old)
    if idx == -1:
        return

    # Map back to original content
    char_count = 0
    for i, line in enumerate(content_lines):
        trimmed_line = line.strip()
        if char_count == idx and trimmed_old in "\n".join(
            l.strip() for l in content_lines[i:]
        ):
            # Found start, now find end
            remaining = trimmed_old
            end_idx = i
            chars_needed = len(trimmed_old)
            current_pos = 0
            for j in range(i, len(content_lines)):
                tl = content_lines[j].strip()
                if current_pos + len(tl) + (1 if current_pos > 0 else 0) <= chars_needed + 10:
                    end_idx = j
                    current_pos += len(tl) + 1
                    if current_pos >= chars_needed:
                        break
                else:
                    break
            result = "\n".join(content_lines[i : end_idx + 1])
            if result.strip() == trimmed_old:
                yield result
            return
        char_count += len(line) + 1


def _layer_whitespace_normalized(content: str, old_string: str) -> Iterator[str]:
    """Layer 3: Normalize whitespace (collapse \\s+ to single space)."""
    normalized_old = _normalize_whitespace(old_string)
    normalized_content = _normalize_whitespace(content)
    idx = normalized_content.find(normalized_old)
    if idx == -1:
        return

    # Map back to original content by finding lines that match
    content_lines = content.split("\n")
    old_lines = old_string.split("\n")

    # Try to find a block where normalized lines match
    for start in range(len(content_lines)):
        for end in range(start + 1, min(start + len(old_lines) + 5, len(content_lines) + 1)):
            candidate = "\n".join(content_lines[start:end])
            if _normalize_whitespace(candidate) == normalized_old:
                if not _is_disproportionate(old_string, candidate):
                    yield candidate
                    return


def _layer_indentation_flexible(content: str, old_string: str) -> Iterator[str]:
    """Layer 4: Remove common indentation before matching."""
    stripped_old = _remove_indentation(old_string)
    content_lines = content.split("\n")

    for start in range(len(content_lines)):
        for end in range(start + 1, min(start + len(content_lines) + 1, len(content_lines) + 1)):
            candidate = "\n".join(content_lines[start:end])
            stripped_candidate = _remove_indentation(candidate)
            if stripped_candidate == stripped_old:
                if not _is_disproportionate(old_string, candidate):
                    yield candidate
                    return


def _layer_escape_normalized(content: str, old_string: str) -> Iterator[str]:
    """Layer 5: Normalize escape sequences before matching."""
    normalized_old = _normalize_escapes(old_string)
    normalized_content = _normalize_escapes(content)

    idx = normalized_content.find(normalized_old)
    if idx == -1:
        return

    # Find the corresponding region in original content
    # Count characters up to idx in normalized content
    orig_pos = 0
    norm_pos = 0
    while norm_pos < idx and orig_pos < len(content):
        norm_pos += 1
        orig_pos += 1

    # Find the end
    end_pos = orig_pos
    norm_end = norm_pos
    old_len = len(normalized_old)
    while norm_end - norm_pos < old_len and end_pos < len(content):
        norm_end += 1
        end_pos += 1

    candidate = content[orig_pos:end_pos]
    if _normalize_escapes(candidate) == normalized_old:
        if not _is_disproportionate(old_string, candidate):
            yield candidate


def _layer_trimmed_boundary(content: str, old_string: str) -> Iterator[str]:
    """Layer 6: Trim leading/trailing whitespace of block."""
    trimmed_old = old_string.strip()
    content_lines = content.split("\n")

    for start in range(len(content_lines)):
        for end in range(start + 1, min(start + len(content_lines) + 1, len(content_lines) + 1)):
            candidate = "\n".join(content_lines[start:end])
            if candidate.strip() == trimmed_old:
                if not _is_disproportionate(old_string, candidate):
                    yield candidate
                    return


def _layer_block_anchor(content: str, old_string: str) -> Iterator[str]:
    """Layer 7: Match first+last lines as anchors, verify middle with fuzzy match."""
    old_lines = old_string.strip().split("\n")
    if len(old_lines) < 2:
        return

    first_line = old_lines[0].strip()
    last_line = old_lines[-1].strip()
    middle_lines = [l.strip() for l in old_lines[1:-1]]

    content_lines = content.split("\n")

    # Find all candidates where first+last lines match
    candidates = []
    for start in range(len(content_lines)):
        if content_lines[start].strip() != first_line:
            continue
        for end in range(start + 1, len(content_lines)):
            if content_lines[end].strip() != last_line:
                continue
            candidate_lines = [l.strip() for l in content_lines[start + 1 : end]]
            if not middle_lines or not candidate_lines:
                continue

            # Compute average Levenshtein similarity for middle lines
            if len(middle_lines) == len(candidate_lines):
                similarities = [
                    _levenshtein_ratio(m, c) for m, c in zip(middle_lines, candidate_lines)
                ]
                avg_similarity = sum(similarities) / len(similarities)
                threshold = 0.65 if len(candidates) == 0 else 0.65
                if avg_similarity >= threshold:
                    candidates.append((start, end, avg_similarity))

    if not candidates:
        return

    # Pick best match
    candidates.sort(key=lambda x: x[2], reverse=True)
    best_start, best_end, _ = candidates[0]
    result = "\n".join(content_lines[best_start : best_end + 1])
    if not _is_disproportionate(old_string, result):
        yield result


def _layer_context_aware(content: str, old_string: str) -> Iterator[str]:
    """Layer 8: Match first+last lines, check 50% middle-line match."""
    old_lines = old_string.strip().split("\n")
    if len(old_lines) < 2:
        return

    first_line = old_lines[0].strip()
    last_line = old_lines[-1].strip()
    middle_lines = [l.strip() for l in old_lines[1:-1]]

    content_lines = content.split("\n")

    for start in range(len(content_lines)):
        if content_lines[start].strip() != first_line:
            continue
        for end in range(start + 1, len(content_lines)):
            if content_lines[end].strip() != last_line:
                continue
            candidate_lines = [l.strip() for l in content_lines[start + 1 : end]]
            if not middle_lines:
                result = "\n".join(content_lines[start : end + 1])
                if not _is_disproportionate(old_string, result):
                    yield result
                return
            if not candidate_lines:
                continue

            # Check if at least 50% of middle lines match exactly
            matches = sum(1 for m, c in zip(middle_lines, candidate_lines) if m == c)
            match_ratio = matches / max(len(middle_lines), len(candidate_lines))
            if match_ratio >= 0.5:
                result = "\n".join(content_lines[start : end + 1])
                if not _is_disproportionate(old_string, result):
                    yield result
                return


def _layer_multi_occurrence(content: str, old_string: str) -> Iterator[str]:
    """Layer 9: Yield all exact occurrences."""
    start = 0
    while True:
        idx = content.find(old_string, start)
        if idx == -1:
            break
        yield old_string
        start = idx + len(old_string)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

ALL_REPLACERS = [
    _layer_exact,
    _layer_line_trimmed,
    _layer_whitespace_normalized,
    _layer_indentation_flexible,
    _layer_escape_normalized,
    _layer_trimmed_boundary,
    _layer_block_anchor,
    _layer_context_aware,
    _layer_multi_occurrence,
]


def find_match(
    content: str,
    old_string: str,
) -> str | None:
    """Find the best matching text in content for old_string.

    Tries 9 progressively looser matching strategies. Returns the matched
    text from content, or None if no match found.
    """
    for replacer in ALL_REPLACERS:
        for candidate in replacer(content, old_string):
            if not _is_disproportionate(old_string, candidate):
                return candidate
    return None


def replace_text(
    content: str,
    old_string: str,
    new_string: str,
    replace_all: bool = False,
) -> tuple[str, int, str]:
    """Replace old_string with new_string in content using 9-layer matching.

    Returns (new_content, replacement_count, match_type).
    """
    if replace_all:
        # Try each layer and return first one that finds matches
        for i, replacer in enumerate(ALL_REPLACERS):
            matches = list(replacer(content, old_string))
            # Filter out disproportionate matches
            valid = [m for m in matches if not _is_disproportionate(old_string, m)]
            if valid:
                unique_matches = list(dict.fromkeys(valid))  # preserve order, dedupe
                new_content = content
                total_replaced = 0
                for match in unique_matches:
                    count = new_content.count(match)
                    total_replaced += count
                    new_content = new_content.replace(match, new_string)
                return new_content, total_replaced, f"layer_{i + 1}"
        return content, 0, "no_match"

    # Single replacement — try each layer
    for i, replacer in enumerate(ALL_REPLACERS):
        for candidate in replacer(content, old_string):
            if _is_disproportionate(old_string, candidate):
                continue
            # Check for multiple occurrences
            idx = content.find(candidate)
            last_idx = content.rfind(candidate)
            if idx != last_idx:
                # Multiple occurrences — skip to next layer
                continue
            new_content = content[:idx] + new_string + content[idx + len(candidate):]
            return new_content, 1, f"layer_{i + 1}"

    return content, 0, "no_match"
