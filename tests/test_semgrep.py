import json

from sensors.runners.parsers import SemgrepParser

SEMGREP_FINDINGS_JSON = json.dumps({
    "results": [
        {
            "check_id": "javascript.express.security.audit.xss.mustache.var-in-href",
            "path": "src/views/index.ejs",
            "start": {"line": 12, "col": 5},
            "end": {"line": 12, "col": 40},
            "extra": {
                "message": "Detected a template variable used in an anchor tag href. This is a potential XSS vulnerability.",
                "severity": "WARNING",
            },
        },
        {
            "check_id": "javascript.lang.security.detect-eval-with-expression",
            "path": "src/utils/dynamic.ts",
            "start": {"line": 45, "col": 1},
            "end": {"line": 45, "col": 25},
            "extra": {
                "message": "Detected eval with a non-literal argument. This is a security risk.",
                "severity": "ERROR",
            },
        },
    ],
    "errors": [],
})

SEMGREP_CLEAN_JSON = json.dumps({
    "results": [],
    "errors": [],
})

SEMGREP_WITH_ERRORS_JSON = json.dumps({
    "results": [],
    "errors": [
        {"message": "Failed to parse file.ts", "type": "ParseError"},
    ],
})

# PartialParsing errors use a list type: ["PartialParsing", [...spans...]]
SEMGREP_WITH_PARTIAL_PARSING_JSON = json.dumps({
    "results": [],
    "errors": [
        {
            "code": 3,
            "level": "warn",
            "type": ["PartialParsing", [{"path": ".github/workflows/main.yml"}]],
            "message": "Syntax error at line .github/workflows/main.yml:67",
            "path": ".github/workflows/main.yml",
        },
    ],
})

# NeedLogin also uses a list type in some semgrep versions
SEMGREP_WITH_NEED_LOGIN_LIST_JSON = json.dumps({
    "results": [],
    "errors": [
        {"message": "Not logged in", "type": ["NeedLogin", {}]},
    ],
})

SEMGREP_MIXED_OUTPUT = (
    "\n"
    "Scanning 95 files tracked by git with 1064 Code rules:\n"
    "\n"
    "  Language      Rules   Files\n"
    "  ts              166      76\n"
    "\n"
    "  100% 0:00:00\n"
    "\n"
    + SEMGREP_CLEAN_JSON
    + "\n"
    "Ran 261 rules on 95 files: 0 findings.\n"
)


def test_parse_mixed_with_stderr():
    """Semgrep sends progress to stderr mixed with JSON stdout."""
    parser = SemgrepParser()
    parsed = parser.parse(SEMGREP_MIXED_OUTPUT)

    assert parsed.success is True
    assert parsed.findings == []


def test_parse_with_findings():
    parser = SemgrepParser()
    parsed = parser.parse(SEMGREP_FINDINGS_JSON)

    assert parsed.success is False
    assert len(parsed.findings) == 2

    f0 = parsed.findings[0]
    assert f0.file == "src/views/index.ejs"
    assert f0.line == 12
    assert f0.column == 5
    assert f0.rule == "javascript.express.security.audit.xss.mustache.var-in-href"
    assert f0.severity == "warning"

    f1 = parsed.findings[1]
    assert f1.file == "src/utils/dynamic.ts"
    assert f1.line == 45
    assert f1.severity == "error"


def test_parse_clean():
    parser = SemgrepParser()
    parsed = parser.parse(SEMGREP_CLEAN_JSON)

    assert parsed.success is True
    assert parsed.findings == []
    assert parsed.score.value == 0


def test_parse_with_errors():
    parser = SemgrepParser()
    parsed = parser.parse(SEMGREP_WITH_ERRORS_JSON)

    assert parsed.success is False
    ec = next(m for m in parsed.metrics if m.key == "errorCount")
    assert ec.value == 1
    assert parsed.extra["errors"][0]["message"] == "Failed to parse file.ts"


def test_parse_partial_parsing_is_ignored():
    """PartialParsing errors (list-typed) should be ignored -- scan still succeeded."""
    parser = SemgrepParser()
    parsed = parser.parse(SEMGREP_WITH_PARTIAL_PARSING_JSON)

    assert parsed.success is True
    ec = next(m for m in parsed.metrics if m.key == "errorCount")
    assert ec.value == 0


def test_parse_need_login_list_type_is_ignored():
    """NeedLogin encoded as a list type should also be ignored."""
    parser = SemgrepParser()
    parsed = parser.parse(SEMGREP_WITH_NEED_LOGIN_LIST_JSON)

    assert parsed.success is True
    ec = next(m for m in parsed.metrics if m.key == "errorCount")
    assert ec.value == 0


def test_parse_invalid_json():
    parser = SemgrepParser()
    parsed = parser.parse("not json at all")

    assert parsed.success is False
    assert "parseError" in parsed.extra


def test_parse_empty():
    parser = SemgrepParser()
    parsed = parser.parse("")

    assert parsed.success is False
    assert "parseError" in parsed.extra


def test_parse_score():
    parser = SemgrepParser()
    parsed = parser.parse(SEMGREP_FINDINGS_JSON)

    assert parsed.score.value == 2
    assert parsed.score.direction == "less"


def test_parse_summary_findings():
    parser = SemgrepParser()
    parsed = parser.parse(SEMGREP_FINDINGS_JSON)
    assert "2 findings" in parsed.summary


def test_parse_summary_clean():
    parser = SemgrepParser()
    parsed = parser.parse(SEMGREP_CLEAN_JSON)
    assert parsed.summary == "No findings"

