"""The `Changelog:` / `Before you update:` lines of a PR description (tools/pr_changelog.py)."""
import importlib.util
import sys
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "pr_changelog", Path(__file__).resolve().parents[1] / "tools" / "pr_changelog.py")
pc = importlib.util.module_from_spec(_SPEC)
sys.modules["pr_changelog"] = pc  # dataclasses look the module up while the class is built
_SPEC.loader.exec_module(pc)

TEMPLATE = (Path(__file__).resolve().parents[1] / ".github" / "pull_request_template.md").read_text(encoding="utf-8")


def test_one_line_is_one_entry():
    res = pc.parse("Fix the ring.\n\nChangelog: Bug fixes - The ring no longer goes on for 90 s (#60)\n")
    assert res.entries == [("Bug fixes", "The ring no longer goes on for 90 s (#60)")]
    assert res.problems == []


def test_several_lines_in_several_sections_from_a_bulleted_list():
    """Apeiv in #115: a fix plus a docs update in one PR."""
    body = """Summary.

- Changelog: Bug fixes — The door no longer opens twice over the cloud (#120)
* **Changelog:** documentation – New page on the Echo Show setup
1. Changelog: Other changes: tests for the cloud door
"""
    res = pc.parse(body)
    assert res.entries == [
        ("Bug fixes", "The door no longer opens twice over the cloud (#120)"),
        ("Documentation", "New page on the Echo Show setup"),
        ("Other changes", "tests for the cloud door"),
    ]
    assert res.problems == []


def test_before_you_update_lines_are_collected():
    body = ("Changelog: Other changes - Minimum Home Assistant raised\n"
            "Before you update: Home Assistant 2025.10 or newer is now required\n"
            "- Before you update: restart Home Assistant twice\n")
    res = pc.parse(body)
    assert res.before_you_update == ["Home Assistant 2025.10 or newer is now required", "restart Home Assistant twice"]
    assert res.problems == []


def test_examples_in_a_code_block_do_not_count():
    body = "How to write it:\n\n```\nChangelog: <section> - <text>\n```\n\nChangelog: none\n"
    res = pc.parse(body)
    assert res.none and res.problems == []


def test_none_alone_is_fine():
    res = pc.parse("Only tests.\n\nChangelog: none\n")
    assert res.none and res.entries == [] and res.problems == []


@pytest.mark.parametrize("body, problem", [
    ("", "no 'Changelog:' line"),
    (None, "no 'Changelog:' line"),
    ("Changelog: none\nChangelog: Bug fixes - x", "must be the only"),
    ("Changelog: Fixed - the door", "unknown section"),
    ("Changelog: Bug fixes", "needs '<section>"),
    ("Changelog: none\nBefore you update:", "empty 'Before you update:'"),
])
def test_problems_are_reported(body, problem):
    assert any(problem in p for p in pc.parse(body).problems)


def test_the_templates_examples_do_not_count():
    """The examples sit in an HTML comment: an untouched template must fail the check."""
    res = pc.parse(TEMPLATE)
    assert res.entries == [] and not res.none
    assert res.problems, "the empty 'Changelog: ' placeholder must be filled in"


def test_the_templates_examples_are_valid_once_uncommented():
    res = pc.parse(TEMPLATE.replace("<!--", "").replace("-->", ""))
    assert len(res.entries) >= 2 and res.before_you_update, "the template shows two entries and a note"


def test_main_prints_the_entries_and_fails_on_problems(monkeypatch, capsys):
    monkeypatch.setenv("PR_BODY", "Changelog: Security - Tokens are masked in the logs\n")
    assert pc.main() == 0
    assert "Security: Tokens are masked in the logs" in capsys.readouterr().out
    monkeypatch.setenv("PR_BODY", "Changelog: none\n")
    assert pc.main() == 0
    assert "Changelog: none" in capsys.readouterr().out
    monkeypatch.setenv("PR_BODY", "no line at all")
    assert pc.main() == 1
    assert "::error::" in capsys.readouterr().out
