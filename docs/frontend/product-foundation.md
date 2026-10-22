# Rpy frontend product foundation

## Product decision record

The product brief intentionally leaves audience, brand references and several feature choices open. Until product research says otherwise, the MVP takes the conservative path below instead of inventing backend capabilities.

### Primary user and job

Primary user: legal professional (lawyer, paralegal or internal legal team member) who already knows the CNJ number and needs to understand the latest available process state quickly.

Primary job: find a tenant-authorized process by CNJ, request provider-backed acquisition when an authorized CNJ is not yet available, verify its identity/current state and read the latest validated AI summary without traversing every movement manually.

This is deliberately narrower than case management. Rpy is an intelligence/read surface in this phase, not a docketing, deadline or document-management system.

### MVP journey

1. Enter the environment credential.
2. Search by canonical CNJ or 20 digits.
3. See explicit loading/error/permission/not-found feedback.
4. If the CNJ is not yet available, explicitly request acquisition and follow the bounded availability check.
5. Confirm CNJ, class, court and available structured process context.
6. Distinguish summary processing from a legitimate no-summary/unavailable state.
7. Read and copy the validated published summary when available.
8. Run another lookup without losing orientation.

### Information architecture

The current backend supports one product surface, so the MVP remains a single focused workspace rather than a fabricated multi-page dashboard:

- global header: product identity and access status;
- lookup workspace: CNJ search as the primary action;
- process identity: CNJ, class and court;
- summary document: model-generated content and provenance available from the API;
- contextual actions: copy summary and new lookup;
- access setup: technical bearer credential, clearly separated from process search.

A normal login, dashboard, recents, favorites, alerts, profile, administration and case-management navigation are deferred until real contracts exist.

## Existing backend inventory

### Public/product endpoints

- `GET /health`: process liveness.
- `GET /ready`: database-backed readiness.
- `GET /processes/{code}`: bearer-authenticated tenant-scoped process read. Accepts canonical CNJ or 20 digits and returns structured process context, recent movements and the public summary lifecycle.
- `POST /processes/{code}/request`: explicit tenant-scoped acquisition request for an unavailable CNJ; returns only public state and never exposes provider credentials/internal request identifiers.
- `POST /webhooks/judit/{token}`: provider webhook ingestion used by the asynchronous acquisition lifecycle.
- `GET /ops/metrics`: operations-only endpoint protected by a separate ops token; it does not belong in the end-user UI.

### Structured product data

The tenant-safe product response now exposes the structured fields needed by the current interface, including parties, subjects, selected header/context fields, update time, recent movements and the public summary state. The frontend must still treat the API contract—not raw provider payloads or database columns—as its source of truth.

### Summary data already exposed

The current response exposes validated summary Markdown plus public lifecycle/provenance metadata. Internal queue/provider details remain outside the browser contract. The UI should prioritize legal content and use provenance/status as secondary information rather than exposing implementation noise by default.

## Capability classification

### Implemented now

- CNJ lookup with tenant authorization;
- explicit request of an unavailable CNJ through the durable Judit acquisition flow;
- bounded frontend availability polling after explicit user action;
- process identity, parties/subjects/header context and recent movements;
- explicit summary lifecycle (`available`, `processing`, `not_generated`, `unavailable`);
- published validated summary reading/copying;
- explicit auth/validation/not-found/network/server states;
- safe Markdown/allowlisted-JSX rendering;
- accessible focus/loading feedback and retry/new-search actions;
- responsive desktop/mobile layout;
- technical bearer-token access without browser persistence.

### External dependency

- live Judit/provider behavior requires environment-specific credentials, budget and provider acceptance;
- production identity provider if normal user login is required.

Any future extension must remain tenant-scoped and must not leak secret-case data.

### Explicitly deferred

- dashboard statistics;
- favorites/watchlists;
- alerts/notifications;
- deadline/task management;
- document viewer/management;
- sharing links;
- PDF/export workflow;
- administration UI;
- speculative AI risk/next-action widgets not represented by the validated summary contract;
- React/Next/Vite migration without demonstrated interaction complexity.

