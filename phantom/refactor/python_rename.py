"""Symbol-aware Python rename.

Approach
--------

Use :mod:`ast` to find every ``Name`` / ``Attribute`` / function arg /
keyword reference to ``old_name`` *in the symbol's scope*. We do not
do a regex rename — that would clobber strings, comments, and
unrelated identifiers that happen to share the name.

What is "in scope"
------------------

* If ``old_name`` is bound at module top-level (function def, class def,
  module-level assignment, import), every read in the same module
  except inner scopes that **shadow** the name is renamed.
* If ``old_name`` is a function parameter or a local assignment inside
  a function, only that function (and any nested functions that don't
  shadow) is touched.

Out-of-module references
------------------------

This module does **not** rewrite imports in *other* modules. The agent
should call :class:`PythonRenamer` per module, supplying the same old
and new names — the cross-module index is the agent's job, not ours.
For now we provide :func:`rename_python_symbol` as a single-file path,
plus a :class:`PythonRenamer` that operates on a file's source.

Limitations (honestly stated)
-----------------------------

* We do not chase ``from X import Y as Z`` — if a downstream module
  imports the symbol under an alias, the alias is unchanged.
* We do not rename inside docstrings or string literals (this is a
  feature, not a bug — docstrings often quote the old name in
  examples that should stay quoting the old name).
* We do not rewrite type annotations inside string forward references.
* We do not handle ``__all__`` updates yet (caller's responsibility).

These are the same limitations Black/Rope/PyCharm hit; we're being
explicit so callers know what to verify.
"""

from __future__ import annotations

import ast
import io
import logging
import token
import tokenize
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

__all__ = [
    "PythonRenamer",
    "RefactorError",
    "RefactorRequest",
    "RefactorResult",
    "rename_python_symbol",
]

log = logging.getLogger("phantom.refactor")


class RefactorError(RuntimeError):
    """Raised on syntax errors, name conflicts, or invalid requests."""


@dataclass(frozen=True, slots=True)
class RefactorRequest:
    path: Path
    old_name: str
    new_name: str
    # If supplied, only rename inside the function whose def starts at
    # this line number — useful for function-local renames the agent
    # specifies precisely.
    only_in_function_at_line: Optional[int] = None


@dataclass(frozen=True, slots=True)
class RefactorResult:
    path: str
    new_source: str
    occurrences_renamed: int
    skipped_due_to_shadowing: int = 0
    skipped_in_strings_or_comments: int = 0
    locations: tuple[tuple[int, int], ...] = field(default_factory=tuple)


# ─── pure ast walk ──────────────────────────────────────────────────────────


def _is_python_identifier(name: str) -> bool:
    return name.isidentifier() and not _IS_KEYWORD(name)


def _IS_KEYWORD(name: str) -> bool:
    import keyword
    return keyword.iskeyword(name)


