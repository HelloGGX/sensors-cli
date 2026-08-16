const { processResults, messageOverrides } = require("./eslint-formatter-core.cjs");

module.exports = function (results) {
  const { files, totalErrors, totalWarnings, triggeredRules } = processResults(results);

  return JSON.stringify(
    {
      files,
      summary: {
        totalErrors,
        totalWarnings,
        triggeredRules: [...triggeredRules].map((ruleId) => ({
          ruleId,
          guidance: messageOverrides[ruleId].long,
        })),
      },
    },
    null,
    2
  );
};
