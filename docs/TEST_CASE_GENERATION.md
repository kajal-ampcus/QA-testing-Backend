# Requirement-driven test-case generation

Implemented 26 October 2026. This describes the current pipeline and supersedes earlier analysis documents that describe whole-map inventory generation or fixed case quotas.

## Root causes found

1. Normal generation added availability checks for every observed control, then attached them to whichever AC shared the most words. This produced unrelated public-page cases under a login requirement.
2. AC coverage was satisfied by any reference to an AC ID. A visible Login button could therefore conceal missing authentication, validation, or reset behavior.
3. The rule generator ordinarily selected one category/one successful template per AC. The AI schema and prompt imposed four-case and short-step limits, and targeted repair demanded an exact count.
4. Required-field boundary tests asserted visibility while claiming validation had cleared. Native field constraints were not fully carried through discovery, and the real discovery field `input_type` was not consistently recognized.
5. Duplicate identity depended on map-local IDs and placeholder names. It omitted resolved data and structured assertions, had no concurrent-request lock, and could confuse genuinely different account contexts.
6. Screenshot references and several observed constraints were omitted from the model context. Generic outcome checks could substitute for actual result evidence.
7. The automation planner could reinterpret a native constraint check as a form-submission test and insert unrelated actions and input requirements.
8. The requirement-version update was misplaced inside explicit regeneration, where `current_version` was undefined.

## What now happens

The existing Test Design Agent loads the approved requirement, current ACs and discovery evidence. It derives independently testable scenarios before constructing cases. It uses explicit requirement rules as additional context only when the same named observed field anchors both the rule and its mapped AC.

The planner produces relevant field-presence checks, observed action/result flows, blank/nonempty required-input checks, native email-format checks, measured numeric boundaries, observed length constraints, password masking, and accessible-name checks where the requirements and UI support them. It considers negative, boundary, functional, security, accessibility and edge dimensions without expanding the existing database category enum.

Unknown limits, error wording, lockout policies, keyboard behavior, service faults, mailbox delivery and other unsupported behaviors remain named gaps. For example, observed `maxlength` establishes the attribute, but does not establish whether the application truncates input or returns a server error. A reset-request confirmation does not prove an email arrived.

There is no fixed number of cases or steps in the generation output contract. Seven explicitly required fields yield fourteen distinct native required-input scenarios; a narrow observed navigation may need one case; an unsupported requirement can correctly produce zero cases with a gap explanation. Provider token budgets and image-size bounds remain operational limits, not coverage quotas.

The selected generation scope restricts where cases start. The agent retains observed destination states as assertion evidence, so selecting Login does not discard a known Dashboard result. It does not generate unrelated component inventory.

## AI assistance and screenshots

- Existing default behavior remains deterministic. Set `TEST_DESIGN_USE_LLM=true` to use the configured provider for missing obligations.
- AI tasks are derived from missing scenarios after checking existing validated cases. The agent does not request another batch solely to reach a count or balance categories.
- Each AI candidate must pass strict validation and verify its requested missing scenario before it can be saved. Unrelated criteria and out-of-scope starting states are rejected.
- The full relevant controls, native constraints and screenshot provenance are retained in provider context rather than clipping each state to six controls.
- Set `TEST_DESIGN_VISION_MODEL` to an available vision-capable model on the configured provider to analyze local discovery PNGs before case planning. Both existing provider clients support image content, including retry paths.
- Vision summaries supplement DOM evidence; they cannot introduce validation rules or hidden outcomes. With no configured vision model, missing/oversized images, or provider failure, pixel analysis is explicitly marked unavailable. The system does not claim to have inspected pixels it could not analyze.
- Active credentials and `.env` settings were not modified. `.env.example` documents the optional flags.

## Validation and execution

Generated cases require meaningful titles, explicit preconditions, data for referenced inputs, observed state/control targets, ordered steps, and a final measurable assertion. Validation rejects missing action targets, unresolved placeholders, unsupported assertion kinds, mismatched targets, invented messages, unsupported native constraints, impossible validity expectations, and unrelated result destinations. Expected message text must be observed or explicitly quoted in the mapped requirement/AC evidence: a known requirement can specify a future error without requiring that error to have already appeared in discovery. Its assertion still needs an observed control target. Confidence is derived by the deterministic validator rather than accepted from the model.

New structured assertions for native validity, observed attributes and accessible names translate to actual Playwright checks. Native constraint cases retain their approved steps and values; execution preparation does not inject a Login click, alter their boundary data, or demand unrelated CAPTCHA answers. Existing legacy automation behavior remains available for other cases.

## Duplicate identity and concurrency

Generation and persistence share a behavior identity. It compares mapped page/account/control identity, actions, resolved data values, and structured assertions. Titles, category labels, AC labels and paraphrased expectations do not create a distinct behavior. Empty strings and whitespace remain intentional distinct values. Different link destinations, assertion rules, inputs, control scopes and account contexts remain separate.

Equivalent rediscovered IDs can match against their original map context. Earlier cases contribute to current coverage only after validating their own evidence and matching a real current-map scenario. Rejected, outdated, stale-version and malformed historical cases do not establish coverage.

