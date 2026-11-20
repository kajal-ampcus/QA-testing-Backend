from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from apps.api.routers.v1 import test_cases as api


def test_preview_reports_missing_steps_and_unmapped_result():
    row = api.ImportCaseRow(row_number=2, title='Check login', expected_result='An appropriate result is shown')
    state = SimpleNamespace(state_code='LOGIN', url_pattern='/', reached_via=[], elements=[{'element_code': 'PWD', 'role': 'textbox', 'name': 'Password', 'input_type': 'password', 'css_selector': '#password'}])
    readiness, issues, mappings = api._assess_import_mapping(row, [state], 'LOGIN')
    assert readiness == 'review_needed'
    assert any('steps are missing' in issue for issue in issues)
    assert any('expected result' in issue for issue in issues)
    assert mappings[0]['control'] == 'Not mapped'


def test_preview_maps_numbered_actions_by_observed_page_and_field_label():
    row = api.ImportCaseRow(row_number=2, title='Filter candidates', expected_result='Matching records are displayed',
        step_notes=['Open Recruiter Dashboard', 'Enter an invalid Candidate ID in the filter'])
    state = SimpleNamespace(state_code='DASH', url_pattern='/', reached_via=[], elements=[
        {'element_code': 'HEADING', 'role': 'heading', 'name': 'Dashboard'},
        {'element_code': 'FIELD', 'role': 'textbox', 'name': 'e.g. CAN-001', 'field_label': 'Candidate ID', 'css_selector': '#candidate-id'},
    ])
    readiness, issues, mappings = api._assess_import_mapping(row, [state], 'DASH')
    assert readiness == 'review_needed'
    assert mappings[0]['action'] == 'navigate'
    assert mappings[1]['action'] == 'fill'
    assert mappings[1]['control'] == 'e.g. CAN-001'
    assert any('no configured value' in issue for issue in issues)


def test_distinct_import_inputs_are_not_treated_as_duplicates():
    steps = api._manual_steps(state_code='DASH', expected_result='Matching rows are displayed', step_notes=['Select status'])
    first = api._import_identity('Filter status', 'Matching rows are displayed', 'POSITIVE', [], steps, 'Approved')
    second = api._import_identity('Filter status', 'Matching rows are displayed', 'POSITIVE', [], steps, 'Returned')
    assert first != second


@pytest.mark.asyncio
async def test_import_requires_explicit_confirmation_of_uncertain_page(monkeypatch):
    project_id, req_id, map_id = uuid4(), uuid4(), uuid4()
    project = SimpleNamespace(id=project_id, credential_ref=None)
    requirement = SimpleNamespace(id=req_id, current_version=1)
    state = SimpleNamespace(state_code='A', url_pattern='/a', reached_via=[], elements=[{'name': 'Password'}])
    app_map = SimpleNamespace(id=map_id, states=[state])
    monkeypatch.setattr(api, '_import_context', AsyncMock(return_value=(project, (requirement, None), app_map)))
    repo = SimpleNamespace(lock_generation=AsyncMock(), list_for_requirement=AsyncMock(return_value=[]), create=AsyncMock())
    monkeypatch.setattr(api, 'TestCaseRepository', lambda _: repo)
    session = SimpleNamespace(commit=AsyncMock())
    body = api.ImportConfirmRequest(requirement_id=req_id, application_map_id=map_id, rows=[
        api.ImportCaseRow(row_number=2, title='Check password', expected_result='Error displayed', state_code='A')])
    result = await api.confirm_import(project_id, body, session)
    assert result.results[0].status == 'invalid'
    assert 'Confirm' in result.results[0].error
    repo.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_confirmed_import_saves_source_steps_and_input(monkeypatch):
    project_id, req_id, map_id = uuid4(), uuid4(), uuid4()
    project = SimpleNamespace(id=project_id, credential_ref=None)
    requirement = SimpleNamespace(id=req_id, current_version=1)
    state = SimpleNamespace(state_code='A', url_pattern='/a', reached_via=[], elements=[{'name': 'Password'}])
    app_map = SimpleNamespace(id=map_id, states=[state])
    monkeypatch.setattr(api, '_import_context', AsyncMock(return_value=(project, (requirement, None), app_map)))
    async def create(project_id, code, requirement_id, requirement_version, map_id, version, **kwargs):
        case = SimpleNamespace(id=uuid4(), tc_code=code, project_id=project_id, requirement_id=requirement_id,
            requirement_version=requirement_version, application_map_id=map_id, status='DRAFT', current_version=1,
            credential_ref=None, primary_state_code=kwargs['primary_state_code'], source=kwargs['source'], import_metadata=kwargs['import_metadata'])
        return case, SimpleNamespace(version=1, **version)
    repo = SimpleNamespace(lock_generation=AsyncMock(), list_for_requirement=AsyncMock(return_value=[]),
        next_tc_code=AsyncMock(return_value='TC-1'), create=AsyncMock(side_effect=create))
    monkeypatch.setattr(api, 'TestCaseRepository', lambda _: repo)
    session = SimpleNamespace(commit=AsyncMock())
    body = api.ImportConfirmRequest(requirement_id=req_id, application_map_id=map_id, filename='cases.xlsx', rows=[
        api.ImportCaseRow(row_number=2, title='Check password', expected_result='Error displayed', state_code='A',
            page_confirmed=True, source_case_id='LOGIN_NEG_1', scenario_name='Login', input='invalid password', step_notes=['Enter invalid password', 'Click Sign in'])])
    result = await api.confirm_import(project_id, body, session)
    saved = result.results[0].test_case
    assert result.results[0].status == 'imported'
    assert saved.import_metadata['source_case_id'] == 'LOGIN_NEG_1'
    assert saved.import_metadata['scenario_name'] == 'Login'
    assert saved.current.test_data['imported_input'] == 'invalid password'
    assert [step['expected'] for step in saved.current.steps[1:]] == ['Enter invalid password', 'Click Sign in', 'Error displayed']
