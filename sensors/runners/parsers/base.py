"""Base output parser interface.

Output parsers are stateless plugins that handle tool-specific parsing.
They are injected into the GenericRunner which handles all process management.
"""

from sensors.config import ParsedOutput


class OutputParser:
    """Base class for output parsers.

    Parsers are stateless and focused solely on data transformation:
    - Parsing raw output into structured ParsedOutput
    - Detecting watch-mode completion boundaries (optional)

    All process management (spawning, watching, intervals) is handled by GenericRunner.
    """

    def parse(self, output: str) -> ParsedOutput:
        """Parse raw output into structured ParsedOutput."""
        raise NotImplementedError

    def is_watch_run_complete(self, line: str) -> bool:
        """Detect whether a line of output signals that a watch run has completed.

        This method is only relevant for watch-mode parsers. It's called for each
        line of output to detect when a complete run has finished and its output
        should be parsed.

        The default implementation returns False, meaning this parser doesn't support
        watch mode completion detection (suitable for interval-mode-only tools).

        Args:
            line: A single line of output (already stripped of ANSI escape codes)

        Returns:
            True if this line indicates a completed run whose output should be parsed,
            False otherwise
        """
        return False
