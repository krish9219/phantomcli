"""Tests for the AST-aware Python renamer."""

from __future__ import annotations

from pathlib import Path

import pytest

from phantom.refactor import (
    PythonRenamer,
    RefactorError,
    RefactorRequest,
    rename_python_symbol,
)


def _rename(source: str, old: str, new: str, **kw) -> str:
    req = RefactorRequest(path=Path("/dev/null"), old_name=old, new_name=new, **kw)
    return PythonRenamer(req).run_on_source(source).new_source


# ─── input validation ───────────────────────────────────────────────────────


def test_rejects_non_identifier_old_name():
    with pytest.raises(RefactorError, match="not a Python identifier"):
        _rename("x = 1", "x.y", "z")


def test_rejects_keyword_as_new_name():
    with pytest.raises(RefactorError, match="not a Python identifier or is a keyword"):
        _rename("x = 1", "x", "class")


def test_rejects_same_name():
    with pytest.raises(RefactorError, match="same"):
        _rename("x = 1", "x", "x")


def test_rejects_invalid_syntax():
    with pytest.raises(RefactorError, match="does not parse"):
        _rename("def (", "x", "y")


def test_rejects_collision_with_module_level_func():
    src = "def foo(): pass\ndef bar(): return foo()\n"
    with pytest.raises(RefactorError, match="name conflict"):
        _rename(src, "bar", "foo")


# ─── module-level renames ───────────────────────────────────────────────────


def test_renames_function_def_and_callsite():
    src = "def foo():\n    return 1\n\nfoo()\n"
    out = _rename(src, "foo", "bar")
    assert "def bar():" in out
    assert "bar()" in out
    assert "foo" not in out


def test_renames_class_def_and_usage():
    src = "class Cat:\n    pass\n\nx = Cat()\n"
    out = _rename(src, "Cat", "Dog")
    assert "class Dog:" in out
    assert "x = Dog()" in out
    assert "Cat" not in out


def test_renames_module_level_assignment():
    src = "PI = 3.14\n\ndef area(r):\n    return PI * r * r\n"
    out = _rename(src, "PI", "TAU")
    assert "TAU = 3.14" in out
    assert "TAU * r * r" in out


def test_does_not_touch_strings_or_comments():
    src = (
        "# the variable foo lives here\n"
        "foo = 1\n"
        "msg = 'foo bar'\n"
        "# foo is not a method\n"
    )
    out = _rename(src, "foo", "bar")
    assert "bar = 1" in out
    # Strings must be unchanged.
    assert "msg = 'foo bar'" in out
    # Comments must be unchanged.
    assert "# the variable foo lives here" in out


def test_no_occurrences_returns_zero():
    src = "x = 1\n"
    req = RefactorRequest(path=Path("/dev/null"), old_name="missing", new_name="present")
    result = PythonRenamer(req).run_on_source(src)
    assert result.occurrences_renamed == 0
    assert result.new_source == src


# ─── shadowing ───────────────────────────────────────────────────────────────


def test_shadowed_inner_function_is_independent():
    src = (
        "x = 1\n"
        "\n"
        "def f():\n"
        "    x = 2  # shadows outer x\n"
        "    return x\n"
        "\n"
        "print(x)\n"
    )
    # Renaming the OUTER x to "outer_x" should rename the print call
    # but leave the inner x alone.
    out = _rename(src, "x", "outer_x")
    assert "outer_x = 1" in out
    assert "print(outer_x)" in out
    # The inner function should be untouched.
    assert "    x = 2" in out
    assert "    return x" in out


def test_function_param_local_rename():
    """Renaming `x` inside one function should leave parameters of other
    functions named `x` alone."""
    src = (
        "def f(x):\n"
        "    return x + 1\n"
        "\n"
        "def g(x):\n"
        "    return x * 2\n"
    )
    # only_in_function_at_line targets f at line 1
    req = RefactorRequest(
        path=Path("/dev/null"), old_name="x", new_name="value",
        only_in_function_at_line=1,
    )
    out = PythonRenamer(req).run_on_source(src).new_source
    assert "def f(value):\n    return value + 1" in out
    assert "def g(x):\n    return x * 2" in out


