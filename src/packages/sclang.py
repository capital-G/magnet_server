"""The classes, class extensions and methods of sclang class files (`.sc`), without
running sclang.

No full parser: comments, strings, symbols and characters are blanked out (they may
contain braces, e.g. `$}` or `"{"`), then a scan over the braces finds the class
headers (before a `{` at depth 0) and the methods (before a `{` at depth 1); method
bodies are skipped. Accessors of variables become methods like in sclang: `<` a
getter `x`, `>` a setter `x_`, `<>` both; those of `classvar` and `const` are class
methods. The syntax follows `lang/LangSource/PyrLexer.cpp` and `Bison/lang11d` of
SuperCollider 3.14; `scripts/sclang_classes_check.py` compares the result with
sclang."""

import re
from dataclasses import dataclass, field

# folders sclang never compiles (`SC_Filesystem::shouldNotCompileDirectory`), lowercase
SKIPPED_FOLDERS = {"help", "ignore", ".svn", ".git", "_darcs"}
# compiled only on their platform (`isNonHostPlatformDirectoryName`), lowercase
PLATFORM_FOLDERS = ("osx", "linux", "windows", "iphone")
# compiled only by the IDE named after the prefix (`isUnusedIdeDirectoryName`)
IDE_FOLDER_PREFIX = "scide_"

BINOP = r"[!@%&*\-+=|<>?/]+"
# `Name`, `Name[slot]`, `Name : Super`, `+ Name`. The `:` follows a space or `]`:
# sclang reads `Name:` as a keyword, so `Foo:Bar` and `Foo: Bar` don't compile.
CLASS_HEADER = re.compile(
    r"(?P<extension>\+\s*)?(?P<name>[A-Z]\w*)(?:\s*\[\s*\w*\s*\])?"
    r"(?:\s*(?<=[\s\]]):\s*(?P<superclass>[A-Z]\w*))?"
)
# `name`, `*name`, `* name`, `==`, `* ==`; `*==` is the operator `*==`
METHOD_HEADER = re.compile(
    rf"(?:(?P<class_method>\*)(?=[\sa-z_]))?\s*(?P<name>[a-z_]\w*|{BINOP})\s*"
)
VARIABLES = re.compile(r"\b(?P<keyword>classvar|var|const)\b(?P<body>[^;]*);")
# a literal value (brackets already removed): `= 1`, `= -1`, `= \a`, `= #`
VALUE = re.compile(r"=\s*-?[^\s,;<>]*")
VARIABLE = re.compile(r"(?P<access><>|<|>)?\s*(?P<name>[a-z_]\w*)")


@dataclass(frozen=True)
class ParsedMethod:
    name: str
    is_class_method: bool


@dataclass
class ParsedClass:
    name: str
    # "" for an extension, and for a class without `: Superclass` (sclang uses Object)
    superclass: str
    is_extension: bool
    line: int
    methods: list[ParsedMethod] = field(default_factory=list)


@dataclass
class ParsedFile:
    classes: list[ParsedClass]
    # why the file doesn't parse; then `classes` is empty (sclang refuses it too)
    error: str = ""


class SclangSyntaxError(ValueError):
    pass


def blank(source: str) -> str:
    """`source` with comments, strings, symbols (`'…'`), characters (`$x`) and
    non-ASCII text replaced by spaces, keeping line breaks (so positions and lines
    stay)."""
    out = list(source)
    i, n = 0, len(source)

    def clear(start: int, end: int) -> None:
        for j in range(start, min(end, n)):
            if out[j] != "\n":
                out[j] = " "

    while i < n:
        if source.startswith("//", i):
            end = source.find("\n", i)
            end = n if end < 0 else end
        elif source.startswith("/*", i):
            # comments nest
            depth, end = 0, i
            while depth or end == i:
                if end >= n:
                    raise SclangSyntaxError(
                        f"unterminated comment at {_line(source, i)}"
                    )
                if source.startswith("/*", end):
                    depth, end = depth + 1, end + 2
                elif source.startswith("*/", end):
                    depth, end = depth - 1, end + 2
                else:
                    end += 1
        elif source[i] in "\"'":
            end = i + 1
            while end < n and source[end] != source[i]:
                end += 2 if source[end] == "\\" else 1
            if end >= n:
                raise SclangSyntaxError(f"unterminated string at {_line(source, i)}")
            end += 1
        elif source[i] == "$":
            # `$x` and `$\x`, any character
            end = i + (3 if source.startswith("$\\", i) else 2)
        else:
            i += 1
            continue
        clear(i, end)
        i = end
    # sclang skips characters which aren't printable ASCII (e.g. a stray `…`)
    return "".join(c if " " <= c <= "~" or c in "\t\n\r\v\f" else " " for c in out)


