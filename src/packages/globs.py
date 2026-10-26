"""The globs of a manifest's `[artifact]` table, matched like the CLI does
(`../magnet_cli/src/packaging/collect.rs: glob_builder`), which uses the Rust crate
`globset` 0.4 with `literal_separator(true)`. This is a port of its parser
(`globset/src/glob.rs`), so the server selects exactly the files the CLI packages:

- paths and globs are `/`-separated and relative to the project root
- `?`, `*` and `[…]`/`[!…]` match within a path component (`[…]` can match a `/`,
  like in `globset`), `{a,b}` is an alternation, `\\` escapes the next character
- a component `**` matches zero or more components (`**/x`, `a/**/x`, `a/**`, `**`),
  elsewhere `**` is a `*`
- added by the CLI: `.` components are dropped, and a component starting with `**`
  followed by more text means `**/*<rest>`, e.g. `Classes/**.sc` = `Classes/**/*.sc`
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass, field

# regex fragments of globset's tokens with `literal_separator`
ANY = "[^/]"
ZERO_OR_MORE = "[^/]*"
RECURSIVE_PREFIX = "(?:/?|.*/)"
RECURSIVE_SUFFIX = "/.*"
RECURSIVE_ZERO_OR_MORE = "(?:/|/.*/)"


class GlobError(ValueError):
    pass


@dataclass
class _Alternates:
    branches: list[list[str]]


@dataclass
class _Parser:
    glob: str
    position: int = 0
    prev: str | None = None
    cur: str | None = None
    # tokens (regex fragments or `_Alternates`) of the active branches;
    # tokens get added to the last one
    branches: list[list] = field(default_factory=lambda: [[]])
    alternates_stack: list[int] = field(default_factory=list)

    def bump(self) -> str | None:
        self.prev = self.cur
        if self.position < len(self.glob):
            self.cur = self.glob[self.position]
            self.position += 1
        else:
            self.cur = None
        return self.cur

    def peek(self) -> str | None:
        return self.glob[self.position] if self.position < len(self.glob) else None

    def push(self, token) -> None:
        self.branches[-1].append(token)

    def parse(self) -> list:
        while (c := self.bump()) is not None:
            if c == "?":
                self.push(ANY)
            elif c == "*":
                self.parse_star()
            elif c == "[":
                self.parse_class()
            elif c == "{":
                self.alternates_stack.append(len(self.branches))
                self.branches.append([])
            elif c == "}":
                if not self.alternates_stack:
                    raise GlobError("unopened alternation")
                start = self.alternates_stack.pop()
                alternates = _Alternates(self.branches[start:])
                del self.branches[start:]
                self.push(alternates)
            elif c == "," and self.alternates_stack:
                self.branches.append([])
            elif c == "\\":
                if (escaped := self.bump()) is None:
                    raise GlobError("dangling escape")
                self.push(re.escape(escaped))
            else:
                self.push(re.escape(c))
        if self.alternates_stack:
            raise GlobError("unclosed alternation")
        return self.branches[0]

    def parse_star(self) -> None:
        prev = self.prev
        if self.peek() != "*":
            self.push(ZERO_OR_MORE)
            return
        self.bump()
        if not self.branches[-1]:
            if self.peek() not in (None, "/"):
                self.push(ZERO_OR_MORE)
            else:
                self.push(RECURSIVE_PREFIX)
                self.bump()
            return

        in_alternates = len(self.branches) > 1
        if prev != "/" and (not in_alternates or prev not in (",", "{")):
            self.push(ZERO_OR_MORE)
            return
        next_char = self.peek()
        if next_char is None:
            self.bump()
            is_suffix = True
        elif next_char in (",", "}") and in_alternates:
            is_suffix = True
        elif next_char == "/":
            self.bump()
            is_suffix = False
        else:
            self.push(ZERO_OR_MORE)
            return
        # the `/` before `**` is part of the recursive token
        last = self.branches[-1].pop()
        if last in (RECURSIVE_PREFIX, RECURSIVE_SUFFIX):
            self.push(last)
        else:
            self.push(RECURSIVE_SUFFIX if is_suffix else RECURSIVE_ZERO_OR_MORE)

    def parse_class(self) -> None:
        negated = False
        if self.peek() in ("!", "^"):
            self.bump()
            negated = True
        ranges: list[list[str]] = []
        first, in_range = True, False
        while True:
            if (c := self.bump()) is None:
                raise GlobError("unclosed character class")
            if c == "]" and not first:
                break
            if c == "-" and not first:
                if in_range:
                    self.add_to_last_range(ranges, "-")
                    in_range = False
                else:
                    in_range = True
            elif in_range:
                self.add_to_last_range(ranges, c)
                in_range = False
            else:
                ranges.append([c, c])
            first = False
        if in_range:
            ranges.append(["-", "-"])
        body = "".join(
            re.escape(start) if start == end else f"{re.escape(start)}-{re.escape(end)}"
            for start, end in ranges
        )
        self.push(f"[{'^' if negated else ''}{body}]")

    def add_to_last_range(self, ranges: list[list[str]], end: str) -> None:
        ranges[-1][1] = end
        if ranges[-1][1] < ranges[-1][0]:
            raise GlobError(f"invalid range {ranges[-1][0]}-{end}")


def _to_regex(tokens: list) -> str:
    parts = []
    for token in tokens:
        if isinstance(token, _Alternates):
            # empty alternatives are dropped, like globset does by default
            alternatives = [r for b in token.branches if (r := _to_regex(b))]
            if alternatives:
                parts.append(f"(?:{'|'.join(alternatives)})")
        else:
            parts.append(token)
    return "".join(parts)


def expand(glob: str) -> str:
    """The CLI's rewrite of a glob before `globset` parses it."""
    components = []
    for component in glob.split("/"):
        if component == ".":
            continue
        rest = component.removeprefix("**")
        if component.startswith("**") and rest and not rest.startswith("*"):
            component = f"**/*{rest}"
        components.append(component)
    return "/".join(components)


def compile_globset_glob(glob: str) -> re.Pattern[str]:
    """The pattern of a glob as `globset` compiles it, to be used with `fullmatch`."""
    tokens = _Parser(glob).parse()
    if tokens == [RECURSIVE_PREFIX]:
        # the glob `**` matches everything
        return re.compile(".*")
    return re.compile(_to_regex(tokens))


def compile_glob(glob: str) -> re.Pattern[str]:
    """The pattern of a manifest glob, to be used with `fullmatch`."""
    try:
        return compile_globset_glob(expand(glob))
    except GlobError as e:
        raise GlobError(f"`{glob}` is not a valid glob: {e}") from e


class GlobSet:
    def __init__(self, globs: Iterable[str]):
        self.patterns = [compile_glob(glob) for glob in globs]

    def matches(self, path: str) -> bool:
        return any(pattern.fullmatch(path) for pattern in self.patterns)
