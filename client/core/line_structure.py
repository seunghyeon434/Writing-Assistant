from __future__ import annotations


def preserve_blank_lines(original: str, replacement: str) -> str:
    """Reinsert blank-line positions from original when content line counts match."""
    original_text = str(original or "")
    replacement_text = str(replacement or "")
    if not original_text or not replacement_text:
        return replacement_text

    original_newline = _dominant_newline(original_text)
    original_lines = _normalize_newlines(original_text).split("\n")
    replacement_lines = _normalize_newlines(replacement_text).split("\n")

    if not any(_is_blank(line) for line in original_lines):
        return replacement_text

    original_content_count = sum(1 for line in original_lines if not _is_blank(line))
    replacement_content_lines = [line for line in replacement_lines if not _is_blank(line)]
    if original_content_count != len(replacement_content_lines):
        return replacement_text

    content_index = 0
    restored_lines: list[str] = []
    for original_line in original_lines:
        if _is_blank(original_line):
            restored_lines.append("")
            continue
        restored_lines.append(replacement_content_lines[content_index])
        content_index += 1

    restored = original_newline.join(restored_lines)
    if original_text.endswith(("\r\n", "\n", "\r")) and not restored.endswith(original_newline):
        restored += original_newline
    return restored


def _normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _dominant_newline(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def _is_blank(line: str) -> bool:
    return not line.strip()

def preserve_replacement_structure(original: str, replacement: str) -> str:
    """Preserve the original line layout when a replacement result is structurally compatible.

    The temporary local analyzer can sometimes produce a single visible result line for
    a multi-line source. For source replacement that would collapse Word/HWP
    paragraphs, which also destroys line-by-line style mapping. Only repair that
    single-line case when it looks like the temporary feature marker was appended.
    """
    restored = preserve_blank_lines(original, replacement)
    if restored != str(replacement or ""):
        return restored
    return _restore_collapsed_marked_lines(original, replacement)


def _restore_collapsed_marked_lines(original: str, replacement: str) -> str:
    original_text = str(original or "")
    replacement_text = str(replacement or "")
    if not original_text or not replacement_text:
        return replacement_text

    original_newline = _dominant_newline(original_text)
    original_lines = _normalize_newlines(original_text).split("\n")
    replacement_lines = _normalize_newlines(replacement_text).split("\n")
    original_content_lines = [line for line in original_lines if not _is_blank(line)]
    replacement_content_lines = [line for line in replacement_lines if not _is_blank(line)]

    if len(original_content_lines) <= 1 or len(replacement_content_lines) != 1:
        return replacement_text

    replacement_line = replacement_content_lines[0]
    suffix = _temporary_marker_suffix(replacement_line, original_content_lines)
    if not suffix:
        return replacement_text

    restored_lines = list(original_lines)
    for index in range(len(restored_lines) - 1, -1, -1):
        if not _is_blank(restored_lines[index]):
            restored_lines[index] = restored_lines[index].rstrip() + suffix
            break

    restored = original_newline.join(restored_lines)
    if original_text.endswith(("\r\n", "\n", "\r")) and not restored.endswith(original_newline):
        restored += original_newline
    return restored


def _temporary_marker_suffix(replacement_line: str, original_content_lines: list[str]) -> str:
    marker_start = replacement_line.rfind(" [")
    if marker_start < 0 or not replacement_line.rstrip().endswith("]"):
        return ""

    marker_suffix = replacement_line[marker_start:]
    for original_line in original_content_lines:
        content = original_line.rstrip()
        if content and replacement_line.startswith(content):
            suffix = replacement_line[len(content):]
            if suffix.startswith(" [") and suffix.rstrip().endswith("]"):
                return suffix
    return marker_suffix

