**Accuracy audit — 20 September 2026**

**Verdict: Gemini integration is working, but the application is not yet producing consistently accurate, complete, executable test suites.** The saved map is a useful partial inventory. Several generated tests are sensible drafts, while others contain confirmed mistakes or can pass without verifying the intended behavior. A successful API response and valid JSON do not establish functional accuracy.

I reviewed project `a180dc62-5ef0-49fd-bf2f-f2f07cde1905`, approved requirement REQ-001 version 2, application map version 2, and all 31 currently saved DRAFT test cases. Database timestamps identify two saved batches: TC-001–016 and TC-017–031. The latest saved batch contains 15 cases. This audit uses the current persisted records, rather than earlier generation-response counts.

**What was verified against the actual application**

The configured URL is https://practicetestautomation.com/practice-test-login/. I opened nine pages in headless Chrome: Home, Login, Practice, Courses, Blog, Contact, Privacy Policy, Test Exceptions, and Test Table. All nine returned HTTP 200. Browser snapshots, screenshots, and results are saved beside this report in `live_evidence.json` and the PNG files.

Six public practice-login scenarios were exercised. Valid credentials reached `/logged-in-successfully/`, displayed the logout control, and logout returned to the login page. Invalid username and empty username produced the username error. Invalid password and empty password produced the password error. Both fields empty produced the username error. These checks support the core login scenarios, but do not mean the saved generated steps were executed unchanged.

The site's own [login instructions](https://practicetestautomation.com/practice-test-login/) document those core scenarios. Its [Practice page](https://practicetestautomation.com/practice/) explicitly links to Login, Exceptions, and Table exercises.

Additional browser interactions confirmed the missing features are functional: selecting Java reduced the Table from nine rows to six Java rows; Reset restored nine rows; sorting by Enrollments produced ascending numeric order; and Add on the Exceptions page made Row 2 visible after its delay. The skip link was also exercised and stayed on the Home page at `/#main-container`. Results are saved in `interaction_evidence.json`. These are audit probes, not executions of the generated test suite.

Contact form submission was deliberately not executed: it would send a message to the site's owner. I inspected its visible content, field constraints, and CAPTCHA presence. The audit does not establish successful contact delivery or validate every link and every interactive state on the website.

**Application map findings**

| Finding | Evidence and implication |
|---|---|
| Basic navigation inventory is substantially useful | The seven distinct mapped paths exist and returned 200 during the browser check. Many saved control names match the live page. |
| COMPLETE overstates coverage | The map reports 8 states, 10 examined actions, zero remaining actions, and EXPLORATION_EXHAUSTED. It omits `/logged-in-successfully/`, `/practice-test-exceptions/`, and `/practice-test-table/`, despite those being central to the requirement. Exhausting its queue does not prove application coverage. |
| Duplicate login content | STATE-001 and STATE-002 have the same URL and identical saved element lists. They have different fingerprints but contain no saved evidence explaining a meaningful behavioral distinction. Eight states represent only seven distinct paths. |
| Link label is corrupted | The map stores the skip link as `Press `; the live accessible name is `Press "Enter" to skip to content`. Its destination is `#main-container`, not a login route. |
| Missing evidence needed for execution | Saved DOM entries contain element code, role, name, source, and risk, but omit link destinations and input constraints/visibility. The Contact form also has CAPTCHA and hidden fields that generated success tests do not account for. |
| Important behavior is unmapped | The live Table page has language and level filters, minimum enrollments, sorting, and reset behavior. The Exceptions page has delayed row creation and edit/save controls. The map contains neither page's controls nor their changing states. |

Current crawler code explains plausible causes, although it was not instrumented during the historical crawl: its unauthenticated loop records first-level destinations without adding their child links (`crawler.py:371–380`). Its authenticated loop stops expanding a state already seen in the first phase (`crawler.py:440`). The snapshot-name regex at `crawler.py:83` stops at embedded quotes. These are code-level findings; the precise historical sequence is not proven by the saved map alone.

**Generated test findings**

All saved state/element codes checked resolve to the current map, and the supplied names/roles match those saved entries. This is useful structural consistency, but it also means errors in the map can propagate into apparently consistent tests.

