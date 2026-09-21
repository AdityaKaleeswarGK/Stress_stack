"""Python. The one language on the `ast` backend — see `graph/pyast.py`."""

from __future__ import annotations

from fleet.graph.spec import LanguageSpec, TestRules, register

PYTHON = register(
    LanguageSpec(
        name="python",
        extensions=(".py", ".pyi"),
        backend="python_ast",
        resolver="dotted",
        # `pkg/__init__.py` is imported as `pkg`, never as `pkg.__init__`.
        index_names=("__init__",),
        tests=TestRules(name_prefixes=("test_",), annotations=("pytest.mark", "unittest")),
    )
)
