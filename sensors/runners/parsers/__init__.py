"""Output parsers for different tools.

This module provides a registry of output parsers that can be plugged into
the generic runner. Each parser is responsible for:
- Parsing tool-specific output into RunnerResult
- Detecting watch-mode completion boundaries
- Formatting results for display and query output
"""


from .base import OutputParser
from .default import DefaultParser
from .depcruise import DepcruiseParser
from .eslint import ESLintParser
from .git_diff import GitDiffParser
from .import_linter import ImportLinterParser
from .pytest import PytestParser
from .pytest_cov import PytestCovParser
from .ruff import RuffParser
from .semgrep import SemgrepParser
from .stryker import StrykerParser
from .stylelint import StylelintParser
from .tsc import TscParser
from .vitest import VitestParser
from .vitest_cov import VitestCovParser


class ParserRegistry:
    """Registry for output parser plugins."""

    _registry: dict[str, type[OutputParser]] = {}

    @classmethod
    def register(cls, name: str, parser_class: type[OutputParser]) -> None:
        """Register a parser plugin.

        Args:
            name: Parser identifier (used in config files)
            parser_class: Parser class to register
        """
        cls._registry[name] = parser_class

    @classmethod
    def get(cls, name: str) -> type[OutputParser]:
        """Get a parser class by name.

        Args:
            name: Parser identifier

        Returns:
            Parser class

        Raises:
            KeyError: If parser not found
        """
        if name not in cls._registry:
            raise KeyError(f"Parser '{name}' not registered. Available: {list(cls._registry.keys())}")
        return cls._registry[name]

    @classmethod
    def list_available(cls) -> list[str]:
        """List all registered parser names."""
        return list(cls._registry.keys())


# Auto-register parsers on import
ParserRegistry.register("default", DefaultParser)
ParserRegistry.register("depcruise", DepcruiseParser)
ParserRegistry.register("import_linter", ImportLinterParser)
ParserRegistry.register("eslint", ESLintParser)
ParserRegistry.register("git_diff", GitDiffParser)
ParserRegistry.register("vitest", VitestParser)
ParserRegistry.register("pytest", PytestParser)
ParserRegistry.register("pytest_cov", PytestCovParser)
ParserRegistry.register("ruff", RuffParser)
ParserRegistry.register("semgrep", SemgrepParser)
ParserRegistry.register("stryker", StrykerParser)
ParserRegistry.register("stylelint", StylelintParser)
ParserRegistry.register("tsc", TscParser)
ParserRegistry.register("vitest_cov", VitestCovParser)


__all__ = [
    "OutputParser", "DefaultParser", "DepcruiseParser", "ESLintParser", "GitDiffParser",
    "ImportLinterParser", "StrykerParser", "StylelintParser", "TscParser", "VitestParser", "VitestCovParser",
    "PytestParser", "PytestCovParser", "RuffParser", "SemgrepParser",
    "ParserRegistry",
]
