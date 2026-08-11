"""Structural rules that a reviewer would otherwise have to remember.

These assert properties of the source itself rather than runtime behaviour, so
they are deliberately few: only the ones where a second copy or a silent
handler has already cost real debugging time on this project.
"""

from __future__ import annotations

import ast
import hashlib
import pathlib

SOURCE_ROOT = pathlib.Path(__file__).resolve().parent.parent / "src"


def _modules() -> list[pathlib.Path]:
    return sorted(SOURCE_ROOT.rglob("*.py"))


class _Normalize(ast.NodeTransformer):
    """Erase names and constants so only the shape of the code remains."""

    def visit_Name(self, node: ast.Name) -> ast.Name:
        return ast.copy_location(ast.Name(id="_", ctx=node.ctx), node)

    def visit_arg(self, node: ast.arg) -> ast.arg:
        node.arg = "_"
        node.annotation = None
        return node

    def visit_Constant(self, node: ast.Constant) -> ast.Constant:
        return ast.copy_location(ast.Constant(value=None), node)

    def visit_Attribute(self, node: ast.Attribute) -> ast.Attribute:
        self.generic_visit(node)
        node.attr = "_"
        return node


def _shape(node: ast.AST) -> str:
    clone = _Normalize().visit(ast.parse(ast.unparse(node)))
    return hashlib.sha256(ast.dump(clone).encode()).hexdigest()


def test_no_function_body_is_implemented_twice():
    """Renaming a local must not be able to hide a copy, so compare shapes.

    _sha256 and _match_case were each defined identically in two modules. Two
    copies of one rule agree until the day someone edits one of them.
    """
    seen: dict[str, list[str]] = {}
    for path in _modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if sum(1 for _ in ast.walk(node)) < 25:
                continue
            seen.setdefault(_shape(node), []).append(
                f"{path.name}:{node.lineno} {node.name}"
            )

    duplicates = {k: v for k, v in seen.items() if len(v) > 1}

    assert not duplicates, "duplicated function bodies: " + "; ".join(
        " == ".join(sites) for sites in duplicates.values()
    )


def test_no_decorator_is_left_without_the_function_it_decorated():
    """Moving a decorated function is where this breaks.

    Python's AST puts a definition's line number after its decorators, so a
    script that moves lineno..end_lineno takes the body and leaves the
    decorator behind. That happened: three @lru_cache lines stayed in polish.py
    and stacked onto the next function, while the three entity helpers that
    needed them lost their caching. Nothing failed - caching is invisible to a
    correctness test - so only reading the file found it.
    """
    stacked: list[str] = []
    for path in _modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            decorators = getattr(node, "decorator_list", [])
            names = [
                getattr(getattr(d, "func", d), "id", getattr(getattr(d, "func", d), "attr", ""))
                for d in decorators
            ]
            if names.count("lru_cache") > 1:
                stacked.append(f"{path.name}:{node.lineno} {getattr(node, 'name', '?')}")

    assert not stacked, "a function carries more than one lru_cache: " + ", ".join(stacked)


def _broad(handler: ast.ExceptHandler) -> bool:
    node = handler.type
    if node is None:
        return True
    if isinstance(node, ast.Name):
        return node.id in {"Exception", "BaseException"}
    if isinstance(node, ast.Tuple):
        return any(
            isinstance(part, ast.Name) and part.id in {"Exception", "BaseException"}
            for part in node.elts
        )
    return False


def _swallows_silently(handler: ast.ExceptHandler) -> bool:
    """True when the handler does nothing observable with the error.

    Logging, re-raising, changing control flow, or assigning a fallback value
    all count as handling it. What is left is a handler that discards the
    failure and carries on as though it had not happened.
    """
    for node in ast.walk(handler):
        if isinstance(node, (ast.Raise, ast.Return, ast.Continue, ast.Break)):
            return False
        if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
            return False
        if isinstance(node, ast.Call):
            name = getattr(node.func, "attr", getattr(node.func, "id", ""))
            if name in {"debug", "info", "warning", "error", "exception", "critical"}:
                return False
    return True


# Every entry was read individually. Each catches a call whose exception types
# are undocumented (COM, UI Automation, clipboard) inside a loop or a teardown
# where there is nothing useful to do and nothing to report.
KNOWN_SILENT_HANDLERS = {
    # A 20-iteration poll waiting for the clipboard to report the staged text.
    # Retrying is the handling; a log line per attempt would be noise.
    ("insertion.py", "_stage_clipboard"),
    # CoUninitialize during teardown. There is no recovery and no caller left
    # to tell.
    ("transcription.py", "__exit__"),
    # UI Automation range arithmetic that throws when the document shifts under
    # it; the caller falls through to a slower path.
    ("windows_selection.py", "_matching_range"),
    # A focus re-check and a host callback, both of which may vanish mid-poll.
    ("windows_selection.py", "poll_once"),
}


def test_no_new_broad_handler_swallows_an_error_silently():
    """A swallowed error is the most likely place a real defect is hiding.

    The five that exist were each read and justified. This does not forbid a
    broad catch; it forbids adding one that says nothing when it fires.
    """
    found: set[tuple[str, str]] = set()
    for path in _modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for handler in ast.walk(node):
                if isinstance(handler, ast.ExceptHandler) and _broad(handler):
                    if _swallows_silently(handler):
                        found.add((path.name, node.name))

    assert found <= KNOWN_SILENT_HANDLERS, (
        "new silently swallowed exception in "
        + ", ".join(f"{module}:{function}" for module, function in sorted(found - KNOWN_SILENT_HANDLERS))
    )
