// Appended to every rule comment so LLMs see the full layering model on any violation.
const LAYERS =
  "[ Architecture: " +
  "React frontend layering: components receive data via props or custom hooks. " +
  "Hooks own data fetching (react-query, fetch, etc.) and business logic. " +
  "Components are pure rendering — they must not reach into data-fetching libraries directly. " +
  " ]" +
  " !! Excluding file paths from the rules is the VERY last resort!! Always consider other options first, like if a file actually belongs somewhere else.";

/** @type {import('dependency-cruiser').IConfiguration} */
module.exports = {
  forbidden: [
    // ── React frontend layer rules ──

    {
      name: "components-no-data-fetching",
      comment:
        "App components must not import react-query directly. " +
        "Data fetching belongs in a hook in hooks/. " +
        "Components should receive data as props or call a custom hook. " + LAYERS,
      severity: "error",
      from: {
        path: "^client/src/components/",
        pathNot: ["^client/src/components/ui/", "/__tests__/"],
      },
      to: { path: "node_modules/@tanstack/react-query" },
    },
    {
      name: "hooks-no-components",
      comment:
        "Hooks are data/behaviour layer; they must not import UI components. " +
        "If a hook needs component types (e.g. shadcn toast), move the shared types to lib/ or hooks/. " + LAYERS,
      severity: "error",
      from: { path: "^client/src/hooks/", pathNot: "/__tests__/" },
      to: { path: "^client/src/components/" },
    },
  ],
  options: {
    doNotFollow: {
      path: "node_modules",
      dependencyTypes: [
        "npm",
        "npm-dev",
        "npm-optional",
        "npm-peer",
        "npm-bundled",
      ],
    },
    tsPreCompilationDeps: true,
    tsConfig: { fileName: "tsconfig.json" },
    enhancedResolveOptions: {
      exportsFields: ["exports"],
      conditionNames: ["import", "require", "node", "default"],
    },
  },
};
