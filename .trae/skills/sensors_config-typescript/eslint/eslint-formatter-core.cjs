const messageOverrides = {
  "@typescript-eslint/no-explicit-any": {
    short: "Avoid `any` — use a specific type",
    long: `About @typescript-eslint/no-explicit-any:
  We want things to be typed to make it easier to avoid errors, especially for key concepts. 
  But we also want to avoid cluttering our codebase with unnecessary types.
  Make a judgment call about this. 
  If you choose to not introduce a type, suppress it with: 
    // eslint-disable-next-line @typescript-eslint/no-explicit-any -- (give reason why)`,
  },
  "max-params": {
    long: `About max-params:
  Functions with many parameters are often a design smell (poor separation of concerns, increased coupling, 
  testing gets harder, we might be missing an abstraction).
  Reflect on the ones that have been flagged, and see if we should do a refactoring to improve maintainability.
  In the rare cases where you conclude that the number of parameters is reasonable, suppress it with: 
    // eslint-disable-next-line max-params -- (give reason why)`,
  },
  "max-lines": {
    short: "File exceeds max lines (see project default or per-file cap in eslint config)",
    long: `About max-lines:
  Prefer splitting the module (components, helpers, types) so the file stays under the default limit.
  If you decide this file should stay above the limit for now, do not use eslint-disable to silence the rule indefinitely.
  Instead, add a targeted override in .sensors/maintainability/eslint.config.js: a new block with files: ["path/to/file.ts"]
  and "max-lines": ["warn", { max: <N>, skipBlankLines: true, skipComments: true }], where <N> is modestly above the current
  eslint line count so routine edits pass, but the file cannot grow without bound (revisit or split when the warning returns).
  Put a comment immediately above that block explaining why a larger file is acceptable here (e.g. vendored UI fork) — same style as the
  chart/sidebar exceptions — so the tradeoff stays explicit.
  Note: **/*.test.{ts,tsx} disables max-lines in config; do not add per-test overrides for that.`,
  },
  "max-lines-per-function": {
    long: `About max-lines-per-function:
  Prefer extracting helpers, hooks, or subcomponents so each function stays under the default limit.
  If you decide a long function is acceptable for now (e.g. a shadcn-style factory), do not use eslint-disable to silence the rule indefinitely.
  Instead, add a targeted override in .sensors/maintainability/eslint.config.js: a block with files: ["path/to/file.ts"]
  and "max-lines-per-function": ["warn", { max: <N>, skipBlankLines: true, skipComments: true }], where <N> is modestly above the current
  eslint count for the largest function in that file so small edits pass, but new mega-functions still trip the rule.
  Put a comment immediately above that block explaining why that file may contain long functions — same style as the chart/sidebar UI-kit exceptions.
  Note: **/*.test.{ts,tsx} disables max-lines-per-function in config; do not add per-test overrides for that.`,
  },
  "complexity": {
    long: `About complexity:
  Look at why the function has high complexity.Prefer fixing the design and making it simpler, only consider adding a higher complexity cap in eslint config as an exception.
  If you conclude that a single dense entry point is still the right tradeoff (rare), add a targeted threshold override in
  .sensors/maintainability/eslint.config.mjs, with a comment explaining the exception.`,
  },
  "no-console": {
    short: "Look for a logger utility instead of using `console` directly. If you don't find one, ask the user what to do.",
  },
  "@typescript-eslint/no-unused-vars": {
    long: `About no-unused-vars:
  We absolutely never want unused variables, you should remove this one. However, before you just delete it, think about why this unused var is there, and
  if it's an indication of a bigger problem in our implementation, or just a leftover.`,
  },
};

function processResults(results) {
  const files = [];
  let totalErrors = 0;
  let totalWarnings = 0;
  const triggeredRules = new Set();

  for (const result of results) {
    if (result.messages.length === 0) continue;

    const messages = result.messages.map((msg) => {
      const override = messageOverrides[msg.ruleId];
      if (override) triggeredRules.add(msg.ruleId);
      if (msg.severity === 2) totalErrors++;
      else totalWarnings++;
      return { ...msg, shortText: override?.short ?? msg.message };
    });

    files.push({ filePath: result.filePath, messages });
  }

  return { files, totalErrors, totalWarnings, triggeredRules, messageOverrides };
}

module.exports = { processResults, messageOverrides };
