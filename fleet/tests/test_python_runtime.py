from fleet.history.python_runtime import resolve_files


def test_requirements_are_not_interpreter_evidence():
    r = resolve_files({'requirements.txt': 'pytest==7.4.4'})
    assert r['selected_python'] is None


def test_tox_braces_ignore_pypy_special_and_threaded():
    r = resolve_files({'tox.ini': '[tox]\nenvlist=py{37,39,310},pypy3,docs,py314t,stress-py314'})
    assert r['selected_python'] == '3.10'
    assert r['declared_test_versions'] == ['3.7', '3.9', '3.10']


def test_constraints_filter_declared_versions():
    r = resolve_files({'tox.ini': '[tox]\nenvlist=py37,py310,py311',
                       'pyproject.toml': '[project]\nrequires-python=">=3.8,<3.11"'})
    assert r['selected_python'] == '3.10'


def test_constraints_alone_do_not_invent_test_version():
    assert resolve_files({'pyproject.toml': '[project]\nrequires-python=">=3.7"'})['selected_python'] is None


def test_pin_conflict_is_not_silently_overridden():
    r = resolve_files({'.python-version': '3.11', 'tox.ini': '[tox]\nenvlist=py310'})
    assert r['selected_python'] is None


def test_setup_is_parsed_never_executed():
    r = resolve_files({'setup.py': 'raise RuntimeError("must not execute")\nsetup(python_requires=">=3.7,<3.11")',
                       'tox.ini': '[tox]\nenvlist=py310,py311'})
    assert r['selected_python'] == '3.10'


def test_dynamic_setup_requires_review():
    r = resolve_files({'setup.py': 'setup(python_requires=get_version())', '.python-version': '3.11'})
    assert r['selected_python'] is None


def test_native_tox_toml():
    r = resolve_files({'pyproject.toml': '[project]\nrequires-python=">=3.10"\n[tool.tox]\nenv_list=["py3.14", "py3.14t", "py3.10"]'})
    assert r['selected_python'] == '3.14'


def test_patch_constraint_requires_exact_version():
    f={'pyproject.toml':'[project]\nrequires-python=">=3.10.5"', 'tox.ini':'[tox]\nenvlist=py310'}
    assert resolve_files(f)['selected_python'] is None
    f['.python-version']='3.10.9'
    assert resolve_files(f)['selected_python']=='3.10.9'


def test_interpreter_override_requires_review():
    r=resolve_files({'tox.ini':'[tox]\nenvlist=py310\n[testenv]\nbasepython=python3.11'})
    assert r['selected_python'] is None
