from __future__ import annotations

import re
from collections.abc import Mapping


_TECHNICAL_TERMS = {
    "api": "API",
    "cli": "CLI",
    "cpu": "CPU",
    "css": "CSS",
    "gpu": "GPU",
    "gui": "GUI",
    "html": "HTML",
    "http": "HTTP",
    "https": "HTTPS",
    "ios": "iOS",
    "javascript": "JavaScript",
    "json": "JSON",
    "mac os": "macOS",
    "powershell": "PowerShell",
    "sql": "SQL",
    "ssh": "SSH",
    "typescript": "TypeScript",
    "uri": "URI",
    "url": "URL",
    "uuid": "UUID",
    "xml": "XML",
    "yaml": "YAML",
}

_DASH = "\ue104"
_DOUBLE_DASH = "\ue105"

_SYMBOLS = (
    ("greater than or equal to", ">="),
    ("less than or equal to", "<="),
    ("open square bracket", "["),
    ("close square bracket", "]"),
    ("open curly brace", "{"),
    ("close curly brace", "}"),
    ("open parenthesis", "("),
    ("close parenthesis", ")"),
    ("open bracket", "["),
    ("close bracket", "]"),
    ("open brace", "{"),
    ("close brace", "}"),
    ("open paren", "("),
    ("close paren", ")"),
    ("strict not equals", "!=="),
    ("strict equals", "==="),
    ("triple equals", "==="),
    ("double equals", "=="),
    ("not equals", "!="),
    ("plus equals", "+="),
    ("minus equals", "-="),
    ("times equals", "*="),
    ("slash equals", "/="),
    ("logical and", "&&"),
    ("logical or", "||"),
    ("fat arrow", "=>"),
    ("scope operator", "::"),
    ("double dash", _DOUBLE_DASH),
    ("dash dash", _DOUBLE_DASH),
    ("forward slash", "/"),
    ("back slash", "\\"),
    ("at sign", "@"),
    ("hash sign", "#"),
    ("pound sign", "#"),
    ("double quote", '"'),
    ("single quote", "'"),
    ("question mark", "?"),
    ("exclamation mark", "!"),
    ("arrow", "->"),
    ("semicolon", ";"),
    ("colon", ":"),
    ("comma", ","),
    ("period", "."),
    ("dot", "."),
    ("equals", "="),
    ("asterisk", "*"),
    ("star", "*"),
    ("plus", "+"),
    ("minus", " - "),
    ("slash", "/"),
    ("backslash", "\\"),
    ("percent", "%"),
    ("pipe", " | "),
    ("ampersand", " & "),
    ("underscore", "_"),
    ("dash", _DASH),
    ("hyphen", _DASH),
    ("backtick", "`"),
)

_NEWLINE = "\ue100"
_INDENT = "\ue101"
_DEDENT = "\ue102"
_TAB = "\ue103"
_STRUCTURED_TOKEN = "ECHOLINKDEVTOKEN{:04d}"
_EXISTING_TOKEN = re.compile(
    r"(?:https?://\S+|www\.\S+|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|"
    r"(?:[A-Za-z]:\\|/)[^\s]+|\b[\w-]+\.[A-Za-z0-9]{1,10}\b|"
    r"\b[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+\b)",
    re.IGNORECASE,
)

_CONTROL_PHRASES = (
    "end file",
    "end case",
    "new paragraph",
    "new line",
    "newline",
    "indent",
    "dedent",
    "tab",
) + tuple(phrase for phrase, _replacement in _SYMBOLS)
_CONTROL_LOOKAHEAD = "|".join(
    re.escape(item) for item in sorted(_CONTROL_PHRASES, key=len, reverse=True)
)
_FILENAME_BOUNDARIES = (
    "end file",
    "new paragraph",
    "new line",
    "newline",
    "indent",
    "dedent",
    "tab",
    "comma",
    "semicolon",
    "equals",
    "double equals",
    "not equals",
    "logical and",
    "logical or",
    "arrow",
    "fat arrow",
    "open paren",
    "open parenthesis",
    "question mark",
    "exclamation mark",
)
_FILENAME_LOOKAHEAD = "|".join(
    re.escape(item) for item in sorted(_FILENAME_BOUNDARIES, key=len, reverse=True)
)

