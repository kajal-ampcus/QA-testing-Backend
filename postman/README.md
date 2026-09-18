# QA Platform Postman collection

Import `qa-platform.postman_collection.json` into Postman. The collection uses
`base_url=http://127.0.0.1:8000` by default. `project_id`, `requirement_id`,
and `req_code` are prefilled for the current local project; change them for
another database, or run **Create Project** and **Submit Requirement** to
capture new values. Set `application_url` before creating a project.

For the requirement and approval flow, run these requests in order:

1. **Submit Requirement** (captures `requirement_id` and `req_code`). The LLM
   may return ambiguities, so read **Get Requirement**.
2. If there are ambiguities, use **Resolve Current Ambiguities (Human Decision)**
   to provide one concrete decision for each ambiguity shown by **Get
   Requirement**. The indexes are zero-based and `expected_version` prevents
   resolving a stale version. This appends a version without another LLM call.
   Use **Revise Requirement** when the full requirement text needs re-extraction.
3. Run **List Pending Approvals** after the latest clarification or revision. It captures the
   pending `approval_id` for `requirement_id`.
4. Choose **Approve Requirement** or **Reject Requirement**. These are
   alternative actions; running both on the same approval returns 409.
5. For discovery, run **Trigger General Discovery** or, after approval,
   **Trigger Focused Discovery**. Poll **Get Discovery Job** using the captured
   `job_id`, then read **Get Latest Application Map**.

The sample requirement text is a test fixture: edit its login and history
decisions to match the application you are testing. Discovery requires a
running Redis service and `arq apps.worker.arq_worker.WorkerSettings` in a
separate terminal. HTTP 202 means the job was queued; inspect `result.status`
and `result.errors` to see whether the crawl succeeded.

The collection contains every endpoint currently mounted by `apps/api/main.py`.
The scaffolded test-design, execution, failure, and reporting routers are not
mounted yet.