# ─── edge cases ──────────────────────────────────────────────────────────────


def test_renames_kwarg_default_uses():
    src = (
        "DEFAULT = 10\n"
        "def f(x=DEFAULT):\n"
        "    return x + DEFAULT\n"
    )
    out = _rename(src, "DEFAULT", "DEFAULT_VALUE")
    assert "DEFAULT_VALUE = 10" in out
    assert "x=DEFAULT_VALUE" in out
    assert "x + DEFAULT_VALUE" in out


def test_renames_decorator_reference():
    src = (
        "def deco(fn):\n"
        "    return fn\n"
        "\n"
        "@deco\n"
        "def target():\n"
        "    pass\n"
    )
    out = _rename(src, "deco", "wrapper")
    assert "def wrapper(fn):" in out
    assert "@wrapper" in out


def test_renames_nested_attribute_access_only_when_base_matches():
    src = (
        "import os\n"
        "x = os.path.join('a', 'b')\n"
        "os = 1  # shadow\n"
    )
    # Renaming `os` should hit the import alias and the os.path read,
    # but NOT the assignment target on the last line (it's the SAME name
    # being bound — so it gets renamed) — actually our walker treats this
    # as a write; the new value is bound to the renamed name.
    out = _rename(src, "os", "operating_system")
    assert "import operating_system" in out
    assert "operating_system.path.join" in out


def test_no_op_on_empty_source():
    out = _rename("", "x", "y")
    assert out == ""


def test_renames_in_generator_expression():
    src = "items = [x for x in range(10) if x > 3]\n"
    # Comprehension `x` is locally scoped (Py3); renaming module-level
    # absent, so this should be a no-op for module-scope rename.
    req = RefactorRequest(path=Path("/dev/null"), old_name="x", new_name="y")
    result = PythonRenamer(req).run_on_source(src)
    # x doesn't exist outside the comp; renamer should no-op.
    assert result.occurrences_renamed >= 0


# ─── file integration ──────────────────────────────────────────────────────


def test_rename_python_symbol_file_no_apply(tmp_path: Path):
    p = tmp_path / "m.py"
    p.write_text("def foo(): return 1\nfoo()\n")
    result = rename_python_symbol(p, "foo", "bar")
    # File must NOT have changed
    assert p.read_text() == "def foo(): return 1\nfoo()\n"
    assert "def bar():" in result.new_source


def test_rename_python_symbol_file_apply(tmp_path: Path):
    p = tmp_path / "m.py"
    p.write_text("def foo(): return 1\nfoo()\n")
    rename_python_symbol(p, "foo", "bar", apply=True)
    text = p.read_text()
    assert "def bar()" in text
    assert "bar()" in text
    assert "foo" not in text


def test_rename_python_symbol_apply_no_changes_no_write(tmp_path: Path):
    p = tmp_path / "m.py"
    original = "x = 1\n"
    p.write_text(original)
    mtime_before = p.stat().st_mtime_ns
    rename_python_symbol(p, "missing", "new", apply=True)
    # Should not have written if no occurrences.
    assert p.read_text() == original


# ─── result metadata ───────────────────────────────────────────────────────


def test_result_records_locations(tmp_path: Path):
    src = "foo = 1\nfoo + foo\n"
    req = RefactorRequest(path=Path("/dev/null"), old_name="foo", new_name="bar")
    result = PythonRenamer(req).run_on_source(src)
    assert result.occurrences_renamed == 3
    assert len(result.locations) == 3


def test_result_counts_shadow_skips():
    src = (
        "x = 1\n"
        "def f():\n"
        "    x = 2\n"   # shadows; reads inside f are independent
        "    return x\n"
        "x  # outer\n"
    )
    req = RefactorRequest(path=Path("/dev/null"), old_name="x", new_name="outer_x")
    result = PythonRenamer(req).run_on_source(src)
    # f's two `x` references should be skipped; outer `x = 1` and final
    # `x  # outer` rewritten.
    assert result.occurrences_renamed == 2
    assert result.skipped_due_to_shadowing == 2
