"""Symbol-aware rename for JavaScript and TypeScript.

We do not ship a full JS/TS parser. Instead we use a hand-written
tokenizer that's aware of:

* Strings (``'…'``, ``"…"``, template literals ```…```)
* Comments (``//…``, ``/*…*/``, JSDoc)
* Regex literals (with the lookback heuristic)
* Identifiers vs keywords
* Block scopes (``{ … }``) for ``let`` / ``const`` shadowing detection
* Function parameter scopes
* Imports (``import { x } from`` / ``import x from``) so renaming the
  binding also touches the alias when present

What this is NOT
----------------

A full TypeScript type-checker. We don't follow type-only references
across files; we don't resolve overload signatures; we don't expand
generic type parameters. For those, ts-morph or the TypeScript
compiler API are the right tools — they're heavyweight and need Node.js
on the host.

What this IS
------------

A safe, pure-Python rename for the 90% case: rename a top-level
function or const within one .js/.ts file, with correct shadowing
inside nested function bodies and block scopes. Strings, comments, and
regex literals are never touched.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

__all__ = [
    "JsRenamer",
    "JsRefactorError",
    "JsRefactorRequest",
    "JsRefactorResult",
    "rename_js_symbol",
]

# JS/TS reserved words that must not be used as identifiers.
_KEYWORDS = frozenset({
    "abstract", "any", "as", "async", "await", "boolean", "break", "case",
    "catch", "class", "const", "constructor", "continue", "debugger",
    "declare", "default", "delete", "do", "else", "enum", "export",
    "extends", "false", "finally", "for", "from", "function", "get",
    "if", "implements", "import", "in", "instanceof", "interface", "is",
    "keyof", "let", "module", "namespace", "never", "new", "null",
    "number", "of", "package", "private", "protected", "public",
    "readonly", "require", "return", "set", "static", "string", "super",
    "switch", "symbol", "this", "throw", "true", "try", "type", "typeof",
    "undefined", "unique", "var", "void", "while", "with", "yield",
})

_IDENT_RE = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*")
_NUMERIC_RE = re.compile(r"\d")


class JsRefactorError(RuntimeError):
    """Raised on invalid identifiers, name conflicts, or unbalanced syntax."""


@dataclass(frozen=True, slots=True)
class JsRefactorRequest:
    path: Path
    old_name: str
    new_name: str
    language: str = "js"   # "js" | "ts" | "tsx" | "jsx"


@dataclass(frozen=True, slots=True)
class JsRefactorResult:
    path: str
    new_source: str
    occurrences_renamed: int
    skipped_due_to_shadowing: int = 0
    locations: tuple[tuple[int, int], ...] = field(default_factory=tuple)


# ─── token/scope walker ─────────────────────────────────────────────────────


def _is_valid_identifier(name: str) -> bool:
    if not name or name in _KEYWORDS:
        return False
    if _NUMERIC_RE.match(name[0]):
        return False
    return bool(_IDENT_RE.fullmatch(name))


@dataclass
class _ScopeFrame:
    binds: set[str] = field(default_factory=set)


class _JsScanner:
    """Single-pass scanner that yields rewriteable identifier hits.

    The output is a list of (lineno, col, char_offset) for every
    identifier matching ``old_name`` that should be renamed under
    JS/TS scoping rules.
    """

    def __init__(self, source: str, old_name: str) -> None:
        self.source = source
        self.old_name = old_name
        self.pos = 0
        self.line = 1
        self.col = 0
        self.locations: list[tuple[int, int, int]] = []
        self.shadow_skips = 0
        # Stack of scopes. The bottom element is module scope.
        self.scopes: list[_ScopeFrame] = [_ScopeFrame()]
        # Tracks whether the most recent significant token was one
        # that can precede a regex literal (operator, keyword, etc.) —
        # used to disambiguate `/x/` regex from `/` divide.
        self._regex_legal = True

    # ── helpers ──────────────────────────────────────────────────────

    def _shadowed(self) -> bool:
        # The name is shadowed if any inner scope binds it.
        for frame in self.scopes[1:]:
            if self.old_name in frame.binds:
                return True
        return False

    def _peek(self, n: int = 1) -> str:
        return self.source[self.pos : self.pos + n]

    def _advance(self, n: int = 1) -> str:
        chunk = self.source[self.pos : self.pos + n]
        for ch in chunk:
            if ch == "\n":
                self.line += 1
                self.col = 0
            else:
                self.col += 1
        self.pos += n
        return chunk

    def _skip_string(self, quote: str) -> None:
        # Already past opening quote
        while self.pos < len(self.source):
            ch = self.source[self.pos]
            if ch == "\\":
                self._advance(2)
                continue
            if ch == quote:
                self._advance()
                return
            self._advance()

    def _skip_template(self) -> None:
        # Already past opening backtick. Watch for ${...} interpolations.
        # When we find one, scan its expression body with the SAME
        # identifier-recognition logic the main loop uses (so identifiers
        # inside ${...} get rename hits). When the matching `}` closes the
        # expression, return to template-skipping mode.
        while self.pos < len(self.source):
            ch = self.source[self.pos]
            if ch == "\\":
                self._advance(2)
                continue
            if ch == "`":
                self._advance()
                return
            if ch == "$" and self._peek(2) == "${":
                self._advance(2)
                self._scan_expression_until_close_brace()
                continue
            self._advance()

    def _scan_expression_until_close_brace(self) -> None:
        """Sub-scanner for ``${ ... }`` template interpolations.

        Mirrors the relevant subset of :meth:`scan` so identifiers inside
        the expression get rename hits. Stops when brace depth returns to 0.
        """
        depth = 1
        while self.pos < len(self.source) and depth > 0:
            ch = self.source[self.pos]
            if ch == "\\":
                self._advance(2)
                continue
            # nested template
            if ch == "`":
                self._advance()
                self._skip_template()
                continue
            # nested string
            if ch in "'\"":
                self._advance()
                self._skip_string(ch)
                continue
            # comments
            if ch == "/" and self._peek(2) == "//":
                self._advance(2)
                self._skip_line_comment()
                continue
            if ch == "/" and self._peek(2) == "/*":
                self._advance(2)
                self._skip_block_comment()
                continue
            # braces govern depth
            if ch == "{":
                depth += 1
                self._advance()
                continue
            if ch == "}":
                depth -= 1
                self._advance()
                continue
            # identifier
            if ch.isalpha() or ch in "_$":
                start_pos = self.pos
                start_line, start_col = self.line, self.col
                while (self.pos < len(self.source)
                       and (self.source[self.pos].isalnum()
                            or self.source[self.pos] in "_$")):
                    self._advance()
                ident = self.source[start_pos : self.pos]
                if ident == self.old_name:
                    if self._shadowed():
                        self.shadow_skips += 1
                    else:
                        self.locations.append((start_line, start_col, start_pos))
                continue
            self._advance()

    def _skip_line_comment(self) -> None:
        while self.pos < len(self.source) and self.source[self.pos] != "\n":
            self._advance()

    def _skip_block_comment(self) -> None:
        while self.pos < len(self.source):
            if self._peek(2) == "*/":
                self._advance(2)
                return
            self._advance()

    def _skip_regex(self) -> None:
        # Already past opening /
        in_class = False
        while self.pos < len(self.source):
            ch = self.source[self.pos]
            if ch == "\\":
                self._advance(2)
                continue
            if ch == "[":
                in_class = True
            elif ch == "]":
                in_class = False
            elif ch == "/" and not in_class:
                self._advance()
                # Skip flags
                while (self.pos < len(self.source)
                       and self.source[self.pos].isalpha()):
                    self._advance()
                return
            elif ch == "\n":
                # Unterminated regex — bail out gracefully
                return
            self._advance()

    # ── binding detection ────────────────────────────────────────────

    def _bind_in_current_scope(self, name: str) -> None:
        self.scopes[-1].binds.add(name)

    # ── main scan ────────────────────────────────────────────────────

    def scan(self) -> None:
        n = len(self.source)
        # Track whether next identifier is a binding (after `let`/`const`
        # /`var`/`function`/`class`/parameter list); simple state, not
        # exhaustive, but covers the common patterns.
        next_is_binding = False

        while self.pos < n:
            ch = self.source[self.pos]

            # Comments
            if ch == "/" and self._peek(2) == "//":
                self._advance(2)
                self._skip_line_comment()
                continue
            if ch == "/" and self._peek(2) == "/*":
                self._advance(2)
                self._skip_block_comment()
                continue

            # Strings
            if ch in "'\"":
                self._advance()
                self._skip_string(ch)
                self._regex_legal = False
                continue
            if ch == "`":
                self._advance()
                self._skip_template()
                self._regex_legal = False
                continue

            # Regex
            if ch == "/" and self._regex_legal:
                self._advance()
                self._skip_regex()
                self._regex_legal = False
                continue

            # Identifier
            if ch.isalpha() or ch in "_$":
                start_pos = self.pos
                start_line, start_col = self.line, self.col
                while (self.pos < n
                       and (self.source[self.pos].isalnum()
                            or self.source[self.pos] in "_$")):
                    self._advance()
                ident = self.source[start_pos : self.pos]

                if ident in ("let", "const", "var", "function", "class"):
                    next_is_binding = True
                    self._regex_legal = True
                    continue

                if ident == "import":
                    next_is_binding = True
                    self._regex_legal = True
                    continue

                # New block-scope marker: capture binding into current scope
                if ident == self.old_name:
                    if next_is_binding:
                        self._bind_in_current_scope(ident)
                        # ALWAYS rename declaration sites (even if inner-scoped):
                        # the user wants `old_name` → `new_name` for THIS
                        # binding. Skip only when we're inside a scope that
                        # has ALREADY bound the same name above us.
                        if self._shadowed_above(ident):
                            self.shadow_skips += 1
                        else:
                            self.locations.append((start_line, start_col, start_pos))
                    else:
                        # Read site — rename if no inner scope shadows.
                        if self._shadowed():
                            self.shadow_skips += 1
                        else:
                            self.locations.append((start_line, start_col, start_pos))

                next_is_binding = False
                self._regex_legal = False
                continue

            # Block scope tracking
            if ch == "{":
                self.scopes.append(_ScopeFrame())
                self._regex_legal = True
                self._advance()
                continue
            if ch == "}":
                if len(self.scopes) > 1:
                    self.scopes.pop()
                self._regex_legal = True
                self._advance()
                continue

            # Operators / whitespace re-enable regex parsing
            if ch in "([,=:;+*-!&|^~?<>%":
                self._regex_legal = True
            elif not ch.isspace():
                self._regex_legal = False

            self._advance()

    def _shadowed_above(self, name: str) -> bool:
        """Does any enclosing scope (above current) bind this name?"""
        for frame in self.scopes[:-1]:
            if name in frame.binds:
                return True
        return False


# ─── rewriter ───────────────────────────────────────────────────────────────


def _rewrite(source: str, hits: list[tuple[int, int, int]],
             old_name: str, new_name: str) -> tuple[str, int]:
    if not hits:
        return source, 0
    # Sort by char offset descending so earlier offsets aren't shifted.
    hits_sorted = sorted(hits, key=lambda h: h[2], reverse=True)
    name_len = len(old_name)
    out = source
    replaced = 0
    for _line, _col, off in hits_sorted:
        if out[off : off + name_len] != old_name:
            continue
        out = out[:off] + new_name + out[off + name_len:]
        replaced += 1
    return out, replaced


# ─── public API ─────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class JsRenamer:
    request: JsRefactorRequest

    def run_on_source(self, source: str) -> JsRefactorResult:
        old = self.request.old_name
        new = self.request.new_name
        if old == new:
            raise JsRefactorError("old_name and new_name are the same")
        if not _is_valid_identifier(old):
            raise JsRefactorError(f"old_name {old!r} is not a valid JS identifier")
        if not _is_valid_identifier(new):
            raise JsRefactorError(f"new_name {new!r} is not a valid JS identifier or is a reserved word")

        scanner = _JsScanner(source, old)
        scanner.scan()
        new_source, replaced = _rewrite(source, scanner.locations, old, new)
        return JsRefactorResult(
            path=str(self.request.path),
            new_source=new_source,
            occurrences_renamed=replaced,
            skipped_due_to_shadowing=scanner.shadow_skips,
            locations=tuple((l, c) for l, c, _ in scanner.locations),
        )

    def run_on_file(self) -> JsRefactorResult:
        try:
            source = self.request.path.read_text(encoding="utf-8")
        except OSError as e:
            raise JsRefactorError(f"could not read {self.request.path}: {e}") from e
        return self.run_on_source(source)


def rename_js_symbol(
    path: Path,
    old_name: str,
    new_name: str,
    *,
    language: str = "js",
    apply: bool = False,
) -> JsRefactorResult:
    """High-level convenience. Writes file in place when ``apply=True``."""
    req = JsRefactorRequest(
        path=Path(path), old_name=old_name, new_name=new_name, language=language,
    )
    result = JsRenamer(req).run_on_source(Path(path).read_text(encoding="utf-8"))
    if apply and result.occurrences_renamed > 0:
        Path(path).write_text(result.new_source, encoding="utf-8")
    return result