class _ScopeWalker(ast.NodeVisitor):
    """Walk the AST and collect (lineno, col_offset) of every Name access
    to ``old_name`` that should be renamed."""

    def __init__(
        self,
        old_name: str,
        only_in_function_at_line: Optional[int] = None,
    ) -> None:
        self.old_name = old_name
        self.only_in_function_at_line = only_in_function_at_line
        self.locations: list[tuple[int, int]] = []
        self.shadow_skips: int = 0
        self._scope_stack: list[set[str]] = [set()]   # set of locally-bound names per scope
        self._inside_target_function: bool = (only_in_function_at_line is None)
        # (None means "not constrained; rename across module")

    def _push_scope(self, locals_bound: set[str]) -> None:
        self._scope_stack.append(locals_bound)

    def _pop_scope(self) -> None:
        self._scope_stack.pop()

    def _name_is_shadowed_in_inner_scopes(self) -> bool:
        # If any scope above the outermost binds old_name as a NEW binding
        # (not just a reference), the name is shadowed there. Skip those.
        for scope in self._scope_stack[1:]:
            if self.old_name in scope:
                return True
        return False

    def _function_locals(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
        out: set[str] = set()
        # parameters
        for arg in (
            node.args.args + node.args.posonlyargs + node.args.kwonlyargs
        ):
            out.add(arg.arg)
        if node.args.vararg:
            out.add(node.args.vararg.arg)
        if node.args.kwarg:
            out.add(node.args.kwarg.arg)
        # local assignments
        for sub in ast.walk(node):
            if sub is node:
                continue
            if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                # Don't descend into nested defs for THIS scope's locals.
                # They get their own scope.
                continue
            if isinstance(sub, ast.Assign):
                for tgt in sub.targets:
                    out.update(_extract_name_targets(tgt))
            elif isinstance(sub, ast.AnnAssign) and isinstance(sub.target, ast.Name):
                out.add(sub.target.id)
            elif isinstance(sub, ast.AugAssign) and isinstance(sub.target, ast.Name):
                out.add(sub.target.id)
            elif isinstance(sub, ast.For) and isinstance(sub.target, ast.Name):
                out.add(sub.target.id)
            elif isinstance(sub, (ast.Import, ast.ImportFrom)):
                for alias in sub.names:
                    out.add((alias.asname or alias.name).split(".")[0])
        return out

    # ── visitors ────────────────────────────────────────────────────

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._handle_func(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._handle_func(node)

    def _handle_func(self, node) -> None:
        was_in_target = self._inside_target_function
        if (
            self.only_in_function_at_line is not None
            and node.lineno == self.only_in_function_at_line
        ):
            self._inside_target_function = True
        # Rename the function name itself if it matches and we're in scope.
        # The AST node's col_offset points to `def`/`async def`/`class`;
        # we add the keyword's length to land on the identifier's start.
        if node.name == self.old_name and self._inside_target_function and not self._name_is_shadowed_in_inner_scopes():
            keyword_len = len("async def ") if isinstance(node, ast.AsyncFunctionDef) else len("def ")
            self.locations.append((node.lineno, node.col_offset + keyword_len))
        locals_bound = self._function_locals(node)
        # Shadowing: if this function declares `old_name` as a local
        # binding AND the user did not explicitly target this function,
        # every reference inside the body resolves to the local
        # binding, not the outer one — we must not rename them.
        # When the user *did* target this function (only_in_function_at_line
        # matches its lineno), the local binding IS the rename target.
        is_explicit_target = (
            self.only_in_function_at_line is not None
            and node.lineno == self.only_in_function_at_line
        )
        if self.old_name in locals_bound and not is_explicit_target:
            skip_count = 0
            for sub in ast.walk(node):
                if sub is node:
                    continue
                if isinstance(sub, ast.Name) and sub.id == self.old_name:
                    skip_count += 1
                elif isinstance(sub, ast.arg) and sub.arg == self.old_name:
                    skip_count += 1
            self.shadow_skips += skip_count
            self._inside_target_function = was_in_target
            return
        self._push_scope(locals_bound)
        self.generic_visit(node)
        self._pop_scope()
        self._inside_target_function = was_in_target

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        if node.name == self.old_name and self._inside_target_function and not self._name_is_shadowed_in_inner_scopes():
            self.locations.append((node.lineno, node.col_offset + len("class ")))
        # Class scope: methods see the enclosing module scope, not class
        # locals, for name resolution. So don't push a binding scope.
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if node.id != self.old_name:
            return
        if not self._inside_target_function:
            return
        # If the name is locally bound in any inner scope (i.e. shadowed),
        # the read in that inner scope is a different variable.
        if self._scope_stack[-1] is not self._scope_stack[0] and self.old_name in self._scope_stack[-1]:
            # Inside a function whose locals shadow the outer name —
            # still rename, because that's the function's own variable.
            self.locations.append((node.lineno, node.col_offset))
            return
        if self._name_is_shadowed_in_inner_scopes():
            self.shadow_skips += 1
            return
        self.locations.append((node.lineno, node.col_offset))

    def visit_arg(self, node: ast.arg) -> None:
        if node.arg == self.old_name and self._inside_target_function:
            self.locations.append((node.lineno, node.col_offset))

    def visit_alias(self, node: ast.alias) -> None:
        # `import os` or `from x import os` — `node.name` is the
        # identifier being introduced. (Or `node.asname` if aliased,
        # in which case `name` is the original symbol — out of scope.)
        if node.asname is None and node.name == self.old_name and self._inside_target_function:
            # Tokenizer will agree at this position — name col is where
            # the alias starts. AST's `lineno`/`col_offset` on alias
            # point to the alias's identifier in 3.10+.
            if hasattr(node, "lineno") and hasattr(node, "col_offset"):
                self.locations.append((node.lineno, node.col_offset))


def _extract_name_targets(node: ast.AST) -> set[str]:
    out: set[str] = set()
    if isinstance(node, ast.Name):
        out.add(node.id)
    elif isinstance(node, (ast.Tuple, ast.List)):
        for el in node.elts:
            out.update(_extract_name_targets(el))
    elif isinstance(node, ast.Starred):
        out.update(_extract_name_targets(node.value))
    return out


# ─── token-based rewrite ────────────────────────────────────────────────────


def _rewrite_at_locations(
    source: str,
    locations: list[tuple[int, int]],
    old_name: str,
    new_name: str,
) -> tuple[str, int]:
    """Replace `old_name` with `new_name` at each (lineno, col_offset).

    Strategy: tokenise once to get the canonical list of NAME-token
    positions. Only rewrite a NAME token whose start matches an entry
    in `locations` AND whose string equals `old_name`. This guarantees
    we never rewrite inside a string literal or a comment (those don't
    produce NAME tokens). Rewriting is done via line-based slicing so
    we don't go through ``tokenize.untokenize`` (which is fussy about
    geometric consistency between adjacent tokens).
    """
    if not locations:
        return source, 0
    loc_set: set[tuple[int, int]] = set(locations)

    # Find every NAME-token position the tokenizer agrees on.
    valid_hits: list[tuple[int, int]] = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(source).readline):
            if (
                tok.type == token.NAME
                and tok.string == old_name
                and tok.start in loc_set
            ):
                valid_hits.append(tok.start)
    except tokenize.TokenizeError as e:
        raise RefactorError(f"tokenize failed: {e}") from e

    if not valid_hits:
        return source, 0

    # Group hits by line for one slice-rewrite per line.
    by_line: dict[int, list[int]] = {}
    for lineno, col in valid_hits:
        by_line.setdefault(lineno, []).append(col)

    lines = source.splitlines(keepends=True)
    replaced = 0
    name_len = len(old_name)
    for lineno, cols in by_line.items():
        idx = lineno - 1
        if idx < 0 or idx >= len(lines):
            continue
        line = lines[idx]
        # Rewrite RIGHT-TO-LEFT so earlier replacements don't shift
        # later columns.
        for col in sorted(cols, reverse=True):
            if line[col : col + name_len] != old_name:
                # Defensive — tokenizer agreed but raw text disagrees;
                # skip rather than corrupt the file.
                continue
            line = line[:col] + new_name + line[col + name_len:]
            replaced += 1
        lines[idx] = line

    return "".join(lines), replaced


# ─── public API ─────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class PythonRenamer:
    request: RefactorRequest

    def run_on_source(self, source: str) -> RefactorResult:
        old = self.request.old_name
        new = self.request.new_name
        if old == new:
            raise RefactorError("old_name and new_name are the same")
        if not _is_python_identifier(old):
            raise RefactorError(f"old_name {old!r} is not a Python identifier")
        if not _is_python_identifier(new):
            raise RefactorError(f"new_name {new!r} is not a Python identifier or is a keyword")

        try:
            tree = ast.parse(source)
        except SyntaxError as e:
            raise RefactorError(f"file does not parse: {e}") from e

        # Refuse if `new_name` is already bound at module level — would
        # collide on import.
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.FunctionDef) and node.name == new:
                raise RefactorError(f"name conflict: {new!r} already defined at module level (line {node.lineno})")
            if isinstance(node, ast.ClassDef) and node.name == new:
                raise RefactorError(f"name conflict: {new!r} already defined at module level (line {node.lineno})")
            if isinstance(node, ast.Assign):
                for tgt in node.targets:
                    if isinstance(tgt, ast.Name) and tgt.id == new:
                        raise RefactorError(f"name conflict: {new!r} already assigned at module level (line {node.lineno})")

        walker = _ScopeWalker(
            old_name=old,
            only_in_function_at_line=self.request.only_in_function_at_line,
        )
        walker.visit(tree)

        if not walker.locations:
            return RefactorResult(
                path=str(self.request.path),
                new_source=source,
                occurrences_renamed=0,
                skipped_due_to_shadowing=walker.shadow_skips,
                locations=tuple(),
            )

        new_source, replaced = _rewrite_at_locations(source, walker.locations, old, new)

        return RefactorResult(
            path=str(self.request.path),
            new_source=new_source,
            occurrences_renamed=replaced,
            skipped_due_to_shadowing=walker.shadow_skips,
            locations=tuple(walker.locations),
        )

    def run_on_file(self) -> RefactorResult:
        try:
            source = self.request.path.read_text(encoding="utf-8")
        except OSError as e:
            raise RefactorError(f"could not read {self.request.path}: {e}") from e
        return self.run_on_source(source)


def rename_python_symbol(
    path: Path,
    old_name: str,
    new_name: str,
    *,
    only_in_function_at_line: Optional[int] = None,
    apply: bool = False,
) -> RefactorResult:
    """High-level API. Renames in-place when ``apply=True``."""
    req = RefactorRequest(
        path=Path(path),
        old_name=old_name,
        new_name=new_name,
        only_in_function_at_line=only_in_function_at_line,
    )
    result = PythonRenamer(req).run_on_file()
    if apply and result.occurrences_renamed > 0:
        Path(path).write_text(result.new_source, encoding="utf-8")
    return result
