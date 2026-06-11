---
name: sensors_config-typescript
description: Set up sensors (quality monitors) for a TypeScript/JavaScript/Node project. Detects which sensors are already configured, identifies missing ones from the supported list
---

# Configure Sensors for a TypeScript/JavaScript Project

This skill sets up sensor config files for a TypeScript/JavaScript/Node project using the supported sensor parsers. It is **TypeScript/JavaScript/Node only** — refuse if the project is not of that type.

---

## Supported Sensors

### Maintainability (tool configs → `.sensors/maintainability/`)

| Sensor name | Parser | Underlying tool | What it monitors |
|-------------|--------|-----------------|-----------------|
| `tests` | `vitest` | vitest | Unit/integration test results |
| `cov` | `vitest_cov` | vitest --coverage | Test coverage |
| `types` | `tsc` | tsc --noEmit | TypeScript type errors |
| `lint` | `eslint` | eslint | Code style and quality issues |
| `structure` | `depcruise` | dependency-cruiser | Import/dependency rule violations |
| `stryker` | `stryker` | stryker | Mutation testing results (triggered) |
| `mut_state` | _(script)_ | query_stryker.py | Per-file mutation state (on_check) |

### Security (tool configs → `.sensors/security/`)

| Sensor name | Parser | Underlying tool | What it monitors |
|-------------|--------|-----------------|-----------------|
| `security` | `semgrep` | semgrep | SAST — secrets, injection, vuln patterns |

---

## ESLint Custom Formatter

This skill includes a custom ESLint formatter and config (in `eslint/` relative to this skill file). It enriches lint output with per-rule guidance text — both for the sensor (JSON) and for developers (human-readable). The folder contains:

| File | Purpose |
|------|---------|
| `eslint.config.js` | Starter ESLint config — copied then adapted to the project |
| `eslint-formatter-core.cjs` | Shared processing logic and rule override messages |
| `eslint-formatter-json.cjs` | JSON output for the sensor parser — requires core |
| `eslint-formatter-custom.cjs` | Human-readable output with guidance — requires core |

All four files are **copied** (not moved) from the skill folder into `.sensors/maintainability/` in the target project.

**After copying `eslint.config.js`**, adapt it to the project:
- Update the `files` glob patterns (e.g. `client/src/**/*.{ts,tsx}`) to match the actual source directories
- Remove or add plugins (e.g. `eslint-plugin-react-hooks`) based on what the project uses
- Adjust rule thresholds to match the project's existing standards
- Do **not** change the formatter references or the `ignores` block without good reason

The npm scripts use the formatters via `--format <path>`:
```json
"lint": "eslint --config .sensors/maintainability/eslint.config.js --format .sensors/maintainability/eslint-formatter-custom.cjs src/",
"lint:sensor": "eslint --config .sensors/maintainability/eslint.config.js --format .sensors/maintainability/eslint-formatter-json.cjs src/"
```

**Rule overrides** (in `eslint-formatter-core.cjs`) are the key customisation point — they attach short display text and long guidance to specific ESLint rule IDs. When setting up, adapt the overrides in `core.cjs` to match any project-specific ESLint rules that benefit from extra context.

### Detecting an existing formatter

Before copying, check whether the project already uses a custom ESLint formatter:
- Look for `--format` in existing npm lint scripts pointing to a file path (not a built-in like `json`, `stylish`, etc.)
- Look for formatter packages in devDependencies (e.g. `eslint-formatter-*`, `@microsoft/eslint-formatter-*`)

If a custom formatter is **already in use**: tell the user what was found and ask what they'd like to do — replace it, keep it, or merge the rule overrides into their existing formatter.

If **no custom formatter** is found: copy all four files to `.sensors/maintainability/` and use them in the npm scripts without asking.

---

## Tool Config Files

Each sensor's tool config file lives inside the `.sensors/<category>/` folder, not at the project root. This keeps quality tooling config co-located with the sensor definitions and away from the root.

**When a tool config already exists at the root, move it** to the appropriate subfolder and update any references (npm scripts, `command` in the sensor YAML). Ask the user to confirm the move.

