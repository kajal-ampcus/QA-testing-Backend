"""Requirement-backed form combinations, distinct from executable assertions.

An expected outcome stated in a requirement is enough to design a manual test;
it is not evidence that a locator or an automated outcome oracle exists.
"""

import re
from itertools import combinations

from core.agents.test_design.fast_generate import _observed, _target, _url_path
from core.agents.test_design.schemas import TestCaseSpec, TestStep

_INPUTS = {'textbox', 'searchbox', 'spinbutton', 'combobox'}
_STOP = {'enter', 'your', 'registered', 'address', 'valid', 'invalid', 'the', 'field', 'input'}


def _words(text):
    words = set(re.findall(r'[a-z0-9]+', text.lower())) - _STOP
    return words - {'answer', 'code'} if 'captcha' in words else words


def _named(element, text):
    words = _words(str(element.get('name') or ''))
    return bool(words) and words.issubset(_words(text))


def invalid_expected(requirement: str, names: list[str]) -> str:
    outcomes = []
    if re.search(r'\b(prevent\w*|reject\w*|den\w*|fail\w*|cannot|remain\w*)\b', requirement, re.I):
        outcomes.append('Submission is rejected')
    if re.search(r'\b(errors?|messages?|validation)\b', requirement, re.I):
        outcomes.append('Required validation feedback is displayed')
    return '; '.join(outcomes) + ' for invalid ' + ', '.join(names) + '.'


def _case(ac_id, text, state, submit, fields, changed, mode, category, expected):
    steps = [TestStep(step_number=1, action='navigate', target=_target(state))]
    data = {}
    for element in fields:
        code = element['element_code']
        key = 'field_' + re.sub(r'\W+', '_', code)
        value = '' if mode == 'missing' and code in changed else (
            f"INVALID_TEST_FIXTURE:{element['name']}" if code in changed else f"VALID_TEST_FIXTURE:{element['name']}")
        data[key] = value
        steps.append(TestStep(step_number=len(steps) + 1, action='fill', target=_target(state, element), value='{' + key + '}'))
    steps.append(TestStep(step_number=len(steps) + 1, action='click', target=_target(state, submit)))
    steps.append(TestStep(step_number=len(steps) + 1, action='assert', target=_target(state, submit), expected=expected,
        assertion={'kind': 'manual', 'target': submit['element_code'], 'text': expected,
                   'fields': '|'.join(element['name'] for element in fields if element['element_code'] in changed),
                   'requirement_text': text, 'basis': mode}))
    names = ', '.join(element['name'] for element in fields if element['element_code'] in changed)
    action = f'Submit with {names} blank' if mode == 'missing' else (
        f'Submit with invalid {names}' if mode == 'invalid' else f"Submit valid inputs using {submit['name']}")
    return TestCaseSpec(title=action, objective=expected, category=category,
        preconditions=[f'Browser is on {_url_path(state)}',
            'Manual verification required: the expected outcome comes from the requirement; an automated result assertion is not yet established.',
            'Replace VALID_TEST_FIXTURE values with configured valid test data, including any current challenge. Replace INVALID_TEST_FIXTURE values with confirmed invalid data. Keep other inputs valid.'],
        steps=steps, expected_result=expected, test_data=data, traceability=[ac_id], confidence=0.7)


