# Frontend behavior harness

The frontend behavior harness is a deterministic, provider-free executable specification for the browser-facing contract implemented in `app/frontend/app.js`.

## Evidence model

The harness follows an empirical testing rule: assert observable effects at the system boundary instead of private implementation structure whenever practical.

Each scenario is written as:

1. **Given** a controlled API response sequence and initial user input;
2. **When** the user performs an interaction such as search, explicit acquisition, copy or new search;
3. **Then** inspect externally observable evidence:
   - HTTP path, method and authorization header;
   - rendered text and visibility;
   - focus movement and ARIA state;
   - clipboard output;
   - scheduled/cancelled polling;
   - absence of credential leakage in URLs;
   - terminal state after bounded retries.

The harness intentionally does not emulate a complete browser. The fake DOM implements only APIs exercised by the application and records their effects. This keeps failures attributable, fast and offline.

## Covered behavioral contracts

The executable scenarios cover:

- successful tenant-authenticated lookup;
- process-result focus/orientation after success;
- summary lifecycle from `processing` to published;
- stale-result removal before a subsequent failed lookup;
- explicit user action before provider-backed acquisition;
- bounded availability polling and terminal stop;
- local rejection of invalid CNJ and missing credential;
- unauthorized behavior without process-existence disclosure;
- network failure distinct from application failure;
- inert rendering of unsupported markup and allowlisted JSX behavior;
- human-readable clipboard projection rather than raw JSX wrappers;
- cancellation of polling on new search and page exit;
- bearer-token transmission only through the authorization header in observed requests.

## Engineering constraints

The harness must remain:

- **deterministic**: no real clock, provider, database or network;
- **hermetic**: only repository files and in-memory fakes;
- **behavioral**: prefer outputs and effects over matching source-code strings;
- **diagnosable**: each scenario has a name that appears in failing output;
- **minimal**: no browser framework dependency while the product surface remains this small;
- **complementary**: static Python tests still enforce asset/markup invariants, while PostgreSQL integration/E2E tests prove backend tenancy, acquisition and persistence.

A browser automation stack should be introduced only when there is a demonstrated class of regression that the current DOM-level harness cannot observe reliably, such as layout, actual focus order across native controls, browser accessibility-tree behavior or cross-browser rendering.

## Running

Run the frontend harness directly:

```bash
node tests/frontend_behavior_test.mjs
```

It is also part of the canonical CI workflow and the offline release smoke. A local pass does not replace CI on the exact pull-request head.

## Change rule

When a frontend bug is fixed, first encode the user-visible failure as a scenario or observable assertion, then make the smallest production change required to pass it. Avoid adding assertions that merely mirror implementation text unless the requirement is intrinsically static, such as forbidding `innerHTML` or persistent browser storage.
