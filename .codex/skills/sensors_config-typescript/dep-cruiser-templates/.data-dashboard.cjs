// Appended to every rule comment so LLMs see the full layering model on any violation.
const LAYERS =
  "[ Architecture: " +
  "Layers: services → (clients + domain). " +
  "Services orchestrate: fetch data via clients, compute via domain. " +
  "Domain is pure data analysis — no I/O, no SDKs, no knowledge of data fetching. " +
  "Middleware is cross-cutting Express infrastructure — no services, no clients, no domain." +
  " ]" +
  " !! Excluding file paths from the rules is the VERY last resort!! Always consider other options first, like if a file actually belongs somewhere else.";

/** @type {import('dependency-cruiser').IConfiguration} */
module.exports = {
  forbidden: [
    // ── Layer 1: Domain is pure computation ──

    {
      name: "domain-no-clients",
      comment:
        "Domain logic must not import from API clients. " + LAYERS,
      severity: "error",
      from: { path: "^server/domain/", pathNot: "/__tests__/" },
      to: { path: "^server/clients/" },
    },
    {
      name: "domain-no-services",
      comment:
        "Domain must not depend on the orchestration layer above it. " + LAYERS,
      severity: "error",
      from: { path: "^server/domain/", pathNot: "/__tests__/" },
      to: { path: "^server/services/" },
    },
    {
      name: "domain-no-api-contracts",
      comment:
        "Domain types are independent of API contract shapes (shared schema). " + LAYERS,
      severity: "error",
      from: { path: "^server/domain/", pathNot: "/__tests__/" },
      to: { path: "^shared/schema" },
    },
    {
      name: "domain-no-unlayered",
      comment:
        "Domain modules must only import from other domain modules. " +
        "Any import from a server/ path outside domain/ (e.g. server/jigsaw/) " +
        "means a type was placed in the wrong layer — move it to domain/. " + LAYERS,
      severity: "error",
      from: { path: "^server/domain/", pathNot: "/__tests__/" },
      to: { path: "^server/", pathNot: "^server/domain/" },
    },

    // ── Layer 2: SDK usage is confined to clients ──

    {
      name: "sdks-only-in-clients",
      comment:
        "External SDK packages may only be imported by files in server/clients/. " + LAYERS,
      severity: "error",
      from: {
        path: "^server/",
        pathNot: ["^server/clients/", "^server/auth/", "/__tests__/"],
      },
      to: {
        path: [
          "node_modules/googleapis",
          "node_modules/@google/generative-ai",
          "node_modules/google-auth-library",
          "node_modules/node-fetch",
          "node_modules/axios",
        ],
      },
    },

    // ── Layer 3: Clients don't depend upward ──

    {
      name: "clients-no-services",
      comment:
        "API clients must not depend on the orchestration layer above them. " + LAYERS,
      severity: "error",
      from: { path: "^server/clients/", pathNot: "/__tests__/" },
      to: { path: "^server/services/" },
    },
    {
      name: "clients-no-routes",
      comment:
        "API clients must not import route handlers. " + LAYERS,
      severity: "error",
      from: { path: "^server/clients/", pathNot: "/__tests__/" },
      to: { path: "^server/(routes|index)\\.ts$" },
    },

    {
      name: "services-no-unlayered",
      comment:
        "Services must only import from server/clients/, server/domain/, other services, " +
        "or known root utilities (storage, mock-storage, secrets). " +
        "Ad-hoc directories (e.g. server/jigsaw/) and root-level client files " +
        "belong in server/clients/. " + LAYERS,
      severity: "error",
      from: { path: "^server/services/", pathNot: "/__tests__/" },
      to: {
        path: "^server/",
        pathNot: "^server/(clients|services|domain|auth|storage|mock-storage|secrets)(/|\\.ts$)",
      },
    },

    // ── Middleware: cross-cutting Express infrastructure ──

    {
      name: "middleware-no-services",
      comment:
        "Middleware is pure Express infrastructure and must not reach into the service layer. " + LAYERS,
      severity: "error",
      from: { path: "^server/middleware/", pathNot: "/__tests__/" },
      to: { path: "^server/services/" },
    },
    {
      name: "middleware-no-clients",
      comment:
        "Middleware must not import API clients. " + LAYERS,
      severity: "error",
      from: { path: "^server/middleware/", pathNot: "/__tests__/" },
      to: { path: "^server/clients/" },
    },
    {
      name: "middleware-no-domain",
      comment:
        "Middleware must not import domain logic. " + LAYERS,
      severity: "error",
      from: { path: "^server/middleware/", pathNot: "/__tests__/" },
      to: { path: "^server/domain/" },
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