| Severity | Cases | Confirmed problem |
|---|---|---|
| High | TC-013, TC-023 | Both use the Contact page's `Thank you,` text as submission confirmation. It is already visible in the page's static sign-off before submission. That assertion can pass even when submission fails. Both carry confidence 0.9. |
| High | TC-007 | Expects the Home page's skip-to-content link to open the Login page. The real link targets the same page's main-content anchor. This is an incorrect test, despite confidence 0.9. |
| High | TC-016 | Expects Exceptions and Table URLs to contain `/exceptions/` and `/tables/`. Actual destinations are `/practice-test-exceptions/` and `/practice-test-table/`; the asserted substrings do not match. |
| High | TC-008–015 | Nineteen dotted test-data paths cannot be resolved from their saved values. For example, `{contact_form_valid.first_name}` refers to `contact_form_valid`, which is stored as a descriptive string, not an object with a `first_name` property. An executor must guess or fail. |
| High | TC-019, TC-030, TC-031 | Contain a click and no assertion. They cannot establish correct navigation or successful scenario completion. Their low confidence appropriately signals a gap, but they still count toward AC references. |
| Medium | TC-019 | Supplies an unobserved `/ai-workshop/` value. The live navigation href is `/workshop`. The ultimate destination was not followed during this audit, so the test's external-link assumption is unverified. |
| Medium | TC-008, TC-025 | Successful login expectations match the real application, but assertions reference the old login state because the success page is absent from the map. Manual intent is reasonable; map-driven execution is underspecified. |
| Medium | TC-010, TC-027 | Password-error assertions target a node named with the username error. TC-027 additionally mixes an explanatory note into the expected value. A name-based locator or literal text assertion can fail even when the application behaves correctly. |
| Medium | TC-024 | Claims all required Contact fields are validated, but only checks messages near First and Email. Last and Comment/Message are required too and are not asserted. |
| Medium | TC-028 | Uses an open-ended expected message instead of the exact observed empty-input response. The live response is now known and can support a precise assertion. |
| Medium | TC-029–031 | Opening a practice page is not the same as starting and completing its exercises. There are no generated filter/sort/reset checks or delayed-row/edit/save checks for the missing pages. |

The ordinary Home-to-Practice/Courses/Blog/Contact/Privacy navigation cases have destinations consistent with the live site. The invalid-username scenario also has the correct expected error. These are useful parts of the output; the defects above prevent approving the complete suite as accurate.

**Requirement quality and coverage**

The approved requirement contains 17 AC entries, but several are broad compound statements rather than independently testable criteria. AC-CLARIFY-2-1 and AC-CLARIFY-2-4 repeat the same text. The clarification requested for an inventory of pages/forms repeats generic outcomes instead of providing that inventory. The saved ambiguities list is empty despite these unresolved details.

The latest 15 saved tests mention all 17 AC IDs, but that is reference coverage only. `_check_ac_coverage` in `core/agents/test_design/agent.py:49` checks ID membership. It does not verify that a test actually proves each clause. For example, a single login test references an AC also requiring dynamic tables, filters, and sorting; those features remain untested. Checking a handful of navigation destinations also cannot prove that every hyperlink works or that every interaction is free of server errors.

The current positive/negative pairing checker reports gaps for 13 of 17 ACs in the latest saved batch. That rule itself should be applied to meaningful feature scenarios: not every broad navigation statement naturally needs a negative twin. The objective should be explicit, testable behavior coverage, rather than mechanically adding AC labels or categories.

Confidence values are model-generated estimates, not measured accuracy percentages. This audit found incorrect assertions at confidence 0.9. It would be misleading to state an overall accuracy percentage from these scores or from the integration unit tests.

**Recommended correction order**

1. Fix public-page traversal and preserve full quoted names, hrefs, input constraints, and visibility. Revisit known pages when necessary to expand links, without duplicating states. Record skipped/failed actions and feature coverage so COMPLETE has a defensible meaning.
2. Capture the missing success, error, Exceptions, and Table states, then map the relevant interactions. Treat any Contact submission test as requiring an appropriate test environment and explicit success evidence.
3. Add validation before saving generated drafts: resolvable data paths, at least one meaningful final assertion, valid state transitions, observed destinations, and separate assertion text from explanatory notes. Preserve uncovered behaviors as gaps rather than counting click-only drafts as covered.
4. Split broad/duplicate ACs into atomic scenarios with exact expected results. Regenerate against the corrected map and compare coverage against those scenarios.
5. Execute reviewed tests and compare the recorded outcomes with their expected assertions. Evaluate repeat generations and additional applications before claiming general reliability.

**What can truthfully be said now:** the application connects to Gemini, discovers some real pages, and generates useful draft test cases with traceability. It cannot yet be described as reliably generating a complete application map and accurate executable test cases automatically.

Audit artifacts: `project.json`, `requirements.json`, `map.json`, `test_cases.json`, `batches.json`, `static_checks.json`, `live_evidence.json`, screenshots, and the browser audit scripts. Production code and saved requirements/maps/test cases were not changed by this audit.