The database locks the requirement row across refreshing signatures and inserting a batch. Concurrent requests therefore recheck committed cases before writing. The requirement version is checked under that lock so a run cannot silently save against a version changed while the model was working. Regeneration now performs the correct graph retirement without the previously undefined version variable; historical records are retained.

## Coverage calculation

The additive generation-response `coverage` field contains:

- `total_scenarios`, `covered_scenarios`, `missing_scenarios`;
- `scenario_coverage_percent = covered_scenarios / total_scenarios * 100`;
- `total_acs`, `fully_covered_acs`, and their calculated percentage;
- measured counts and percentages by coverage dimension;
- individual scenario status/reason and screenshot-analysis status.

An empty denominator produces `null`, not a fabricated 100%. A compound AC remains incomplete while any derived obligation is missing. Duplicate templates within an AC are consolidated before counting. An AC tag by itself never establishes coverage. These are design-coverage measurements, not line coverage, execution coverage, or a claim that the website passed.

Existing UI fields remain compatible: `ac_evidence` reports the measured covered/total scenario counts in its explanation; `generation_gaps` identifies missing scenarios. No frontend redesign, new database table, migration, or category change was required.

## Verification and practical limits

Regression coverage includes requirement relevance, multiple clauses, counts exceeding four, missing evidence, native boundaries, security/accessibility gaps, no invented messages, account separation, rediscovery, repeated and concurrent generation, strict relevance of AI candidates, image forwarding, native Playwright output and regeneration/version handling.

Verification results: full unit suite **428 passed, 2 skipped, 1 pre-existing discovery-resume failure**. After the final explicit-requirement-message validation adjustment, the relevant scenario/validation subset passed **42 tests**. Ruff checks on the changed implementation/tests passed, as did Python compilation and diff whitespace checks.

Tests use controlled fixtures and mocked model responses. They do not establish live provider vision quality, live CEP behavior, receipt of real reset emails, or production PostgreSQL concurrency performance. The row-lock SQL is implemented; concurrent orchestration is verified with a controlled transactional fixture.

One existing discovery-resume regression, `test_guided_late_credentials_offer_same_branches_as_preconfigured`, was already failing before this change. It concerns missing authentication branches after late credential entry; it is reported separately from generation verification.

Restart the backend before reviewing new output. Historical approved cases and original spreadsheets were not deleted or rewritten. Re-run discovery to collect newly supported native attributes where old maps lack them. Draft review and actual execution against a controlled test environment remain necessary to establish application behavior.

## Login coverage expansion — 28 October 2026

The low count was not a seven- or ten-case quota. The default deterministic planner rejected requirement-backed behavior when discovery had not captured an automated result assertion. An optional LLM prompt change alone cannot fix that path. Native constraint checks also did not enumerate form-submission interactions.

The planner now derives individual missing-field submissions, pairwise omissions, and all fields blank from required controls and requirement text. Credential rejection varies identifier, password, and both independently; invalid CAPTCHA remains isolated with otherwise valid inputs. Discovered password reveal and CAPTCHA refresh controls can receive clearly marked manual behavior checks. Generic negative templates are replaced when the explicit invalid-input matrix applies: discovering an error screen does not establish which arbitrary input combination produced it.

Requirement-backed cases without an established automated oracle are saved as manual-verification drafts. Their data explicitly identifies required valid/invalid fixtures; CAPTCHA needs a current challenge answer. Automation is blocked until a real outcome assertion and fixtures are configured. These drafts never produce a fake passing visibility assertion. The test-case list identifies manual verification, and measured coverage separates manual from automatable scenarios.

The prompt now explicitly requests independent scenarios, omission combinations and isolated invalid credentials, even when an AC/category already has a case. Existing architecture, categories and database schema are preserved. Semantic identity ignores requirement-proof wording so additional traceability does not manufacture duplicate behavior.

The Excel screenshot includes scenarios that require additional evidence: Employee/Kitchen/Admin transitions, remember-me policy, keyboard behavior, logout/session checks, whitespace handling, CAPTCHA expiry/reuse, and field formats. They must not all be copied into every website. The available controls, approved requirements and verified outcomes determine which are valid; unknown policies remain gaps.

Current regression validation: **396 backend unit tests passed, 2 skipped; 24 frontend unit tests passed; changed-file Ruff checks passed.** These results verify generation and automation guards, not successful execution of every website scenario. No verification projects or standalone temporary test scripts were added.

Live Cafinity validation retained 20 active cases (13 manual-verification behaviors). Two final generation requests each produced zero additional cases and skipped the 20 existing identities. Six native-check requirement mappings were corrected; unsupported order/session visibility drafts and incorrectly mapped verification drafts were retired with history retained. Current all-module design coverage is 23/41 derived obligations (56.10%); 18 remain unresolved. Screenshot pixel analysis is unconfigured, and ordering/notification/logout discovery remains incomplete. Docker API, worker and frontend were rebuilt; all five running services were healthy or running as appropriate.