| Sensor Tool | Root location (typical) | Move to |
|------|------------------------|---------|
| eslint | `eslint.config.js`, `.eslintrc.*` | `.sensors/maintainability/eslint.config.js` |
| dependency-cruiser | `.dependency-cruiser.js`, `.dependency-cruiser.cjs` | `.sensors/maintainability/.dependency-cruiser.js` |
| vitest | `vitest.config.ts` (only if standalone, not merged into `vite.config.ts`) | `.sensors/maintainability/vitest.config.ts` |
| semgrep | `.semgrepignore`, `.semgrep.yml` | `.sensors/security/` |

**Do NOT move:**
- `tsconfig.json` — used by the build, editor, and many tools; must stay at root
- `vite.config.ts` — if vitest config is embedded here, leave it; only move a standalone `vitest.config.ts`
- `package.json` — obviously stays at root

Each tool that supports a `--config <path>` flag should use that flag pointing to the new location. The sensor `command` in the YAML and any npm scripts must use the new path.

---

## Stryker: Mutation Testing

This skill includes a `stryker/` folder (relative to this skill file) containing `query_stryker.py` — a script that reads a Stryker mutation report and outputs per-file mutation state.

### Setup

1. **Copy `query_stryker.py`** from the skill's `stryker/` folder to `.sensors/maintainability/` in the target project.

2. **Create or move the Stryker config** to `.sensors/maintainability/` (e.g. `stryker.config.json` or `stryker.config.mjs`). If one already exists at the project root, move it there and update any references.

3. **Add an npm script** for mutation testing. The report must be written to `reports/mutation/mutation.json`:
   ```json
   "test:mutation": "stryker run .sensors/maintainability/stryker.config.json"
   ```
   Ensure the Stryker config sets `"jsonReporter": { "fileName": "reports/mutation/mutation.json" }` (or equivalent).

4. **Add two runners** to the sensors YAML:

```yaml
  - name: stryker
    parser: stryker
    enabled: true
    mode: triggered
    result: reports/mutation/mutation.json
    command: npm run test:mutation
    prompt: "This is just informational, you don't have to do anything about it unless the user asks you to."

  - name: mut_state
    enabled: true
    mode: on_check
    command: python .sensors/maintainability/query_stryker.py reports/mutation/mutation.json files --changed -v
```

The `stryker` runner is `triggered` — it only runs when explicitly requested, not on a timer. The `mut_state` runner is `on_check` — it reads the existing report on every sensor check without re-running mutation testing.

---

## Dependency-Cruiser: Architecture Template Analysis

Before creating a dependency-cruiser config, analyse the codebase and match it against the available ruleset templates in the `dep-cruiser-templates/` folder (relative to this skill file). The templates are reference rulesets — the agent adapts path patterns to match the actual folder names found in the project, then generates a single merged `.dependency-cruiser.js`.

### Available templates

| Template file | Applies to |
|--------------|-----------|
| `.data-dashboard.cjs` | Projects with a layered backend: routes → services → clients + domain, plus optional middleware |
| `.react.cjs` | Projects with a React frontend: hooks own data fetching, components are pure rendering |

### Layer detection — scan the project structure

List the top-level directories under the main source root(s) (commonly `src/`, `server/`, `client/src/`, `app/`, etc.). Map what you find to the known layer concepts using these naming equivalences:

| Layer concept | Common folder names |
|--------------|-------------------|
| Routes / entry | `routes`, `controllers`, `handlers`, `pages`, `api` (Next.js), `app` (Next.js app router) |
| Services / orchestration | `services`, `orchestration`, `usecases`, `use-cases`, `application`, `facades` |
| Clients / adapters | `clients`, `adapters`, `repositories`, `gateways`, `infrastructure`, `integrations` |
| Domain / core | `domain`, `core`, `entities`, `models`, `business` |
| Middleware | `middleware`, `interceptors`, `guards` |
| React components | `components`, `ui`, `views`, `pages` (when React) |
| React hooks | `hooks`, `composables` |

### Template matching logic

1. **`.data-dashboard.cjs` applies if** at least a services/orchestration layer AND a clients/adapters layer are found (even under different names). Domain and middleware layers are bonus — include their rules if the folders exist.

2. **`.react.cjs` applies if** React is in `dependencies` or `devDependencies` AND both a components folder and a hooks folder exist.

3. **Both templates apply** if both conditions are met — merge all their rules into the output config.

