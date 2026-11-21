"""Static screening must never execute historical code."""
import pytest

from fleet.history.content import python_change_kind
from fleet.history.patch import classify_path, PathKind


@pytest.mark.parametrize('before,after,path,expected', [
    ('x = 1\n', '# comment\nx=1\n', 'app.py', 'comments_or_formatting_only'),
    ('"""old"""\nx=1\n', '"""new"""\nx=1\n', 'app.py', 'docstrings_only'),
    ('def f():\n "old"\n return 1\n', 'def f():\n "new"\n return 1\n', 'app.py', 'docstrings_only'),
    ('__version__="1"\n', '__version__="2"\n', '_version.py', 'version_values_only'),
    ('__version__="1"\n', '__version__="2"\n', 'app.py', 'executable_syntax_changed'),
    ('__version__="1"\nx=1\n', '__version__="2"\nx=2\n', '_version.py', 'executable_syntax_changed'),
    ('__version__="1"\n', '__version__=calculate()\n', '_version.py', 'executable_syntax_changed'),
    ('raise ValueError("old")\n', 'raise ValueError("new")\n', 'app.py', 'executable_syntax_changed'),
    ('broken (', 'x=1\n', 'app.py', 'unknown'),
    # Type comments are comments: the parser must not count them as code.
    ('d[k] = v  # type: ignore[index]\n', 'd[k] = v\n', 'app.py', 'comments_or_formatting_only'),
    ('def f(a):\n    # type: (int) -> int\n    return a\n', 'def f(a):\n    return a\n', 'app.py',
     'comments_or_formatting_only'),
    ('x = []  # type: list[int]\n', 'x = []\n', 'app.py', 'comments_or_formatting_only'),
])
def test_python_screen(before, after, path, expected):
    assert python_change_kind(before, after, path) == expected


@pytest.mark.parametrize('path', ['MANIFEST.in', '.tox-coveragerc', '.coveragerc',
    '.readthedocs.yaml', '.readthedocs.yml', 'requirements.txt',
    'requirements-dev.txt', 'docs/requirements-rtd.txt'])
def test_packaging_and_environment_files_are_config(path):
    assert classify_path(path) == PathKind.CONFIG


@pytest.mark.parametrize('before,after,path,expected', [
    ('func f() int {\n\treturn 1\n}\n', '// doc\nfunc f() int {\n    return 1 // one\n}\n', 'a.go', 'comments_or_formatting_only'),
    ('fn a() -> i32 { 1 }\n', '/// doc\nfn a() -> i32 {\n    1\n}\n', 'a.rs', 'comments_or_formatting_only'),
    ('int f(){return 1;}\n', '/* x */ int f() {\n  return 2;\n}\n', 'a.cpp', 'executable_syntax_changed'),
    ('const a = "x // not a comment";\n', 'const a = "x";\n', 'a.js', 'executable_syntax_changed'),
    # Directives written as comments are code.
    ('//go:build amd64\n\npackage p\n', '//go:build arm64\n\npackage p\n', 'a.go', 'executable_syntax_changed'),
    ('// +build linux\n\npackage p\n', '// +build darwin\n\npackage p\n', 'a.go', 'executable_syntax_changed'),
    ('package p\n\n// #include <a.h>\nimport "C"\n', 'package p\n\n// #include <b.h>\nimport "C"\n', 'a.go',
     'executable_syntax_changed'),
    ('// @ts-ignore\nconst a: number = "x";\n', 'const a: number = "x";\n', 'a.ts', 'executable_syntax_changed'),
    ('// ordinary\nconst a = 1;\n', 'const a = 1;\n', 'a.ts', 'comments_or_formatting_only'),
    ('x', 'y', 'a.unknownlang', 'unknown'),
])
def test_token_screen(before, after, path, expected):
    from fleet.history.content import token_change_kind
    assert token_change_kind(before, after, path) == expected
