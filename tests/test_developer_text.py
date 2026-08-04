from __future__ import annotations

import ast
from pathlib import Path

import pytest

from echo_link_core.developer_text import format_developer_text


@pytest.mark.parametrize(
    ("spoken", "expected"),
    (
        ("camel case user name", "userName"),
        ("camel case user id", "userId"),
        ("snake case user name", "user_name"),
        ("pascal case api response", "APIResponse"),
        ("pascal case json api client", "JSONAPIClient"),
        ("kebab case feature branch", "feature-branch"),
        ("constant case max retry count", "MAX_RETRY_COUNT"),
        ("constant case api key", "API_KEY"),
    ),
)
def test_explicit_identifier_case_commands(spoken, expected):
    assert format_developer_text(spoken) == expected


@pytest.mark.parametrize(
    ("spoken", "expected"),
    (
        ("filename main dot py", "main.py"),
        ("file read me dot md", "readme.md"),
        ("tag file app slash routes dot ts", "@app/routes.ts"),
        ("at filename source slash core slash engine dot py", "@source/core/engine.py"),
        (
            "filename C colon backslash Work backslash main dot py",
            "C:\\Work\\main.py",
        ),
        ("filename My space File dot MD", "My File.MD"),
        (
            "open filename main dot py end file comma then edit it",
            "open main.py, then edit it",
        ),
    ),
)
def test_spoken_filename_and_file_tag_commands(spoken, expected):
    assert format_developer_text(spoken) == expected


def test_case_commands_compose_with_syntax_and_indentation():
    spoken = (
        "def camel case get user open paren snake case user id close paren colon "
        "new line indent return snake case user id"
    )
    assert format_developer_text(spoken) == "def getUser(user_id):\n    return user_id"


def test_nested_indent_and_dedent_are_deterministic():
    spoken = (
        "root new line indent child new line indent grandchild new line "
        "dedent sibling new line dedent done"
    )
    assert format_developer_text(spoken) == (
        "root\n    child\n        grandchild\n    sibling\ndone"
    )


@pytest.mark.parametrize(
    ("spoken", "expected"),
    (
        ("git status dash dash short", "git status --short"),
        ("python dash m pytest", "python -m pytest"),
        ("npm run build pipe tee build dot log", "npm run build | tee build.log"),
        (
            "git checkout dash b filename feature slash api",
            "git checkout -b feature/api",
        ),
        ("a minus b", "a - b"),
    ),
)
def test_cli_and_operator_spacing(spoken, expected):
    assert format_developer_text(spoken) == expected


@pytest.mark.parametrize(
    ("spoken", "expected"),
    (
        (
            "list name equals open bracket single quote alpha single quote comma "
            "single quote beta single quote close bracket",
            "list name = ['alpha','beta']",
        ),
        (
            "dictionary equals open brace double quote id double quote colon "
            "camel case user id close brace",
            'dictionary = {"id": userId}',
        ),
        (
            "if enabled logical and ready colon new line indent return true new line "
            "dedent return false",
            "if enabled && ready:\n    return true\nreturn false",
        ),
        (
            "namespace scope operator class name arrow method open paren close paren",
            "namespace::class name -> method()",
        ),
    ),
)
def test_core_spoken_syntax_symbols(spoken, expected):
    assert format_developer_text(spoken) == expected


def test_developer_vocabulary_is_local_and_user_extendable():
    vocabulary = {
        "echo link client": "EchoLinkClient",
        "project config": "echo_link.toml",
    }
    assert format_developer_text("call echo link client", vocabulary) == "call EchoLinkClient"
    assert format_developer_text("open file project config", vocabulary) == "open echo_link.toml"
    assert format_developer_text("json api uses sql", vocabulary) == "JSON API uses SQL"


def test_existing_sensitive_tokens_are_preserved_exactly():
    samples = (
        "open https://example.com/api/v1",
        r"open C:\Work\api.json",
        "send jane.doe@example.com",
        "use api.client",
    )
    for sample in samples:
        assert format_developer_text(sample) == sample


def test_ordinary_developer_prose_stays_conservative():
    assert format_developer_text("please review this change") == "please review this change"
    assert format_developer_text("hello world") == "hello world"
    assert format_developer_text("foo foo bar") == "foo foo bar"


def test_formatter_has_no_network_process_or_execution_imports():
    module_path = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "echo_link_core"
        / "developer_text.py"
    )
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    imported_roots = {
        alias.name.split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported_roots.update(
        (node.module or "").split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    )
    assert imported_roots.isdisjoint(
        {"socket", "subprocess", "urllib", "requests", "http", "ftplib"}
    )
