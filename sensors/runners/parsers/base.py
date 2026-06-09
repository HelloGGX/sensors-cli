"""Base output parser interface.

Output parsers are stateless plugins that handle tool-specific parsing and formatting.
They are injected into the GenericRunner which handles all process management.

Each parser provides 6 formatting methods for 3 client types (terminal, HTML, LLM):
- format_details_*: Short one-line summary (e.g., "2 errors, 1 warning")
- format_failures_*: Multi-line failure details

HTML methods should use these CSS classes for consistent styling:
- sensors-error: error-level items (red)
- sensors-warn: warning-level items (yellow)
- sensors-success: success items (green)
- sensors-file: file path references
- sensors-rule: lint rule identifiers
- sensors-message: descriptive messages
- sensors-violation: a single violation/failure block
"""

from abc import ABC, abstractmethod

from sensors.config import RunnerResult, ScoreInfo


class OutputParser(ABC):
    """Abstract base class for output parsers.

    Parsers are stateless and focused solely on data transformation:
    - Parsing raw output into structured RunnerResult
    - Detecting watch-mode completion boundaries (optional)
    - Formatting results for three client types: terminal (Rich), HTML, and LLM (plain text)

    All process management (spawning, watching, intervals) is handled by GenericRunner.
    """

    @abstractmethod
    async def parse_output(self, output: str) -> RunnerResult:
        """Parse raw command output into a structured result.

        Args:
            output: Command output string (ANSI escape codes already stripped by the runner)

        Returns:
            RunnerResult containing timestamp, success status, and parsed output data
        """
        pass

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

    @abstractmethod
    def calculate_score(self, result: RunnerResult) -> ScoreInfo:
        """Calculate a numerical score for trend comparison.

        Returns a ScoreInfo with:
        - value: a number representing the current status (e.g. number of failures)
        - direction: "less" if lower is better (errors, failures), "more" if higher is better

        Args:
            result: The parsed runner result

        Returns:
            ScoreInfo with the numerical score and which direction is better
        """
        pass

    # -- Details: short one-line summary --

    @abstractmethod
    def format_details_terminal(self, result: RunnerResult) -> str:
        """Format result as a short line for the Rich terminal display.

        May include Rich markup tags (e.g., [red], [green]).
        Shown in the "Details" column of the live display table (~20-30 chars).
        """
        pass

    @abstractmethod
    def format_details_html(self, result: RunnerResult) -> str:
        """Format result as a short HTML snippet for web dashboards.

        Use the documented CSS classes (sensors-error, sensors-warn, etc.).
        """
        pass

    @abstractmethod
    def format_details_llm(self, result: RunnerResult) -> str:
        """Format result as a short plain-text line for LLM/agent consumption.

        No markup, no special characters. Pure text.
        """
        pass

    # -- Failures: multi-line failure details --

    @abstractmethod
    def format_failures_terminal(self, result: RunnerResult) -> str:
        """Format failures as multi-line Rich text for terminal display.

        May include Rich markup. Return empty string if no failures.
        """
        pass

    @abstractmethod
    def format_failures_html(self, result: RunnerResult) -> str:
        """Format failures as multi-line HTML for web dashboards.

        Use the documented CSS classes. Return empty string if no failures.
        """
        pass

    @abstractmethod
    def format_failures_llm(self, result: RunnerResult) -> str:
        """Format failures as multi-line plain text for LLM/agent consumption.

        Should be comprehensive and easy to parse line-by-line.
        Return empty string if no failures.
        """
        pass
