"""Opt-in browser contract test; no database, LLM, or target-site crawl.

Run Vite, set FRONTEND_URL=http://localhost:3000 and optionally
PLAYWRIGHT_CHANNEL=msedge, then run this file with pytest.
"""

import json
import os

import pytest

pytestmark = pytest.mark.skipif(not os.getenv("FRONTEND_URL"), reason="FRONTEND_URL required")


def test_requirement_to_discovery_to_test_cases():
    from playwright.sync_api import expect, sync_playwright

    project = {"id": "p1", "name": "Browser contract", "application_url": "https://example.test", "credential_ref": None}
    requirement = {
        "id": "r1", "req_code": "REQ-001", "project_id": "p1", "version": 1,
        "status": "NEEDS_CLARIFICATION", "title": "Login", "description": "Sign in promptly",
        "domain_tags": ["auth"], "acceptance_criteria": [{"id": "AC-1", "text": "Login succeeds", "source": "REQUIREMENT"}],
        "ambiguities": [{"field": "timing", "issue": "Define promptly", "requires_clarification": True}],
    }
    state = {"requirements": [], "approvals": [], "map": None, "tests": [], "polls": 0}
    calls = []

    def route_api(route):
        request = route.request
        path = request.url.split("/api/v1", 1)[1]
        data = request.post_data_json if request.method == "POST" else None
        calls.append((request.method, path, data))
        code = 200
        if path == "/projects":
            response = [project]
        elif path == "/requirements/projects/p1":
            if data:
                assert data["raw_text"] == "User can sign in promptly"
                state["requirements"] = [requirement]
                state["approvals"] = [{"id": "a1", "target_id": "r1", "target_type": "requirement", "status": "PENDING"}]
                response = requirement
            else:
                response = state["requirements"]
        elif path == "/requirements/r1/clarifications":
            assert data["expected_version"] == 1
            assert data["resolutions"] == [{"ambiguity_index": 0, "decision": "Within two seconds"}]
            requirement.update(version=2, ambiguities=[], status="PENDING_APPROVAL")
            state["approvals"][0]["id"] = "a2"
            response = requirement
        elif path == "/approvals?project_id=p1":
            response = state["approvals"]
        elif path == "/approvals/a2/approve":
            assert data["decided_by"] == "QA Reviewer"
            requirement["status"] = "APPROVED"
            state["approvals"] = []
            response = {"status": "APPROVED"}
        elif path == "/projects/p1/credentials":
            assert data == {"username": "tester", "password": "test-only-password"}
            project["credential_ref"] = "cred:1"
            response = {"credential_ref": "cred:1"}
        elif path == "/application-maps/projects/p1/discover":
            assert data["focus_requirements"] == ["r1"]
            response = {"job_id": "j1"}
        elif path == "/application-maps/jobs/j1":
            state["polls"] += 1
            complete = state["polls"] >= 2
            if complete:
                state["map"] = {"id": "m1", "project_id": "p1", "version": 1, "base_url": project["application_url"], "status": "COMPLETE", "termination_reason": "queue_exhausted", "coverage": {}, "states": [{"state_code": "S-1", "url_pattern": "/login", "fingerprint": "hash", "reached_via": [], "elements": []}]}
            response = {"job_id": "j1", "status": "complete" if complete else "in_progress", "result": {"status": "SUCCESS"} if complete else None}
        elif path == "/application-maps/projects/p1":
            response = state["map"] or {"detail": "No map yet"}
            code = 200 if state["map"] else 404
        elif path == "/test-cases/projects/p1/generate":
            assert data == {
                "requirement_id": "r1",
                "application_map_id": "m1",
                "generation_scope": "all",
                "selected_area_ids": [],
                "selected_module_ids": [],
            }
            state["tests"] = [{"id": "t1", "tc_code": "TC-001", "requirement_id": "r1", "requirement_version": 2, "status": "DRAFT", "current": {"version": 1, "title": "Successful sign in", "objective": "Verify sign in", "category": "positive", "preconditions": [], "steps": [{"step_number": 1, "action": "Click sign in", "target": {"role": "button"}, "expected": "Dashboard"}], "expected_result": "Dashboard is visible", "test_data": {}, "traceability": ["REQ-001:AC-1"], "confidence": 0.9}}]
            response = {"generated": 1, "test_cases": state["tests"], "uncovered_acs": [], "partial_pairing_acs": [], "needs_review_test_cases": []}
        elif path == "/test-cases/projects/p1":
            response = state["tests"]
        else:
            raise AssertionError(f"Unexpected request: {request.method} {path}")
        route.fulfill(status=code, content_type="application/json", body=json.dumps(response))

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel=os.getenv("PLAYWRIGHT_CHANNEL") or None)
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.route("**/api/v1/**", route_api)
        page.goto(os.environ["FRONTEND_URL"])
        expect(page.get_by_text("Backend connected", exact=True)).to_be_visible()
        page.get_by_label("Requirements and user stories").fill("User can sign in promptly")
        page.get_by_role("button", name="Analyze and save", exact=True).click()
        page.get_by_label("Reviewer name").fill("QA Reviewer")
        expect(page.get_by_role("button", name="Approve requirement", exact=True)).to_be_disabled()
        page.get_by_label("Decision for timing").fill("Within two seconds")
        page.get_by_role("button", name="Save clarifications").click()
        page.get_by_role("button", name="Approve requirement", exact=True).click()
        expect(page.get_by_text("Requirement approved and saved.", exact=True)).to_be_visible()
        page.get_by_role("button", name="Application discovery", exact=True).click()
        page.get_by_label("Target username").fill("tester")
        page.get_by_label("Target password").fill("test-only-password")
        page.get_by_role("button", name="Save credentials", exact=True).click()
        expect(page.get_by_label("Target password")).to_have_value("")
        page.get_by_role("button", name="Start discovery", exact=True).click()
        expect(page.get_by_text("Job in_progress", exact=False)).to_be_visible()
        page.reload()  # persisted job ID resumes monitoring without a new POST
        page.get_by_role("button", name="Application discovery", exact=True).click()
        expect(page.get_by_text("Application map v1 · COMPLETE", exact=True)).to_be_visible(timeout=15000)
        page.get_by_role("button", name="Test cases", exact=True).click()
        page.get_by_role("button", name="REQ-001: Generate").click()
        expect(page.get_by_role("heading", name="Successful sign in")).to_be_visible()
        assert len([c for c in calls if c[1].endswith("/discover")]) == 1
        stored = page.evaluate("JSON.stringify(localStorage)")
        assert "test-only-password" not in stored
        assert "qa-discovery-job:p1" not in stored
        assert not errors
        browser.close()


