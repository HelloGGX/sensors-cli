const { processResults, messageOverrides } = require("./eslint-formatter-core.cjs");

module.exports = function (results) {
  const { files, totalErrors, totalWarnings, triggeredRules } = processResults(results);

  let output = "";

  for (const { filePath, messages } of files) {
    output += `\n${filePath}\n`;
    for (const msg of messages) {
      const severity = msg.severity === 2 ? "error" : "warning";
      output += `  ${msg.line}:${msg.column}  ${severity}  ${msg.shortText}  ${msg.ruleId}\n`;
    }
  }

  if (totalErrors + totalWarnings > 0) {
    output += `\n\u2716 ${totalErrors + totalWarnings} problems (${totalErrors} errors, ${totalWarnings} warnings)\n`;
  }

  if (triggeredRules.size > 0) {
    output += "\n────────────────────────────────────────\n";
    output += "Additional guidance:\n";
    for (const ruleId of triggeredRules) {
      output += `\n${messageOverrides[ruleId].long}\n`;
    }
  }

  return output;
};
