import uuid
from types import SimpleNamespace

import pytest

from apps.api.routers.v1.application_maps import SubmitInputRequestBody, submit_input_request


@pytest.mark.asyncio
async def test_admin_can_be_selected_after_employee_without_losing_results():
    choice = {"id": "admin", "label": "Admin", "step": {"role": "button", "name": "Admin"}}
    employee_job = {"key": "employee", "status": "completed", "path": []}
    graph = {"nodes": [{"fingerprint": "employee-dashboard"}], "edges": []}
    app_map = SimpleNamespace(status="PARTIAL", discovery_checkpoint={
        "configuration": {"mode": "guided", "credential_ref": "cred:employee"},
        "graph": graph, "jobs": [employee_job], "completed_nodes": ["employee-dashboard"],
        "input_requests": [{"id": "roles", "kind": "login_choice", "status": "applied",
                            "choices": [choice], "navigation_path": []}],
    })

    class Session:
        async def get(self, *args):
            return app_map

        async def commit(self):
            pass

    response = await submit_input_request(uuid.uuid4(), "roles", SubmitInputRequestBody(choice_id="admin", approve_submission=True), Session())
    checkpoint = app_map.discovery_checkpoint
    assert response.credential_ref is None
    assert checkpoint["configuration"]["credential_ref"] is None
    assert checkpoint["graph"] == graph
    assert checkpoint["account_contexts"]["cred:employee"]["jobs"] == [employee_job]
    assert checkpoint["input_requests"][0]["selected_choice"] == choice
    assert checkpoint["jobs"][-1]["key"] == response.selected_branches[0]
    assert checkpoint["jobs"][-1]["path"][0]["name"] == "Admin"