def form_behavior_cases(ac_id: str, text: str, state: dict | None) -> list[tuple[str, TestCaseSpec]]:
    if state is None:
        return []
    observed = [element for element in _observed(state) if not (element.get('validation') or {}).get('disabled')]
    fields = [element for element in observed if element.get('role') in _INPUTS
              and (str(element.get('name') or '').lower() != 'search' or _named(element, text))]
    output = []
    refresh = [element for element in observed if element.get('role') == 'button' and re.search(r'refresh', str(element.get('name') or ''), re.I)]
    positive_ui = not re.search(r'\b(invalid|incorrect|wrong|missing|blank|reject\w*|prevent\w*)\b', text, re.I)
    refresh_declared = bool(re.search(r'\brefresh\w*\b', text, re.I))
    if len(refresh) == 1 and ((refresh_declared and _named(refresh[0], text)) or (
        positive_ui and re.search(r'\bcaptcha\b', text, re.I) and re.search(r'\bcaptcha\b', str(refresh[0].get('name') or ''), re.I))):
        button = refresh[0]
        expected = 'The displayed challenge changes after refresh.'
        spec = TestCaseSpec(title=f"Verify {button['name']} changes the displayed challenge", objective=expected,
            category='POSITIVE', preconditions=[f'Browser is on {_url_path(state)}',
                'Manual verification required: compare the displayed challenge before and after the click. No claim is made about old-challenge expiry.'],
            steps=[TestStep(step_number=1, action='navigate', target=_target(state)),
                   TestStep(step_number=2, action='click', target=_target(state, button)),
                   TestStep(step_number=3, action='assert', target=_target(state, button), expected=expected,
                       assertion={'kind': 'manual', 'target': button['element_code'], 'basis': 'refresh' if refresh_declared else 'observed_refresh', 'text': expected, 'requirement_text': text})],
            expected_result=expected, test_data={}, traceability=[ac_id], confidence=0.7)
        output.append(('functional', spec))
    password = next((element for element in fields if (element.get('type') or element.get('input_type')) == 'password' and _named(element, text)), None)
    reveal = [element for element in observed if element.get('role') == 'button' and re.search(r'\b(show|reveal)\s+password\b', str(element.get('name') or ''), re.I)]
    if password and len(reveal) == 1 and positive_ui:
        button = reveal[0]
        expected = f"{password['name']} is revealed without changing its entered value."
        spec = TestCaseSpec(title=f"Verify {button['name']} reveals the entered value", objective=expected, category='POSITIVE',
            preconditions=[f'Browser is on {_url_path(state)}', 'Manual verification required for the observed reveal control. This case does not submit credentials.'],
            steps=[TestStep(step_number=1, action='navigate', target=_target(state)),
                   TestStep(step_number=2, action='fill', target=_target(state, password), value='{sample_text}'),
                   TestStep(step_number=3, action='click', target=_target(state, button)),
                   TestStep(step_number=4, action='assert', target=_target(state, password), expected=expected,
                       assertion={'kind': 'manual', 'target': password['element_code'], 'basis': 'observed_toggle', 'text': expected, 'requirement_text': text})],
            expected_result=expected, test_data={'sample_text': 'Visibility sample'}, traceability=[ac_id], confidence=0.7)
        output.append(('functional', spec))
    submits = [element for element in observed if element.get('role') == 'button' and re.search(
        r'\b(log.?in|sign.?in|submit|send|save|register|continue)\b', str(element.get('name') or ''), re.I)]
    if not fields or len(submits) != 1:
        return output  # do not guess which form or submit control is intended
    submit = submits[0]
    required = [element for element in fields if (element.get('validation') or {}).get('required') or element.get('required')]
    named_required = [element for element in required if _named(element, text)]
    if re.search(r'\b(required|mandatory|missing|blank|empty)\b|\bmust be (entered|provided|filled)\b', text, re.I) and re.search(r'\b(fields|inputs|submit\w*|validat\w*|messages?|reject\w*|prevent\w*)\b', text, re.I):
        selected = required if re.search(r'\b(fields|inputs)\b', text, re.I) else named_required
        # Single omissions isolate each constraint. Pairwise omissions expose
        # interactions; the all-empty submission checks the form as a whole.
        groups = [*[(element,) for element in selected], *combinations(selected, 2)]
        if selected:
            groups.append(tuple(selected))
        seen = set()
        for group in groups:
            codes = frozenset(element['element_code'] for element in group)
            if codes in seen:
                continue
            seen.add(codes)
            names = ', '.join(element['name'] for element in group)
            expected = f'Submission is blocked for missing required values: {names}.'
            if re.search(r'\bmessages?\b', text, re.I):
                expected += ' Required-field validation feedback is displayed; exact wording must follow the approved requirement.'
            output.append(('negative', _case(ac_id, text, state, submit, fields, codes, 'missing', 'NEGATIVE', expected)))
    if re.search(r'\b(invalid|incorrect|wrong)\b', text, re.I) and re.search(r'\b(prevent\w*|reject\w*|den\w*|fail\w*|error|cannot)\b', text, re.I) and not re.search(r'\b(expir\w*|reus\w*|previous|lock\w*|attempts?|whitespace|spaces|refresh\w*)\b', text, re.I):
        credentials = [element for element in fields if re.search(r'\b(password|passphrase|username|user name|employee id|staff id|user id|e-?mail)\b', str(element.get('name') or ''), re.I)] if re.search(r'\bcredentials\b', text, re.I) else []
        selected = [element for element in fields if _named(element, text) or element in credentials]
        # Vary credentials independently of a challenge so an invalid CAPTCHA
        # does not hide the credential-rejection behavior under test.
        groups = [*[(element,) for element in selected], *[pair for pair in combinations(selected, 2)
            if not any(re.search(r'\bcaptcha\b', str(element.get('name') or ''), re.I) for element in pair)]]
        for group in groups:
            codes = frozenset(element['element_code'] for element in group)
            expected = invalid_expected(text, [element['name'] for element in group])
            output.append(('negative', _case(ac_id, text, state, submit, fields, codes, 'invalid', 'NEGATIVE', expected)))
    if ((re.search(r'\b(valid|successful\w*)\b', text, re.I) and re.search(r'\b(authenticat\w*|credentials|log\s*in|sign\s*in)\b', text, re.I)) or re.search(r'\bcan enter\b.*\bsubmit\b', text, re.I)) and not re.search(r'\b(invalid|incorrect|prevent\w*|cannot)\b', text, re.I):
        output.append(('functional', _case(ac_id, text, state, submit, fields, frozenset(), 'valid', 'POSITIVE', text)))
    return output