4. **Neither template applies** — tell the user no template matched, and offer to create a minimal config with only the built-in depcruise defaults.

### Presenting findings to the user

Before generating the config, present a mapping label like this:

```
Detected architecture layers:
  routes layer     → app/routes/         (matches: routes)
  services layer   → app/orchestration/  (matches: services)
  clients layer    → app/adapters/       (matches: clients)
  domain layer     → app/core/           (matches: domain)
  middleware       → not found — middleware rules will be skipped
  React components → client/src/components/
  React hooks      → client/src/hooks/

Applicable templates: .data-dashboard.cjs + .react.cjs

Proposed rules (adapted paths):
  domain-no-clients, domain-no-services, domain-no-unlayered,
  sdks-only-in-clients, clients-no-services, clients-no-routes,
  services-no-unlayered, components-no-data-fetching, hooks-no-components

Does this look correct? Confirm to generate .sensors/maintainability/.dependency-cruiser.js.
```

Ask the user to confirm (or correct) the layer mapping before generating anything.

### Generating the adapted config

Once confirmed:
- Replace all template path patterns (e.g. `^server/services/`) with the actual paths found in the project (e.g. `^app/orchestration/`)
- Omit rules for layers that don't exist in the project
- Merge rules from all applicable templates into a single `forbidden` array
- Keep the `options` block from `.data-dashboard.cjs` as the base (it includes tsconfig integration)
- Update the `LAYERS` constant to describe the actual layer names used in this project

---

## Instructions

### Step 1 — Verify this is a TypeScript/JavaScript project

Read `package.json` in the project root. If it does not exist, **stop and tell the user** this skill is for TypeScript/JavaScript/Node projects only.

Also check for at least one of: `typescript` in `devDependencies`, a `tsconfig.json`, or `.ts` files in the project. If none of these exist, **proactively offer to set up TypeScript** before continuing:

> This project is plain JavaScript. TypeScript gives coding agents much stronger signal (type errors surface immediately as a sensor) and makes the codebase easier to work in safely. Want me to set up TypeScript now? I'll install the packages, create `tsconfig.json`, and rename source files to `.ts`/`.tsx`.

If the user agrees, complete the TypeScript setup before proceeding with sensor configuration:
1. Install `typescript @types/node` (and `@types/react @types/react-dom` for React projects)
2. Create `tsconfig.json` — copy any `paths` from an existing `jsconfig.json`, then delete `jsconfig.json`
3. Rename `.js`/`.jsx` source files to `.ts`/`.tsx` and add type annotations where straightforward
4. Add `next-env.d.ts` to `.gitignore` if not already present (for Next.js projects)

If the user declines, continue with JavaScript-compatible sensors only.

### Step 2 — Determine the project name

Derive from the directory name: lowercase, hyphens for spaces/underscores. Example: `my-app`.

### Step 3 — Check existing sensor configuration

Look for `.sensors/<project-name>.sensors.yaml`. Note which sensors are already configured in it — skip those in later steps. If it doesn't exist yet, all sensors are candidates.

### Step 4 — Analyse the project tooling

Read `package.json` (scripts + devDependencies + dependencies). For each sensor in the supported list that is **not yet configured**, determine:

- **Is the underlying tool already installed?** (present in devDependencies or can be checked via scripts)
- **Is it compatible with this project?** For example:
  - `vitest` / `vitest_cov`: compatible if `vitest` is already installed — but also **offer to set up vitest even if no tests exist yet**. A test suite with a placeholder test and coverage is valuable from day one and gives coding agents a working feedback loop to build on. If offering: install `vitest @vitejs/plugin-react jsdom @testing-library/react @testing-library/dom vite-tsconfig-paths` (plus `vite-tsconfig-paths` for TypeScript projects), create the vitest config in `.sensors/maintainability/`, and create a placeholder test. For `vitest_cov` specifically, also install `@vitest/coverage-v8` — it is a separate package from `vitest` and coverage runs will fail at runtime without it.
  - `tsc`: only if `typescript` is a devDependency and a `tsconfig.json` exists
  - `eslint`: only if `eslint` is a devDependency or in scripts
  - `depcruise`: only if `dependency-cruiser` is installed, OR ask if user wants to add it
  - `semgrep`: always applicable (runs standalone), but flag it as requiring a separate install

