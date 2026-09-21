"""Java — added after the refactor, as the test of whether it actually worked.

Total cost: this file and `queries/java.scm`. No new dependency (the grammar
already ships in tree-sitter-language-pack), no engine change, no resolver
change. Under the previous layout the same language meant ~130 lines spread
across three files, most of it a copy of an existing parser's stack walks.

`source_roots` is the one genuinely Java-shaped fact: `import com.foo.Bar`
addresses `src/main/java/com/foo/Bar.java`, so the build-tool prefix has to
come off the path before it can be read as a dotted module name. Declared as
data, resolved by the generic "dotted" strategy Python already uses.
"""

from __future__ import annotations

from fleet.graph.spec import LanguageSpec, TestRules, register

JAVA = register(
    LanguageSpec(
        name="java",
        extensions=(".java",),
        resolver="dotted",
        source_roots=("src/main/java", "src/test/java", "src/main/kotlin", "src"),
        # Substring match, so JUnit 4's @Test and JUnit 5's
        # @ParameterizedTest / @RepeatedTest all land without extra patterns.
        tests=TestRules(name_prefixes=("test",), annotations=("Test",)),
    )
)
