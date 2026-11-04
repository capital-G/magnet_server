"""Classes, extensions and methods of sclang class files (`packages.sclang`).
`scripts/sclang_classes_check.py` compares the parser with sclang itself."""

import pytest

from packages.sclang import ParsedMethod, blank, compiled_folder, parse


def methods(source, name=None):
    parsed = parse(source)
    assert not parsed.error
    [cls] = [c for c in parsed.classes if name is None or c.name == name]
    return [(m.name, m.is_class_method) for m in cls.methods]


def test_classes_and_extensions():
    parsed = parse(
        """
        Foo : Bar { }
        Baz { }
        Slotted[slot]: ArrayedCollection { }
        + String { shout { ^this.toUpper } }
        """
    )

    assert not parsed.error
    assert [(c.name, c.superclass, c.is_extension, c.line) for c in parsed.classes] == [
        ("Foo", "Bar", False, 2),
        ("Baz", "", False, 3),
        ("Slotted", "ArrayedCollection", False, 4),
        ("String", "", True, 5),
    ]
    assert parsed.classes[3].methods == [ParsedMethod("shout", False)]


def test_methods():
    assert methods(
        """
        Foo {
            *new { |x| ^super.new.init(x) }
            * other { }
            init { |x| }
            == { |that| ^false }
            * { |that| }
            * + { |that| }
            *== { |that| }
            <> { |that| }
            prPrivate { _Primitive_Name ^this.primitiveFailed }
        }
        """
    ) == [
        ("new", True),
        ("other", True),
        ("init", False),
        ("==", False),
        # `*` alone is the operator
        ("*", False),
        ("+", True),
        # `*==` is one operator, like in sclang
        ("*==", False),
        ("<>", False),
        ("prPrivate", False),
    ]


def test_accessors():
    assert methods(
        """
        Foo {
            classvar <>default, <counter = 0;
            const <pi2 = 6.28, <eps = 1e-3 limit = 10;
            var <a, >b, <>c = #[1, 2, 3], d = [nil, true], <>e = \\sym, f = $;, <>g = "x;y";
            var <>h = -1;
            foo { }
        }
        """
    ) == [
        ("default", True),
        ("default_", True),
        ("counter", True),
        ("pi2", True),
        ("eps", True),
        ("a", False),
        ("b_", False),
        ("c", False),
        ("c_", False),
        ("e", False),
        ("e_", False),
        ("g", False),
        ("g_", False),
        ("h", False),
        ("h_", False),
        ("foo", False),
    ]


def test_a_class_with_variables_only():
    assert methods("Point3 { var <>x, <>y, <>z; }") == [
        ("x", False),
        ("x_", False),
        ("y", False),
        ("y_", False),
        ("z", False),
        ("z_", False),
    ]


def test_braces_in_literals_and_comments():
    assert methods(
        r"""
        // a { in a comment
        /* nested /* comments { */ still a comment } */
        Foo {
            a { ^"}" ++ "\"}" ++ 'sym}' ++ $} ++ $\} ++ ${ }
            b { ^{ #{ 1 } }.value }  // closures
            c { ^"http://example.org/*" }
        }
        """
    ) == [("a", False), ("b", False), ("c", False)]


def test_non_ascii_text_is_ignored():
    # sclang skips characters which aren't printable ASCII
    assert methods("Foo { a { ^1 } … b { } }\n".replace("…", "…")) == [
        ("a", False),
        ("b", False),
    ]


@pytest.mark.parametrize(
    "source, error",
    [
        ("Foo { a { }", "unmatched '{'"),
        ("Foo { } }", "unmatched '}'"),
        ('Foo { a { ^"open } }', "unterminated string"),
        ("Foo { a { } } /* open", "unterminated comment"),
        ("Foo:Bar { }", "expected a class"),
        ("Foo: Bar { }", "expected a class"),
        ("foo { }", "expected a class"),
        ("Foo { a { } var b; }", "unexpected text"),
        ("Foo { a b { } }", "expected a method"),
        ("Foo { } 1 + 2", "unexpected text"),
    ],
)
def test_errors(source, error):
    parsed = parse(source)
    assert error in parsed.error
    assert parsed.classes == []


def test_blank_keeps_positions_and_lines():
    source = 'a /* x\ny */ "s\n" $} b'
    blanked = blank(source)
    assert len(blanked) == len(source)
    assert blanked.split("\n") == ["a     ", "       ", "     b"]


@pytest.mark.parametrize(
    "path, platform, ide, folder",
    [
        ("Classes/Foo.sc", None, None, ""),
        ("Classes/Foo.scd", None, None, None),
        ("Classes/Foo.sc.rtf", None, None, None),
        ("Classes/help/Foo.sc", None, None, None),
        ("Classes/Ignore/Foo.sc", None, None, None),
        ("Classes/.git/Foo.sc", None, None, None),
        # all platforms, or only the host's
        ("Classes/osx/Foo.sc", None, None, "osx"),
        ("Classes/Linux/Foo.sc", None, None, "linux"),
        ("Classes/osx/Foo.sc", "osx", None, "osx"),
        ("Classes/linux/Foo.sc", "osx", None, None),
        # all IDEs, or only the running one
        ("scide_scqt/Foo.sc", None, None, ""),
        ("scide_scqt/Foo.sc", None, "scqt", ""),
        ("scide_scqt/Foo.sc", None, "none", None),
    ],
)
def test_compiled_folder(path, platform, ide, folder):
    assert compiled_folder(path, platform=platform, ide=ide) == folder
