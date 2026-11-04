"""Compares `packages.sclang` with what sclang itself compiles.

Runs sclang with only the given folders as class library (`excludeDefaultPaths`),
dumps `Class.allClasses` (superclass, instance and class methods) and compares it
with the parser's result for the same files, merged over files like sclang does.
Run from `src/`:

    uv run python ../scripts/sclang_classes_check.py \\
        /Applications/SuperCollider-3.14.1.app/Contents/Resources/SCClassLibrary [quark …]

`MAGNET_SCLANG` overrides the sclang binary (default: the 3.14.1 app). Quarks need
SuperCollider's class library as first folder, since they extend its classes."""

import json
import os
import subprocess
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from packages.sclang import compiled_folder, parse  # noqa: E402

SCLANG = os.environ.get(
    "MAGNET_SCLANG", "/Applications/SuperCollider-3.14.1.app/Contents/MacOS/sclang"
)
PLATFORM = {"darwin": "osx", "linux": "linux", "win32": "windows"}[sys.platform]

DUMP = r"""
var file = File(thisProcess.argv[0], "w");
var quote = { |x| x.asString.replace("\\", "\\\\").replace("\"", "\\\"").quote };
var names = { |methods| (methods ? []).collect { |m| quote.(m.name) }.join(",") };
file.write("[");
Class.allClasses.reject(_.isMetaClass).do { |class, i|
    if (i > 0) { file.write(",\n") };
    file.write("{\"name\":" ++ quote.(class.name)
        ++ ",\"superclass\":" ++ quote.(class.superclass !? _.name ? "")
        ++ ",\"methods\":[" ++ names.(class.methods)
        ++ "],\"class_methods\":[" ++ names.(class.class.methods) ++ "]}");
};
file.write("]");
file.close;
0.exit;
"""


def sclang_classes(folders: list[Path], tmp: Path) -> dict[str, dict]:
    config = tmp / "sclang_conf.yaml"
    config.write_text(
        "excludeDefaultPaths: true\nincludePaths:\n"
        + "".join(f'  - "{folder}"\n' for folder in folders)
    )
    script, output = tmp / "dump.scd", tmp / "classes.json"
    script.write_text(DUMP)
    result = subprocess.run(
        [SCLANG, "-l", str(config), str(script), str(output)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if not output.exists():
        sys.exit(f"sclang wrote nothing:\n{result.stdout[-3000:]}{result.stderr}")
    return {c["name"]: c for c in json.loads(output.read_text())}


def parser_classes(folders: list[Path]) -> tuple[dict[str, dict], list[str]]:
    classes: dict[str, dict] = defaultdict(
        lambda: {"superclass": None, "methods": set(), "class_methods": set()}
    )
    errors = []
    for folder in folders:
        for path in sorted(folder.rglob("*.sc")):
            relative = path.relative_to(folder).as_posix()
            if compiled_folder(relative, platform=PLATFORM, ide="none") is None:
                continue
            parsed = parse(path.read_text(encoding="utf-8", errors="replace"))
            if parsed.error:
                errors.append(f"{path}: {parsed.error}")
            for c in parsed.classes:
                entry = classes[c.name]
                if not c.is_extension:
                    entry["superclass"] = c.superclass or (
                        "" if c.name == "Object" else "Object"
                    )
                for m in c.methods:
                    entry["class_methods" if m.is_class_method else "methods"].add(
                        m.name
                    )
    return classes, errors


def main() -> None:
    folders = [Path(arg).resolve() for arg in sys.argv[1:]]
    if not folders:
        sys.exit(__doc__)
    with tempfile.TemporaryDirectory() as tmp:
        expected = sclang_classes(folders, Path(tmp))
    parsed, errors = parser_classes(folders)
    defined = {name for name, c in parsed.items() if c["superclass"] is not None}
    problems = [f"parse error: {error}" for error in errors]
    problems += [
        f"only sclang: class {name}" for name in sorted(expected.keys() - defined)
    ]
    problems += [
        f"only parser: class {name}" for name in sorted(defined - expected.keys())
    ]
    for name in sorted(expected.keys() & defined):
        want, got = expected[name], parsed[name]
        if want["superclass"] != got["superclass"]:
            problems.append(
                f"{name}: superclass {got['superclass']!r}, sclang {want['superclass']!r}"
            )
        for key in ("methods", "class_methods"):
            for method in sorted(set(want[key]) - got[key]):
                problems.append(f"{name}: only sclang has {key[:-1]} {method}")
            for method in sorted(got[key] - set(want[key])):
                problems.append(f"{name}: only the parser has {key[:-1]} {method}")
    print("\n".join(problems))
    print(
        f"{len(expected)} classes in sclang, {len(defined)} parsed, "
        f"{len(problems)} differences"
    )
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
