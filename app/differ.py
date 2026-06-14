import difflib


def compute_diff(old_text: str, new_text: str) -> dict:
    old_lines = old_text.splitlines(keepends=True)
    new_lines = new_text.splitlines(keepends=True)

    unified = list(difflib.unified_diff(old_lines, new_lines, fromfile="before", tofile="after", lineterm=""))
    added = [line[1:] for line in unified if line.startswith("+") and not line.startswith("+++")]
    removed = [line[1:] for line in unified if line.startswith("-") and not line.startswith("---")]

    return {
        "unified_diff": "\n".join(unified),
        "added_lines": added,
        "removed_lines": removed,
    }
