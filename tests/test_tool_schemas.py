"""Tests for tool_schemas.validate — covers every built-in tool."""
from __future__ import annotations

import pytest

from omnicli.tool_schemas import validate, TOOL_SCHEMAS


class TestRunBashSchema:
    def test_valid(self):
        ok, err = validate("run_bash", {"command": "ls -la"})
        assert ok, err
        assert err == ""

    def test_missing_command(self):
        ok, err = validate("run_bash", {})
        assert not ok
        assert "command" in err

    def test_empty_command_rejected(self):
        ok, err = validate("run_bash", {"command": ""})
        assert not ok
        assert "command" in err.lower() or "minlength" in err.lower()

    def test_wrong_type_rejected(self):
        ok, err = validate("run_bash", {"command": 42})
        assert not ok
        assert "command" in err.lower()

    def test_extra_args_allowed(self):
        ok, err = validate("run_bash", {"command": "ls", "explanation": "listing"})
        assert ok, err


class TestBrowseUrlSchema:
    def test_valid_https(self):
        ok, err = validate("browse_url", {"url": "https://example.com/page"})
        assert ok, err

    def test_valid_http(self):
        ok, err = validate("browse_url", {"url": "http://example.com"})
        assert ok, err

    def test_non_http_scheme_rejected(self):
        ok, err = validate("browse_url", {"url": "file:///etc/passwd"})
        assert not ok
        assert "url" in err.lower()

    def test_missing_url(self):
        ok, err = validate("browse_url", {})
        assert not ok


class TestWebSearchSchema:
    def test_valid(self):
        ok, err = validate("web_search", {"query": "phantom cli", "max_results": 5})
        assert ok, err

    def test_stringified_max_results_accepted(self):
        # Models sometimes stringify integers — we tolerate it.
        ok, err = validate("web_search", {"query": "x", "max_results": "10"})
        assert ok, err

    def test_max_results_over_limit_rejected(self):
        ok, err = validate("web_search", {"query": "x", "max_results": 999})
        assert not ok

    def test_missing_query(self):
        ok, err = validate("web_search", {"max_results": 5})
        assert not ok


class TestWriteFileSchema:
    def test_valid(self):
        ok, err = validate("write_file", {"path": "/tmp/x.txt", "content": "hi"})
        assert ok, err

    def test_missing_content_allowed_as_empty_string(self):
        ok, err = validate("write_file", {"path": "/tmp/x.txt", "content": ""})
        # empty content is intentionally allowed — "touch"-style creation
        assert ok, err

    def test_missing_path_rejected(self):
        ok, err = validate("write_file", {"content": "hi"})
        assert not ok
        assert "path" in err.lower()

    def test_missing_content_rejected(self):
        ok, err = validate("write_file", {"path": "/tmp/x.txt"})
        assert not ok
        assert "content" in err.lower()


class TestEditFileSchema:
    def test_valid(self):
        ok, err = validate("edit_file", {
            "path": "/tmp/x.txt",
            "old_text": "foo",
            "new_text": "bar",
        })
        assert ok, err

    def test_empty_old_text_rejected(self):
        # Edit with empty old_text could replace the whole file — require non-empty
        ok, err = validate("edit_file", {
            "path": "/tmp/x.txt", "old_text": "", "new_text": "bar",
        })
        assert not ok


class TestReadFileSchema:
    def test_valid(self):
        ok, err = validate("read_file", {"path": "/tmp/x.txt"})
        assert ok, err

    def test_missing_path(self):
        ok, err = validate("read_file", {})
        assert not ok


class TestPlanTasksSchema:
    def test_list_form_valid(self):
        ok, err = validate("plan_tasks", {"tasks": ["a", "b", "c"]})
        assert ok, err

    def test_string_form_valid(self):
        ok, err = validate("plan_tasks", {"tasks": "a\nb\nc"})
        assert ok, err

    def test_empty_args_ok(self):
        # plan_tasks has no required field; empty is fine.
        ok, err = validate("plan_tasks", {})
        assert ok, err


class TestValidatorBehavior:
    def test_unknown_tool_passes(self):
        ok, err = validate("some_unknown_tool", {"foo": "bar"})
        assert ok, err

    def test_non_dict_args_rejected(self):
        ok, err = validate("run_bash", "ls -la")
        assert not ok
        assert "object" in err.lower() or "dict" in err.lower()

    def test_error_message_is_model_friendly(self):
        ok, err = validate("run_bash", {})
        assert not ok
        # Error should be short, structured, and name the tool.
        assert "run_bash" in err
        assert "INVALID_TOOL_ARGS" in err

    def test_all_schemas_have_additional_properties_true(self):
        """Permissive by design — extra model fields shouldn't break dispatch."""
        for name, schema in TOOL_SCHEMAS.items():
            assert schema.get("additionalProperties") is True, f"{name} is not permissive"
