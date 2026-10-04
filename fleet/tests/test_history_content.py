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
])
def test_python_screen(before, after, path, expected):
    assert python_change_kind(before, after, path) == expected


@pytest.mark.parametrize('path', ['MANIFEST.in', '.tox-coveragerc', '.coveragerc',
    '.readthedocs.yaml', '.readthedocs.yml', 'requirements.txt',
    'requirements-dev.txt', 'docs/requirements-rtd.txt'])
def test_packaging_and_environment_files_are_config(path):
    assert classify_path(path) == PathKind.CONFIG
