# Shared discovery form resolution

`core/tool_gateway/form_resolution.py` resolves values before discovery asks for
input. It reads through the scoped answer repository, the existing secret resolver,
and the fixture repository. It does not write values or place them in checkpoints.

Resolution requires project, page and form identity. Login values additionally
require the selected account and a compatible login route. Matching uses exact
role/name keys, exact saved authentication field names, or a small explicit set
of standard account aliases. Multiple different candidates, invalid control
values and duplicate control identities remain unresolved. Missing required
fields are prompted; unmatched optional fields are not.

The browser is inspected again before every fill. Populated or read-only controls
are preserved. Options must match a currently enabled option. Checkbox/radio
values use `true`/`false`; checked radio groups cannot be silently replaced. OTP,
CAPTCHA and session-token candidates never participate in reusable resolution.
The existing temporary challenge flow remains separate.

Filling does not authorize business-form submission. Approval still uses the
existing input-request endpoint. Requests contain unresolved fields plus
value-free `resolution_fields` descriptors for replay. Values are retrieved again
at replay time; a previously populated required control must still be populated,
otherwise replay stops before submission. Legacy requests remain readable.

Authentication responses update the encrypted account. Resolver-backed responses
remove the corresponding old durable answer entries after updating the vault,
avoiding two competing copies. Ordinary answers retain the selected account scope.
No schema migration is required.

## Fixture eligibility

General discovery has no selected test case/scenario. It deliberately does not
scan the fixture library or infer assignments from similar labels.

An explicit caller can provide `test_case_id`, `scenario`, and `form_group` in
`FormContext`. The repository verifies case/project ownership, effective account,
active assignment, exact scenario and exact form group. Fixture field keys may be
role-qualified keys (preferred) or exact normalized field names. Existing execution
fixture assignment and generation behavior are unchanged.

## Verification

`tests/unit/agents/test_shared_form_resolution.py` covers source conflicts,
project/account lookup boundaries, fixture eligibility, temporary-value exclusion,
optional/preserved inputs, option/type validation, replay and checkpoint secrecy,
and authentication storage ownership. Existing discovery, login, browser gateway
and API regressions should also be run when changing this service.