Also check for existing tool config files at the project root (see "Tool Config Files" section above). Note which ones exist — these will be moved rather than created fresh.

### Step 5 — Research current CLI APIs before configuring

**Before generating the config for each sensor**, use web search to verify:
- Current recommended CLI flags for the tool (especially output format flags required by the parser)
- The `--config <path>` flag syntax for pointing to a non-default config file location
- Any breaking changes in recent major versions

Pay special attention to:
- **eslint**: uses the custom formatter at `.sensors/maintainability/eslint-formatter-json.cjs` (not `--format json`). Verify the `--format <path>` flag syntax for the installed ESLint version, and verify `--config` flag syntax.
- **vitest**: verify the `--run` flag (single-pass, no watch) and `--config` for pointing to `.sensors/maintainability/vitest.config.ts`.
- **tsc**: verify `--noEmit` is still correct for type-checking without emitting; note `tsconfig.json` stays at root.
- **depcruise**: verify the `--output-type err-long` and `--config` flags.
- **semgrep**: verify the `--json` flag, ruleset flags (e.g. `--config p/javascript`), and ignore file flag.

For sensors that need JSON output for the sensor parser to work, create **two npm scripts**: one with a `:sensor` suffix (using JSON output, pointing to the `.sensors/` config path) and one without (human-readable, for developer convenience). Use the `:sensor` one in the `*.sensors.yaml` file.

Example for eslint:
```json
"lint": "eslint --config .sensors/maintainability/eslint.config.js src/",
"lint:sensor": "eslint --format json --config .sensors/maintainability/eslint.config.js src/"
```

### Step 6 — Ask user to confirm each new sensor

For each sensor that is **not yet configured** and appears compatible, present a confirmation prompt that includes:

1. What the sensor monitors
2. Whether a tool config file already exists at the root and will be **moved** vs. created fresh
3. Any prerequisite (tool to install, config to create)

Do not configure a sensor the user did not confirm. It is fine to confirm multiple sensors at once with a clear checklist.

### Step 7 — Set up the directory structure

Create these directories if they don't exist:
- `.sensors/`
- `.sensors/maintainability/`
- `.sensors/security/`

Create `.sensors/.gitignore` if it doesn't exist, with:

```
# Sensors runtime state

events*.jsonl
sessions*.jsonl
*history.jsonl

*.log

*.state.json
*.control.json
*.sock
```

### Step 8 — Move or create tool config files

For each confirmed sensor:

1. **If the tool config exists at the root**: move it to the appropriate `.sensors/<category>/` subfolder. Update any existing npm scripts that reference it to use the new path.
2. **If no tool config exists**: create a minimal working config in `.sensors/<category>/`. For example, create a default `eslint.config.js` with a sensible TypeScript ruleset.

If moving a config file could break the build or the editor (e.g., a vitest config embedded in vite.config.ts), **do not move it** — instead point to it in-place using an absolute or relative path.

**For eslint specifically:** follow the "ESLint Custom Formatter" section above. Check for an existing custom formatter before copying. Copy all four files from the skill's `eslint/` folder to `.sensors/maintainability/`, then adapt `eslint.config.js` to match the project's source paths and plugins.

**For dependency-cruiser specifically:** follow the "Dependency-Cruiser: Architecture Template Analysis" section above. Do not create the config until layer detection and user confirmation are complete. The output config goes to `.sensors/maintainability/.dependency-cruiser.js`.

**For vitest/vitest_cov specifically:** when creating the vitest config, include the coverage reporter configuration (`reporter: ["json"]`) if the `cov` sensor is also being set up — the `vitest_cov` parser requires JSON output and will fail without it. If there are no existing tests, also create a minimal placeholder test so the sensors have a working run from the start.

### Step 9 — Create or update the sensor YAML file

There is **one** sensors file: `.sensors/<project-name>.sensors.yaml`. It contains all runners regardless of category (maintainability or security). The subfolders only hold tool config files.

If the file already exists, **append** only the newly confirmed sensors. Do not overwrite existing runners.

#### Config template

