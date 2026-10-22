# Palette Journal - UX & Accessibility Learnings

## 2026-09-18 - Credential Toggle & Inline Validation Alert Roles
**Learning:** In lightweight JS applications where field validation error elements are dynamically made visible (`hidden` toggled off), adding `role="alert"` directly to the error `<p>` tags enables screen readers to immediately announce error feedback when invalid inputs are submitted. Linking toggle buttons with `aria-controls` and dynamic `aria-label` ensures state changes are clear to screen reader users.
**Action:** Always include `role="alert"` on inline field error containers and explicit `aria-controls` on password/credential toggles.