def _line(source: str, position: int) -> str:
    return f"line {source.count(chr(10), 0, position) + 1}"


def _variables(parsed: ParsedClass, text: str) -> None:
    """Adds the accessors of the variable declarations in `text`."""
    for declaration in VARIABLES.finditer(text):
        body = declaration["body"]
        # array literals (`[1, 2]`, `#[a, b]`) can't hold variables
        while (stripped := re.sub(r"\[[^\[\]]*\]", "", body)) != body:
            body = stripped
        is_class_method = declaration["keyword"] != "var"
        for variable in VARIABLE.finditer(VALUE.sub("", body)):
            access, name = variable["access"] or "", variable["name"]
            if access in ("<", "<>"):
                parsed.methods.append(ParsedMethod(name, is_class_method))
            if access in (">", "<>"):
                parsed.methods.append(ParsedMethod(f"{name}_", is_class_method))


def _parse(source: str) -> list[ParsedClass]:
    text = blank(source)
    classes: list[ParsedClass] = []
    depth, start = 0, 0
    # the class being read, and whether a method of it was found yet (variables
    # come before the methods)
    current: ParsedClass | None = None
    has_methods = False
    for brace in re.finditer(r"[{}]", text):
        position = brace.start()
        # the text since the previous brace at depth 0 or 1
        before = text[start:position]
        if brace[0] == "{" and depth == 0:
            header = CLASS_HEADER.fullmatch(before.strip())
            if header is None:
                raise SclangSyntaxError(
                    f"expected a class or a class extension at {_line(text, position)}"
                )
            line = source.count("\n", 0, start + len(before) - len(before.lstrip())) + 1
            current = ParsedClass(
                header["name"],
                header["superclass"] or "",
                bool(header["extension"]),
                line,
            )
            classes.append(current)
            has_methods = False
        elif brace[0] == "{" and depth == 1:
            assert current is not None
            if not has_methods:
                _variables(current, before)
                before = VARIABLES.sub("", before)
            method = METHOD_HEADER.fullmatch(before.strip())
            if method is None:
                raise SclangSyntaxError(f"expected a method at {_line(text, position)}")
            current.methods.append(
                ParsedMethod(method["name"], bool(method["class_method"]))
            )
            has_methods = True
        elif brace[0] == "}" and depth == 0:
            raise SclangSyntaxError(f"unmatched '}}' at {_line(text, position)}")
        elif brace[0] == "}" and depth == 1:
            # the end of a class: nothing after the last method, or only variables
            assert current is not None
            if not has_methods:
                _variables(current, before)
                before = VARIABLES.sub("", before)
            if before.strip():
                raise SclangSyntaxError(f"unexpected text at {_line(text, start)}")
        depth += 1 if brace[0] == "{" else -1
        if depth <= 1:
            start = position + 1
    if depth:
        raise SclangSyntaxError("unmatched '{' at the end of the file")
    if text[start:].strip():
        raise SclangSyntaxError(f"unexpected text at {_line(text, start)}")
    return classes


def parse(source: str) -> ParsedFile:
    """The classes and extensions of a class file, or why it doesn't parse."""
    try:
        return ParsedFile(_parse(source))
    except SclangSyntaxError as e:
        return ParsedFile([], str(e))


def compiled_folder(path: str, *, platform: str | None, ide: str | None) -> str | None:
    """Whether sclang compiles the class file `path` (relative, `/`-separated): `None`
    if it doesn't, else the platform folder it's in (`osx`, `linux`, … or `""`).

    `platform` is the host's folder name, `None` for all platforms; `ide` the IDE
    whose `scide_<ide>` folders get compiled, `None` for all."""
    *folders, name = path.split("/")
    if not name.endswith(".sc"):
        return None
    platform_folder = ""
    for folder in (f.lower() for f in folders):
        if folder in SKIPPED_FOLDERS:
            return None
        if (
            ide is not None
            and folder.startswith(IDE_FOLDER_PREFIX)
            and len(folder) > len(IDE_FOLDER_PREFIX)
            and folder[len(IDE_FOLDER_PREFIX) :] != ide
        ):
            return None
        if folder in PLATFORM_FOLDERS:
            if platform is not None and folder != platform:
                return None
            platform_folder = platform_folder or folder
    return platform_folder
