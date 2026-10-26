import pytest

from packages.globs import GlobError, GlobSet, compile_glob

# the project of the CLI's `collect.rs` tests
QUARK_FILES = [
    "Classes/Foo.sc",
    "Classes/Nested/Bar.sc",
    "Classes/notes.txt",
    "HelpSource/Classes/Foo.schelp",
    "Root.sc",
    "test.scd",
]


def select(globs: list[str], exclude: tuple[str, ...] = ()) -> list[str]:
    selected, excluded = GlobSet(globs), GlobSet(exclude)
    return [p for p in QUARK_FILES if selected.matches(p) and not excluded.matches(p)]


def test_globstar_with_suffix_spans_folders():
    assert select(["Classes/**.sc", "HelpSource/**/*"]) == [
        "Classes/Foo.sc",
        "Classes/Nested/Bar.sc",
        "HelpSource/Classes/Foo.schelp",
    ]


def test_star_stays_within_folder():
    assert select(["*.sc", "Classes/*.sc"]) == ["Classes/Foo.sc", "Root.sc"]


def test_dot_components_are_dropped():
    assert select(["./Classes/*"]) == ["Classes/Foo.sc", "Classes/notes.txt"]


def test_exclude():
    assert select(["Classes/**"], ("**/*.txt", "Classes/Nested/**")) == [
        "Classes/Foo.sc"
    ]


def test_globstar_alone_matches_everything():
    assert select(["**"]) == QUARK_FILES


@pytest.mark.parametrize(
    "glob, path, expected",
    # results of `globset` 0.4.20 with `literal_separator(true)`
    [
        ("{Classes,HelpSource}/**/*.{sc,schelp}", "Classes/A/Foo.sc", True),
        ("{Classes,HelpSource}/**/*.{sc,schelp}", "HelpSource/Foo.schelp", True),
        ("{Classes,HelpSource}/**/*.{sc,schelp}", "Other/Foo.sc", False),
        ("[!.]*.sc", "Foo.sc", True),
        ("[!.]*.sc", ".Foo.sc", False),
        ("Class**.sc", "Classes.sc", True),
        ("Class**.sc", "Classes/Foo.sc", False),
        ("**/*.scd", "a/b/c.scd", True),
        ("**/*.scd", "c.scd", True),
        ("Classes/**", "Classes", False),
        ("Classes/**", "Classes/a/b", True),
        ("[a-c]?.sc", "b1.sc", True),
        ("\\*.sc", "*.sc", True),
        ("\\*.sc", "a.sc", False),
    ],
)
def test_globset_semantics(glob, path, expected):
    assert bool(compile_glob(glob).fullmatch(path)) == expected


@pytest.mark.parametrize("glob", ["[a", "{a", "a}", "a\\", "[b-a]"])
def test_invalid_glob(glob):
    with pytest.raises(GlobError, match="not a valid glob"):
        compile_glob(glob)