## UX principles

1. Legal reading first. Process identity and summary dominate the hierarchy.
2. Calm density. Desktop is information-efficient without becoming a generic SaaS dashboard.
3. Precision over decoration. No gradients, glass effects, ornamental metrics or card grids without information value.
4. State is content. Loading, empty, error and permission conditions explain what happened and the next valid action.
5. Progressive disclosure. Technical provenance is secondary; user-facing legal content is primary.
6. Safe rendering. Model output remains text-node based and credentials remain in memory only.
7. Keyboard complete. Search, credential visibility, copy and retry/new-search actions must be reachable and have visible focus.
8. Motion is optional and subtle; reduced-motion preference disables nonessential scrolling/transition behavior.

## Design system baseline

### Personality

Until a brand is defined: **precise, sober, intelligent**. Use the wordmark `Rpy`; no invented logo.

### Color

Light-first neutral legal/editorial surface with restrained deep-green accent. Semantic success, warning and danger tokens must meet WCAG 2.2 AA contrast and never communicate state by color alone. Dark mode is deferred until a product requirement exists.

### Typography

Use system sans-serif for controls, metadata and navigation; a conservative system serif stack may be used for long-form summary reading and high-level headings. No external font request is required for the MVP.

### Spacing and sizing

Use a 4px base grid with semantic steps `4, 8, 12, 16, 24, 32, 48, 64`. Interactive controls target at least 44px in the compact dimension. Main reading measure stays approximately 70–80 characters per line.

### Shape and elevation

Borders and spacing establish most hierarchy. Radius is restrained (4–8px) and elevation is reserved for floating/temporary layers; primary content should not look like a pile of cards.

### Breakpoints

- compact: below 720px, single-column and full-width primary actions;
- medium: 720–1023px;
- workspace: 1024px and above, optimized for desktop legal work.

These are layout thresholds, not device assumptions.

### Focus and input states

Every interactive element has a visible `:focus-visible` outline with sufficient contrast and offset. Inputs have persistent labels, concise hints and explicit error association where errors are field-specific. Disabled state is not used as the only loading indicator.

## Required state model

- Initial: explains the lookup requirement without empty dashboard chrome.
- Loading: preserves context, marks the result region busy and states that the latest available version is being consulted.
- Success: moves focus/orientation to the process result without surprising keyboard users.
- Invalid CNJ: identifies the field and preserves entered data for correction.
- Missing credential: identifies/focuses credential input.
- Unauthorized: says the credential is invalid/expired; does not imply whether a process exists.
- Not found/no access: backend currently conflates these through 404, so the UI must preserve that ambiguity rather than leaking authorization information.
- Existing process/summary processing: shows the explicit `processing` lifecycle state and follows it with bounded polling.
- Existing process/no generated summary: distinguishes `not_generated`/`unavailable` from active processing.
- Network unavailable: distinct from an HTTP application error; offers retry.
- Server error: concise recovery action, no stack trace.
- Long summary: readable document flow; do not truncate legal content by default.

## Accessibility acceptance criteria

- semantic landmarks and heading order;
- skip link to main content;
- labels for every form control;
- `aria-live` for asynchronous status without duplicate announcements;
- `aria-busy` while loading;
- errors connected to relevant controls when possible;
- visible keyboard focus using `:focus-visible`;
- no interaction requiring pointer hover;
- minimum target sizing appropriate to WCAG 2.2 AA;
- contrast compatible with WCAG 2.2 AA;
- reduced-motion preference respected;
- content and status never distinguished by color alone.

## MVP success criterion

A legal professional who has a valid environment credential and an authorized CNJ can understand whether the lookup succeeded, explicitly request acquisition when needed, identify the process/current summary state and read/copy the validated published summary quickly on desktop or mobile, using keyboard alone if necessary, without credential persistence, tenant leakage or executable model output.