```yaml
version: 1
# prompt: Optional guidance for coding agents shown on every status query
runners:

  - name: <sensor-name>
    parser: <parser-name>
    enabled: true
    mode: interval
    command: <exact shell command — use npm run <script>:sensor or npx with --config flag>
    interval: <ms>
    workingDir: <relative path — omit if project root>
    # prompt: Optional per-sensor guidance for coding agents
```

#### Field rules

| Field | Rule |
|-------|------|
| `name` | Short, unique, descriptive. Use prefixes for monorepos (e.g. `fe-lint`, `be-tests`). |
| `parser` | Must be one of the registered parsers in the table above. |
| `mode` | Always `interval`. |
| `command` | Exact shell command. Reference the `:sensor` npm script or include `--config` and format flags. |
| `interval` | Set based on tool speed and how frequently the output changes (see guidance below). Spread intervals across runners so they don't all fire simultaneously. |
| `workingDir` | Omit to use project root. Set if runner must run in a subdirectory. |

#### Interval guidance

Choose intervals based on two factors: **how fast the tool runs** and **how often its output meaningfully changes**. Spread values across runners so they don't all trigger at the same moment.

| Sensor | Suggested interval | Rationale |
|--------|-------------------|-----------|
| `lint` (eslint) | 10 000 ms | Fast; catches issues as you edit |
| `types` (tsc) | 20 000 ms | Moderate speed; type errors change with every edit but tsc is slower than eslint |
| `tests` (vitest) | 15 000 ms | Run frequently but give it a moment to settle after edits |
| `cov` (vitest --coverage) | 60 000 ms | Slow; coverage only shifts when tests or source change meaningfully |
| `structure` (depcruise) | 90 000 ms | Slow graph traversal; architectural violations are rare and don't shift quickly |
| `security` (semgrep) | 120 000 ms | Slow SAST scan; security posture rarely changes during a coding session |

Adjust up if the project is large. The goal is that no two sensors fire at exactly the same second during normal coding.

#### Parser-specific command notes

- **eslint**: Use the `:sensor` npm script: `npm run lint:sensor`. This runs the JSON formatter from `.sensors/maintainability/eslint-formatter-json.cjs`.
- **vitest** (tests): `npx vitest run --config .sensors/maintainability/vitest.config.ts` (`--run` ensures single-pass, no watch)
- **vitest_cov** (coverage): `npx vitest run --coverage --config .sensors/maintainability/vitest.config.ts` — also set `result: coverage/coverage-final.json` in the runner YAML and `reporter: ["json"]` in the vitest coverage config; both are required for the parser to read results
- **tsc** (types): `npx tsc --noEmit` (tsconfig.json stays at root, no --config needed)
- **depcruise** (structure): `npx depcruise --config .sensors/maintainability/.dependency-cruiser.js --output-type err-long src/`
- **semgrep** (security): `semgrep --config p/javascript --config p/typescript --json src/` (with `--exclude-rule` or `--ignore` pointing to `.sensors/security/.semgrepignore` if applicable)
- **stryker**: `npm run test:mutation` — writes report to `reports/mutation/mutation.json` (mode: `triggered`, not interval)
- **mut_state**: `python .sensors/maintainability/query_stryker.py reports/mutation/mutation.json files --changed -v` (mode: `on_check`)

---

## Step 10 — Present the label

After creating/updating the files, tell the user:
- Which sensors were added to `.sensors/<project-name>.sensors.yaml`
- Which tool config files were moved and from where
- Which sensors were skipped and why (already configured, incompatible, not confirmed)
- Any prerequisites still needed (tool installs, config files to create)
- That `prompt` fields can be added to sensor YAMLs to give coding agents context-specific guidance

---

## Example output structure

```
.sensors/
  .gitignore
  my-app.sensors.yaml        ← single file, all runners
  maintainability/
    eslint.config.js              ← copied from skill eslint/ folder, adapted to project
    eslint-formatter-core.cjs     ← copied from skill eslint/ folder
    eslint-formatter-json.cjs     ← copied from skill eslint/ folder
    eslint-formatter-custom.cjs   ← copied from skill eslint/ folder
    .dependency-cruiser.js        ← generated from template(s), adapted to project
    vitest.config.ts              ← moved from project root (if standalone)
    query_stryker.py              ← copied from skill stryker/ folder
    stryker.config.json           ← moved from project root or created fresh
  security/
    .semgrepignore           ← moved from project root
```
