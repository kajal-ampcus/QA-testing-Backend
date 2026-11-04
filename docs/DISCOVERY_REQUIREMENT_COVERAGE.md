# Discovery and requirement coverage

Discovery inventories application pages independently of requirement scope.
Before generating tests, the generation screen checks selected pages against
the chosen requirement. Suggestions use page routes and headings, excluding
global navigation. Suggestions are review aids, not proof of complete AC coverage.

For pages without a match, users can:

- Choose another matching approved requirement.
- Preview and edit a requirement drafted from observed page controls.
- Draft exploratory visibility checks through the same approval process.
- Explicitly link an existing requirement to the pages. This creates a new
  requirement version and uses the existing approval/outdated-test lifecycle.

Exploratory drafts make control visibility claims. The normal form-draft flow
also proposes entry/retention, one-option selection, required-field omission,
missing required selection, and all-required-empty rules. Asterisks and labels
are proposal evidence, not native HTML-validity evidence. A reviewer must confirm
these rules and supply the expected successful submission result before saving.
Behavior cases use manual outcome assertions until an automated result oracle
is observed. Hidden or missing upload inputs, unknown format/length constraints,
and ambiguous controls do not produce guessed validation assertions.

Visibility-only approved form requirements expose an upgrade action. Previewing
it changes no data; saving creates a new requirement version for normal approval.
The previous version and its tests follow the existing history/outdated rules.
Generation combines form-display obligations into one smoke case, preserving AC
traceability, and emits separate reviewed positive and negative behavior cases.
Radio controls are clicked, never filled as text. Fixed choices and intentionally
omitted inputs do not become missing test-data requests; valid form values remain
unresolved until supplied by the user.

Drafts use the existing requirement tables, route tags in version metadata, and
version-pinned approvals. No migration or automatic approval is introduced.
Repeated saving of the same map/state/mode draft reopens the existing requirement.
Editing requirement text retains its discovery route metadata.

After approval the workspace returns to generation with the selected pages.
Only pages matched to the chosen approved requirement are submitted by the UI;
full-map discovery remains available. Existing generation API clients retain
their current contract. Test review defaults to the selected requirement and
current map, with earlier-map cases accessible through a history filter.
Requirement-wide AC coverage is labelled separately, and repeated generation
issues are grouped by AC.

New requirement API endpoints:

- `POST /requirements/projects/{project_id}/discovery-coverage`
- `POST /requirements/projects/{project_id}/discovery-draft` (`save=false` previews)
- `POST /requirements/projects/{project_id}/{requirement_id}/discovery-scope`

Every request validates the map and selected states against the project. Links
require the current requirement version. Normal approval remains mandatory
before test generation. Login credentials and post-generation form-data flows
remain separate.