def test_workspace_retains_failed_input_and_isolates_project_state():
    """Hook boundaries must preserve retry input and stop another project's polling."""
    from playwright.sync_api import expect, sync_playwright

    projects = [
        {"id": "p1", "name": "Alpha", "application_url": "https://alpha.test", "credential_ref": None},
        {"id": "p2", "name": "Beta", "application_url": "https://beta.test", "credential_ref": None},
    ]
    state = {"polls": 0, "analysis_attempts": 0, "requirements": []}

    def route_api(route):
        request = route.request
        path = request.url.split("/api/v1", 1)[1]
        code = 200
        if path == "/projects":
            response = projects
        elif path == "/application-maps/jobs/pending-alpha":
            state["polls"] += 1
            response = {"job_id": "pending-alpha", "status": "in_progress"}
        elif path.startswith("/application-maps/projects/"):
            code, response = 404, {"detail": "No map yet"}
        elif path == "/requirements/projects/p1" and request.method == "POST":
            state["analysis_attempts"] += 1
            if state["analysis_attempts"] == 1:
                code, response = 503, {"detail": "Temporary extraction outage"}
            else:
                assert request.post_data_json["raw_text"] == "Alpha login requirement"
                response = {
                    "id": "r-alpha", "req_code": "REQ-ALPHA", "project_id": "p1",
                    "version": 1, "status": "PENDING_APPROVAL", "title": "Alpha login",
                    "description": "Only belongs to Alpha", "acceptance_criteria": [],
                    "ambiguities": [], "domain_tags": [],
                }
                state["requirements"] = [response]
        elif path == "/requirements/projects/p1":
            response = state["requirements"]
        elif path == "/requirements/projects/p2" or path.startswith(("/test-cases/", "/approvals?")):
            response = []
        else:
            raise AssertionError(f"Unexpected request: {request.method} {path}")
        route.fulfill(status=code, content_type="application/json", body=json.dumps(response))

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel=os.getenv("PLAYWRIGHT_CHANNEL") or None)
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.add_init_script("localStorage.setItem('qa-project', 'p1'); localStorage.setItem('qa-discovery-job:p1', 'pending-alpha');")
        page.route("**/api/v1/**", route_api)
        page.goto(os.environ["FRONTEND_URL"])
        source = page.get_by_label("Requirements and user stories")
        source.fill("Alpha login requirement")
        page.get_by_role("button", name="Analyze and save", exact=True).click()
        expect(page.get_by_role("alert")).to_have_text("Temporary extraction outage")
        expect(source).to_have_value("Alpha login requirement")
        page.get_by_role("button", name="Analyze and save", exact=True).click()
        expect(page.get_by_role("heading", name="REQ-ALPHA: Alpha login", exact=True)).to_be_visible()
        expect(source).to_have_value("")
        page.get_by_label("Reviewer name").fill("Alpha reviewer")

        page.get_by_label("Active project").select_option("p2")
        expect(page.get_by_text("No requirements yet. Submit source requirements above.")).to_be_visible()
        expect(page.get_by_role("heading", name="REQ-ALPHA: Alpha login", exact=True)).to_have_count(0)
        expect(page.get_by_label("Reviewer name")).to_have_value("")
        expect(page.get_by_role("button", name="Refresh", exact=True)).to_be_enabled()
        polls_after_switch = state["polls"]
        # Observe one complete polling interval to detect a leaked timer.
        page.wait_for_timeout(3000)
        assert state["polls"] == polls_after_switch

        page.get_by_role("button", name="Application discovery", exact=True).click()
        expect(page.get_by_role("button", name="Stop monitoring", exact=True)).to_have_count(0)
        page.get_by_label("Target username").fill("beta-user")
        page.get_by_label("Target password").fill("beta-password")
        page.get_by_label("Active project").select_option("p1")
        expect(page.get_by_label("Target password")).to_have_value("")
        expect(page.get_by_label("Target username")).to_have_value("")
        expect(page.get_by_role("button", name="Stop monitoring", exact=True)).to_be_visible()
        page.get_by_role("button", name="Stop monitoring", exact=True).click()
        expect(page.get_by_role("button", name="Stop monitoring", exact=True)).to_have_count(0)
        assert page.evaluate("localStorage.getItem('qa-discovery-job:p1')") is None
        assert not errors
        browser.close()
