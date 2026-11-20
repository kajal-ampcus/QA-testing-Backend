from copy import deepcopy
from uuid import uuid4

from core.agents.automation_generation.suite import _plan_case
from pathlib import Path
import shutil
import subprocess
import pytest
from core.agents.test_execution.playwright_runner import _result_status, outcome_from_progress
from domain.enums import TestResultStatus
from tests.unit.agents.test_login_outcome_automation import inputs


def negative_case(mode='invalid', fields='Password'):
    case, states = inputs()
    case.category = 'NEGATIVE'
    case.title = 'Submit with invalid Password' if mode == 'invalid' else 'Submit with Password blank'
    case.objective = case.title
    case.expected_result = 'Login is rejected with validation feedback'
    case.steps[-1]['assertion'] = {'kind': 'manual', 'basis': mode, 'fields': fields, 'requirement_text': 'Sign in rejects invalid password'}
    return case, states


def test_negative_login_renders_runtime_feedback_and_valid_companion():
    case, states = negative_case()
    draft = _plan_case(case, states, set(), uuid4())
    assert not draft.blocked, draft.blocked_reason
    assert 'beginNegativeObservation' in draft.source
    assert 'expectRuntimeLoginRejection' in draft.source
    assert 'envCredential("TEST_USERNAME")' in draft.source
    assert 'envCredential("TEST_PASSWORD")' not in draft.source
    assert draft.source.index('beginNegativeObservation') < draft.source.index('fill') < draft.source.index('clickSignIn')
    assert ', true)' in draft.source


def test_ambiguous_field_and_missing_companion_are_blocked():
    case, states = negative_case(fields='Unknown')
    assert _plan_case(case, states, set(), uuid4()).blocked
    case, states = negative_case()
    case.credential_ref = None
    assert _plan_case(case, states, set(), uuid4()).blocked


def test_review_and_network_errors_are_not_passes():
    assert _result_status({'error': 'NEGATIVE_REVIEW_REQUIRED: no signal', 'status': 'failed'}, {}) == TestResultStatus.BLOCKED
    assert _result_status({'error': 'NEGATIVE_INFRASTRUCTURE_ERROR: network', 'status': 'failed'}, {}) == TestResultStatus.ERROR
    live = outcome_from_progress({'file': 'test.spec.ts', 'title': 'case', 'status': 'failed', 'error': 'NEGATIVE_REVIEW_REQUIRED: no signal'}, Path.cwd(), [])
    assert live.status == TestResultStatus.BLOCKED


def test_both_empty_fields_remain_explicit_scenario_values():
    case, states = negative_case('missing', 'Work email|Password')
    draft = _plan_case(case, states, set(), uuid4())
    assert not draft.blocked, draft.blocked_reason
    assert draft.source.count(', true)') == 2


def test_legacy_manual_negative_login_recovers_its_blank_field_intent():
    case, states = negative_case('missing', 'Password')
    case.steps[-1]['assertion']['basis'] = 'requirement_behavior'
    case.test_data['password'] = ''
    draft = _plan_case(case, states, set(), uuid4())
    assert not draft.blocked, draft.blocked_reason
    assert 'expectRuntimeLoginRejection' in draft.source
    assert 'envCredential("TEST_USERNAME")' in draft.source
    assert 'envCredential("TEST_PASSWORD")' not in draft.source


def test_execution_input_keeps_intentional_blank_even_if_saved_value_exists():
    from core.agents.automation_generation.input_runtime import RUNTIME_SOURCE
    node = shutil.which('node')
    typescript = Path(__file__).resolve().parents[4] / 'QA-testing-Frontend' / 'node_modules' / 'typescript'
    if not node or not typescript.exists():
        pytest.skip('Node and the frontend TypeScript package are required')
    driver = r'''
const ts = require(process.argv[1]); const Module = require('module'); const fs = require('fs');
let source=''; process.stdin.on('data', chunk => source += chunk); process.stdin.on('end', async () => {
  const js = ts.transpileModule(source, {compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,esModuleInterop:true}}).outputText;
  const load = Module._load; Module._load = function(name, ...args) { if (name === '@playwright/test') return {test:{}}; return load.call(this,name,...args); };
  fs.existsSync = () => true;
  fs.readFileSync = file => JSON.stringify(String(file).endsWith('manifest.json') ? {input_bindings:[{key:'field',allow_empty:true}]} : {values:{field:{mode:'value',value:'saved'}}});
  delete process.env.EXECUTION_INPUT_DIR;
  const mod = new Module('runtime'); mod.filename='runtime.js'; mod.paths=module.paths; mod._compile(js,'runtime.js');
  const value = await mod.exports.executionInput('field','',true); process.stdout.write(JSON.stringify(value));
});
'''
    result = subprocess.run([node, '-e', driver, str(typescript)], input=RUNTIME_SOURCE, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert result.stdout == '""'
