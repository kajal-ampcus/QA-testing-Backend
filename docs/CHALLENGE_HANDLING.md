# Challenge handling

## Existing execution path

Discovery reads accessibility/DOM evidence through the Chrome DevTools client,
builds a page map and form descriptors, and keeps CAPTCHA/OTP values out of
durable saved inputs. Generated Playwright suites use `fixtures/auth.ts`; the
execution agent runs those suites, persists assertion/evidence JSON, and the
report agent renders the stored result. Runtime input requests preserve an open
browser while an operator supplies an approved transient value.

The pre-existing suite template already refuses an unconfigured CAPTCHA and
allows only owner-authorized `official_test` and `staging_hook` modes. Its
legacy OCR helper is retained for backwards-compatible standalone packages but
is not invoked by the current guarded login path.

## Universal layer

`core.challenge_handling` is framework-neutral and is intentionally separate
from Playwright selectors:

1. `ChallengeDetector` classifies a sanitized snapshot using visible/a11y text,
   field semantics, frame URLs, provider indicators, and optional visual labels.
   It reports `UNKNOWN` when generic evidence cannot justify a subtype.
2. `ChallengeStrategyResolver` selects provider test mode, an authentication
   integration, a non-production staging fixture, human intervention, or a
   fail-closed block. Staging hooks are never eligible in production.
3. `ChallengeOrchestrator` is a registry for verified adapters. An adapter must
   return verification success; entering a value alone is not success.

The package never solves third-party challenges, logs challenge answers, or
turns unresolved security controls into functional failures. Applications add
adapters by registering a handler for a strategy; no engine changes are needed.

## Integration contract

Browser adapters should build a snapshot after navigation and after a submit or
page-state change, call detector/resolver/orchestrator, and record the returned
`ChallengeResult` in the execution assertion/evidence metadata. For human
intervention, use the existing execution-input request channel so the browser
context stays alive. The current generated-suite guard remains the enforcement
point until browser adapters consume this contract directly.