_FILENAME_COMMAND = re.compile(
    rf"(?<!end\s)\b(?P<prefix>(?:(?:tag|at)\s+)?(?:file|filename))\s+"
    rf"(?P<body>.+?)(?=(?:\s+(?:{_FILENAME_LOOKAHEAD})\b|$))",
    re.IGNORECASE,
)
_CASE_COMMAND = re.compile(
    rf"\b(?P<style>camel|snake|pascal|kebab|constant)\s+case\s+"
    rf"(?P<body>.+?)(?=(?:\s+(?:{_CONTROL_LOOKAHEAD})\b|$))",
    re.IGNORECASE,
)


def format_developer_text(text: str, vocabulary: Mapping[str, str] | None = None) -> str:
    """Format explicit developer-mode speech without executing or sending it anywhere."""

    value = " ".join((text or "").strip().split())
    if not value:
        return ""

    value = _apply_vocabulary(value, vocabulary or {})
    structured: list[str] = []

    def protect(replacement: str) -> str:
        structured.append(replacement)
        return _STRUCTURED_TOKEN.format(len(structured) - 1)

    value = _FILENAME_COMMAND.sub(
        lambda match: protect(
            ("@" if match.group("prefix").casefold().startswith(("tag ", "at ")) else "")
            + _format_filename(match.group("body"))
        ),
        value,
    )
    value = re.sub(r"\bend file\b", "", value, flags=re.IGNORECASE)
    value = _CASE_COMMAND.sub(
        lambda match: protect(_format_identifier(match.group("body"), match.group("style"))),
        value,
    )
    value = re.sub(r"\bend case\b", "", value, flags=re.IGNORECASE)
    value = _EXISTING_TOKEN.sub(lambda match: protect(match.group(0)), value)
    value = _apply_technical_terms(value)
    value = _replace_commands(value)
    value = _render_indentation(value)

    for index in range(len(structured) - 1, -1, -1):
        replacement = structured[index]
        value = value.replace(_STRUCTURED_TOKEN.format(index), replacement)
    return value.strip("\n")


def _apply_vocabulary(text: str, vocabulary: Mapping[str, str]) -> str:
    result = text
    for spoken in sorted(vocabulary, key=len, reverse=True):
        pattern = re.compile(rf"(?<!\w){re.escape(spoken)}(?!\w)", re.IGNORECASE)
        result = pattern.sub(lambda _match, item=vocabulary[spoken]: item, result)
    return result


def _apply_technical_terms(text: str) -> str:
    result = text
    for spoken in sorted(_TECHNICAL_TERMS, key=len, reverse=True):
        pattern = re.compile(rf"(?<!\w){re.escape(spoken)}(?!\w)", re.IGNORECASE)
        result = pattern.sub(lambda _match, item=_TECHNICAL_TERMS[spoken]: item, result)
    return result


def _identifier_words(value: str) -> list[str]:
    expanded = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", value.strip())
    expanded = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", expanded)
    return re.findall(r"[A-Za-z0-9]+", expanded)


def _format_identifier(value: str, style: str) -> str:
    words = _identifier_words(value)
    if not words:
        return ""
    style = style.casefold()
    if style == "snake":
        return "_".join(word.casefold() for word in words)
    if style == "kebab":
        return "-".join(word.casefold() for word in words)
    if style == "constant":
        return "_".join(word.upper() for word in words)

    def titled(word: str) -> str:
        canonical = _TECHNICAL_TERMS.get(word.casefold())
        if canonical and canonical.isupper():
            return canonical
        return word[:1].upper() + word[1:].lower()

    if style == "pascal":
        return "".join(titled(word) for word in words)
    return words[0].casefold() + "".join(titled(word) for word in words[1:])


