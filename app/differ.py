import difflib
import re


def compute_diff(old_text: str, new_text: str) -> dict:
    old_lines = old_text.splitlines(keepends=True)
    new_lines = new_text.splitlines(keepends=True)

    unified = list(difflib.unified_diff(old_lines, new_lines, fromfile="before", tofile="after", lineterm=""))
    added = [line[1:] for line in unified if line.startswith("+") and not line.startswith("+++")]
    removed = [line[1:] for line in unified if line.startswith("-") and not line.startswith("---")]

    structured = _build_structured_diff(old_lines, new_lines)
    additions = sum(1 for s in structured if s["type"] == "added")
    deletions = sum(1 for s in structured if s["type"] == "removed")
    changes = sum(1 for s in structured if s["type"] == "changed")

    return {
        "unified_diff": "\n".join(unified),
        "added_lines": added,
        "removed_lines": removed,
        "structured_diff": structured,
        "stats": {"additions": additions, "deletions": deletions, "changes": changes},
    }


def _build_structured_diff(old_lines: list[str], new_lines: list[str]) -> list[dict]:
    sm = difflib.SequenceMatcher(None, old_lines, new_lines)
    result = []

    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for idx in range(i2 - i1):
                result.append({
                    "type": "context",
                    "old_line_no": i1 + idx + 1,
                    "new_line_no": j1 + idx + 1,
                    "old_text": old_lines[i1 + idx].rstrip("\n"),
                    "new_text": new_lines[j1 + idx].rstrip("\n"),
                    "word_diff": [],
                })
        elif tag == "replace":
            old_chunk = old_lines[i1:i2]
            new_chunk = new_lines[j1:j2]
            pairs = list(zip(old_chunk, new_chunk))
            for idx, (ol, nl) in enumerate(pairs):
                result.append({
                    "type": "changed",
                    "old_line_no": i1 + idx + 1,
                    "new_line_no": j1 + idx + 1,
                    "old_text": ol.rstrip("\n"),
                    "new_text": nl.rstrip("\n"),
                    "word_diff": _word_diff(ol.rstrip("\n"), nl.rstrip("\n")),
                })
            if len(old_chunk) > len(new_chunk):
                for idx in range(len(new_chunk), len(old_chunk)):
                    result.append({
                        "type": "removed",
                        "old_line_no": i1 + idx + 1,
                        "new_line_no": None,
                        "old_text": old_chunk[idx].rstrip("\n"),
                        "new_text": "",
                        "word_diff": [],
                    })
            elif len(new_chunk) > len(old_chunk):
                for idx in range(len(old_chunk), len(new_chunk)):
                    result.append({
                        "type": "added",
                        "old_line_no": None,
                        "new_line_no": j1 + idx + 1,
                        "old_text": "",
                        "new_text": new_chunk[idx].rstrip("\n"),
                        "word_diff": [],
                    })
        elif tag == "delete":
            for idx in range(i2 - i1):
                result.append({
                    "type": "removed",
                    "old_line_no": i1 + idx + 1,
                    "new_line_no": None,
                    "old_text": old_lines[i1 + idx].rstrip("\n"),
                    "new_text": "",
                    "word_diff": [],
                })
        elif tag == "insert":
            for idx in range(j2 - j1):
                result.append({
                    "type": "added",
                    "old_line_no": None,
                    "new_line_no": j1 + idx + 1,
                    "old_text": "",
                    "new_text": new_lines[j1 + idx].rstrip("\n"),
                    "word_diff": [],
                })

    return result


def _word_diff(old_line: str, new_line: str) -> list[dict]:
    old_words = _tokenize(old_line)
    new_words = _tokenize(new_line)

    sm = difflib.SequenceMatcher(None, old_words, new_words)
    result = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            result.append({"type": "equal", "value": "".join(old_words[i1:i2])})
        elif tag == "replace":
            result.append({"type": "delete", "value": "".join(old_words[i1:i2])})
            result.append({"type": "insert", "value": "".join(new_words[j1:j2])})
        elif tag == "delete":
            result.append({"type": "delete", "value": "".join(old_words[i1:i2])})
        elif tag == "insert":
            result.append({"type": "insert", "value": "".join(new_words[j1:j2])})
    return result


def _tokenize(text: str) -> list[str]:
    return re.findall(r'\S+|\s+', text)
