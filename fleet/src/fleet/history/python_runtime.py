"""Conservative, read-only Python runtime selection from committed declarations.

V1 supports root .python-version, tox.ini, native tool.tox.env_list,
requires-python/python_requires. It does not execute setup.py or infer runtime
versions from dependencies, classifiers, arbitrary CI YAML or Dockerfiles.
"""
from __future__ import annotations

import ast
import configparser
import re
import subprocess
import tomllib
from pathlib import Path

from packaging.specifiers import SpecifierSet
from packaging.version import Version

CATALOG = tuple(f'3.{n}' for n in range(7, 15))


def expand_tox(value: str) -> list[str]:
    match = re.search(r'\{([^{}]+)\}', value)
    if match:
        return [item for choice in match[1].split(',')
                for item in expand_tox(value[:match.start()] + choice + value[match.end():])]
    return re.split(r'[,\s]+', value.strip())


def resolve_files(files: dict[str, str], catalog: tuple[str, ...] = CATALOG) -> dict:
    result = {'status': 'needs_environment_review', 'selected_python': None,
              'declared_test_versions': [], 'eligible_versions': [], 'evidence': [],
              'reasons': [], 'policy': 'explicit_pin_else_newest_tox_v1',
              'execution_verified': False}
    constraints = []
    tox_versions = set()
    pin = None

    def evidence(path, field, value):
        result['evidence'].append({'path': path, 'field': field, 'value': value})

    def constraint(path, field, value):
        if not isinstance(value, str):
            raise ValueError(f'{path}: dynamic or invalid {field}')
        constraints.append(SpecifierSet(value))
        evidence(path, field, value)

    def tox(path, raw):
        if isinstance(raw, list):
            if not all(isinstance(x, str) for x in raw):
                raise ValueError('nonliteral tox environment list')
            raw = ','.join(raw)
        if not isinstance(raw, str):
            raise ValueError('nonliteral tox environment list')
        evidence(path, 'tox environment list', raw)
        for env in expand_tox(raw):
            # Exclude PyPy, free-threaded, lint, docs and specialized env factors.
            m = re.fullmatch(r'py3\.?([0-9]{1,2})', env)
            if m:
                tox_versions.add('3.' + str(int(m[1])))

    try:
        if 'pyproject.toml' in files:
            data = tomllib.loads(files['pyproject.toml'])
            project = data.get('project', {})
            if 'requires-python' in project:
                constraint('pyproject.toml', 'requires-python', project['requires-python'])
            if 'requires-python' in project.get('dynamic', []):
                raise ValueError('dynamic requires-python requires investigation')
            tool = data.get('tool', {})
            tox_config = tool.get('tox', {})
            if 'base_python' in tox_config.get('env_run_base', {}):
                raise ValueError('Native tox base_python override requires review')
            if any('base_python' in env for env in tox_config.get('env', {}).values() if isinstance(env, dict)):
                raise ValueError('Native tox interpreter override requires review')
            if 'env_list' in tool.get('tox', {}):
                tox('pyproject.toml', tool['tox']['env_list'])
            if tool.get('poetry', {}).get('dependencies', {}).get('python'):
                raise ValueError('Poetry Python constraints not supported in v1')
        for path in ('setup.cfg', 'tox.ini'):
            if path not in files:
                continue
            cfg = configparser.ConfigParser(interpolation=None)
            cfg.read_string(files[path])
            if cfg.has_option('options', 'python_requires'):
                constraint(path, 'python_requires', cfg.get('options', 'python_requires'))
            for key in ('envlist', 'env_list'):
                if cfg.has_option('tox', key):
                    tox(path, cfg.get('tox', key))
            for section in cfg.sections():
                if (section == 'testenv' or re.fullmatch(r'testenv:py3\.?\d+', section)) and cfg.has_option(section, 'basepython'):
                    raise ValueError(f'{path}: basepython override requires review')
        if 'setup.py' in files:
            tree = ast.parse(files['setup.py'])
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    for keyword in node.keywords:
                        if keyword.arg == 'python_requires':
                            constraint('setup.py', 'python_requires', ast.literal_eval(keyword.value))
        if '.python-version' in files:
            raw = files['.python-version'].strip()
            if not re.fullmatch(r'3\.\d+(?:\.\d+)?', raw):
                raise ValueError('ambiguous or unsupported .python-version')
            pin = raw
            evidence('.python-version', 'runtime pin', pin)
        declared = sorted(tox_versions, key=Version)
        result['declared_test_versions'] = declared
        candidates = [pin] if pin else declared
        if not candidates:
            raise ValueError('No explicit runtime pin or supported tox test declaration; requirements alone are insufficient')
        for version in candidates:
            minor = '.'.join(version.split('.')[:2])
            if minor not in catalog:
                continue
            if pin and tox_versions and minor not in tox_versions:
                raise ValueError('Runtime pin conflicts with tox test versions')
            # Never pretend a minor image tag fixes a required patch release.
            if len(version.split('.')) == 2 and any(len(s.version.rstrip('.*').split('.')) > 2 for c in constraints for s in c):
                raise ValueError('Patch-level Python constraints require an exact runtime pin')
            if all(c.contains(version) for c in constraints):
                result['eligible_versions'].append(version)
        if not result['eligible_versions']:
            raise ValueError('No declared runtime satisfies package constraints and supported runtime catalog')
        result['selected_python'] = max(result['eligible_versions'], key=Version)
        result['status'] = 'resolved_from_declarations'
    except (ValueError, TypeError, SyntaxError, configparser.Error) as exc:
        result['reasons'].append(str(exc))
    return result


def resolve_snapshot(repo: Path, ref: str) -> dict:
    def git(*args):
        return subprocess.check_output(['git', '-C', str(repo), *args], timeout=30)
    sha = git('rev-parse', '--verify', ref + '^{commit}').decode().strip()
    files = {}
    for path in ('.python-version', 'pyproject.toml', 'setup.cfg', 'setup.py', 'tox.ini'):
        spec = f'{sha}:{path}'
        check = subprocess.run(['git', '-C', str(repo), 'cat-file', '-e', spec], capture_output=True, timeout=30)
        if check.returncode:
            continue
        if int(git('cat-file', '-s', spec)) > 1_000_000:
            return {'status': 'needs_environment_review', 'sha': sha, 'selected_python': None,
                    'reasons': [f'{path} exceeds analysis budget']}
        files[path] = git('show', spec).decode()
    return {'sha': sha, **resolve_files(files)}


def resolve_pair(repo: Path, base: str, fixed: str) -> dict:
    before, after = resolve_snapshot(repo, base), resolve_snapshot(repo, fixed)
    common = sorted(set(before.get('eligible_versions', [])) & set(after.get('eligible_versions', [])), key=Version)
    added = sorted(set(after.get('declared_test_versions', [])) - set(before.get('declared_test_versions', [])), key=Version)
    return {'base': before, 'fixed': after, 'common_python': common[-1] if common else None,
            'newly_declared_versions': added,
            'runtime_change_needs_review': before.get('eligible_versions') != after.get('eligible_versions') or not common,
            'meaning': 'Declarations select a candidate environment; installation and execution must still verify it.'}