def _format_filename(value: str) -> str:
    result = value.strip()
    replacements = (
        ("forward slash", "/"),
        ("back slash", "\\"),
        ("backslash", "\\"),
        ("slash", "/"),
        ("underscore", "_"),
        ("hyphen", "-"),
        ("dash", "-"),
        ("colon", ":"),
        ("dot", "."),
    )
    explicit_space = "\ue110"
    result = re.sub(r"\bspace\b", explicit_space, result, flags=re.IGNORECASE)
    for phrase, replacement in replacements:
        result = re.sub(
            rf"(?<!\w){re.escape(phrase)}(?!\w)",
            lambda _match, item=replacement: item,
            result,
            flags=re.IGNORECASE,
        )
    result = re.sub(r"\s*([./\\:_-])\s*", r"\1", result)
    result = re.sub(r"\s+", "", result)
    return result.replace(explicit_space, " ")


def _replace_commands(text: str) -> str:
    result = text
    controls = (
        ("new paragraph", _NEWLINE + _NEWLINE),
        ("new line", _NEWLINE),
        ("newline", _NEWLINE),
        ("dedent", _DEDENT),
        ("indent", _INDENT),
        ("tab", _TAB),
    )
    for phrase, replacement in controls + _SYMBOLS:
        result = re.sub(
            rf"(?<!\w){re.escape(phrase)}(?!\w)",
            lambda _match, item=replacement: item,
            result,
            flags=re.IGNORECASE,
        )
    return result


def _render_indentation(text: str) -> str:
    parts = re.split(f"({_NEWLINE}|{_INDENT}|{_DEDENT}|{_TAB})", text)
    lines: list[str] = []
    current = ""
    indent = 0
    for part in parts:
        if not part:
            continue
        if part == _NEWLINE:
            cleaned = _clean_code_line(current)
            lines.append(("    " * indent + cleaned) if cleaned else "")
            current = ""
        elif part == _INDENT:
            indent += 1
        elif part == _DEDENT:
            indent = max(0, indent - 1)
        elif part == _TAB:
            current += "    "
        else:
            current += part
    cleaned = _clean_code_line(current)
    lines.append(("    " * indent + cleaned) if cleaned else "")
    return "\n".join(lines).rstrip()


def _clean_code_line(text: str) -> str:
    value = re.sub(r"[ \t]+", " ", text).strip()
    if not value:
        return ""
    value = re.sub(r"\s*::\s*", "::", value)
    value = re.sub(r"\s*\.\s*", ".", value)
    value = re.sub(r"\s*/\s*", "/", value)
    value = re.sub(r"\s*\\\s*", r"\\", value)
    value = re.sub(r"\s*_\s*", "_", value)
    value = re.sub(rf"\s*{_DOUBLE_DASH}\s*", " --", value)
    value = re.sub(rf"\s*{_DASH}\s*", " -", value)
    value = re.sub(r"\s+([)\]};,:!?])", r"\1", value)
    value = re.sub(r"([([{])\s+", r"\1", value)
    value = re.sub(r"(?<=[A-Za-z0-9_])\s+([([])", r"\1", value)
    value = re.sub(r"\s*(===|!==|==|!=|<=|>=|\+=|-=|\*=|/=|=>|->|&&|\|\||=)\s*", r" \1 ", value)
    value = re.sub(r"\s+([,;])", r"\1", value)
    value = re.sub(r"([,;])(?=\S)", r"\1 ", value)
    value = re.sub(r"([\"'`])\s+", r"\1", value)
    value = re.sub(r"\s+([\"'`])", r"\1", value)
    value = re.sub(r"@\s+", "@", value)
    return re.sub(r"[ \t]+", " ", value).strip()
