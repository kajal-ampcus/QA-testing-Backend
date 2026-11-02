# Credential and form test data integration

This change extends existing credential, fixture, generation and execution flows.
No database migration is required.

## Implemented

- Discovery retains login prompts and skips general form-value collection. Field nodes remain in the application map.
- `GET /api/v1/projects/{project_id}/form-test-data/smart-template` derives input requirements from current generated case steps and their pinned maps. Login and challenge fields return metadata only.
- Project Data displays requirements, filters missing values, and opens existing fixture entry and Excel import flows. Test Cases links to this screen.
- `infra/test_data_service.py` resolves generated data, an unambiguous positive baseline in the assigned fixture's exact form group, assigned data, and explicit `test_data.scenario_overrides`, in that order. Overrides merge recursively and preserve explicit blanks. Provenance contains source labels only.
- Generation, runtime fixture injection and standalone packaging use the shared resolver. Explicit step literals remain scenario overrides. Configured values in grounded steps use references. Login cases with selected credentials use vault-backed runtime variables for valid companion fields.
- The worker builds a run snapshot before invoking the runner, preserves execution draft overrides, and removes the run input directory on completion, error or cancellation. Existing vault injection supplies credentials separately.
- Download builds local `config/testdata.json` in memory. The generated input loader reads this file without platform API/database access. Credentials and sensitive form values are omitted by default.
- The download checkbox sends `include_credentials=true`. This requires `ALLOW_CREDENTIAL_EXPORT=true`, a nonempty `API_KEY`, and a matching `X-API-Key` header. The current platform API key is the authorization boundary; per-user project export permissions are not available in the existing application.
- Authorized packages contain per-spec `config/credentials.json`; the auth fixture resolves account fields, login URL, role and login navigation from it. Both configuration files are ignored by Git and excluded from regular workspace previews/download copying. Automatic copying of another suite's plaintext `.env` has been removed.

## Usage

1. Discover the application with login credentials when required.
2. Generate test cases, then open **Configure test data**.
3. Supply missing values using the prefilled fixture drawer or existing Excel import. Save an assignment for each applicable case.
4. Use `scenario_overrides` inside versioned test-case data for explicit field overrides, for example `{"scenario_overrides":{"email":""}}`. Existing explicit step values also retain their scenario meaning.
5. Generate or regenerate automation to obtain the new loaders and binding metadata. Existing generated suites are not rewritten automatically.
6. Run from the platform, or download the suite. Enable credential inclusion only when authorized. A fresh local machine requires Node.js, `npm install`, and `npx playwright install` before `npx playwright test`.

## Remaining plan work and limits

This is an initial integration, not certification of every acceptance criterion.

- The existing execution agent still receives repositories for run/report persistence and obtains case context from them. Fully removing its database access requires changing that execution-agent contract; this change does not claim that isolation.
- Existing screenshot, video and trace capture paths have not undergone the plan's complete secret-leak audit. Do not treat artifact confidentiality as verified.
- Smart requirements currently cover structured `fill`/`select` steps with observed stable element codes. Prose-only steps and implicit required companions need a further requirements pass.
- Baselines are reused only when a fixture assignment establishes exact form context and there is exactly one active positive baseline. Ambiguity stays unresolved.
- Fixture entry uses the existing JSON editor and Excel columns. Approval is not blocked on template completeness. The workflow link guides collection before approval, without introducing a new mandatory stage.
- CAPTCHA and OTP remain runtime inputs. Sensitive form inputs omitted from a package require local input or an authorized inclusive export.
- Live authenticated/public discovery, platform browser execution, standalone browser execution and existing-project database regressions still require a running test environment.

## Validation

Frontend production build and 24 frontend unit tests passed. Agent regression suite: 241 passed; the CAPTCHA-refresh expectation in `test_execution_plan.py` fails against both this branch and the unchanged HEAD planner. Additional integration tests cover recursive precedence, blanks, metadata masking, private-file exclusions, runtime resolution and export denial.
