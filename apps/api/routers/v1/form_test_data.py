import io
import json
import re
import uuid
from typing import Literal

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Query,
    UploadFile,
)
from openpyxl import load_workbook
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.dependencies import get_db_session
from infra.db.models.form_test_data import FormTestData
from infra.db.models.project import Project
from infra.db.models.application_map import ApplicationMap, ApplicationMapState
from infra.db.models.test_case import TestCase, TestCaseVersion
from infra.db.repositories.application_map_repo import ApplicationMapRepository
from schemas.form_test_data import (
    FormTestDataCreate,
    FormTestDataResponse,
    FormTestDataUpdate,
)

router = APIRouter(
    prefix="/projects/{project_id}/form-test-data",
    tags=["form-test-data"],
)

MAX_FILE_SIZE = 5 * 1024 * 1024
ALLOWED_TEST_TYPES = {"positive", "negative", "validation", "edge"}


async def ensure_project(session: AsyncSession, project_id: uuid.UUID):
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(404, "Project not found")
    return project


async def ensure_case(
    session: AsyncSession,
    project_id: uuid.UUID,
    test_case_id: uuid.UUID | None,
    exclude_id: uuid.UUID | None = None,
) -> None:
    if test_case_id is None:
        return
    case = await session.scalar(
        select(TestCase).where(
            TestCase.id == test_case_id,
            TestCase.project_id == project_id,
        )
    )
    if case is None:
        raise HTTPException(422, "The selected test case does not belong to this project")
    duplicate = await session.scalar(
        select(FormTestData.id).where(
            FormTestData.project_id == project_id,
            FormTestData.test_case_id == test_case_id,
            FormTestData.active.is_(True),
            FormTestData.id != exclude_id if exclude_id else FormTestData.id.is_not(None),
        )
    )
    if duplicate:
        raise HTTPException(409, "A form fixture is already assigned to this test case")


async def get_record(
    session: AsyncSession,
    project_id: uuid.UUID,
    data_id: uuid.UUID,
):
    result = await session.scalar(
        select(FormTestData).where(
            FormTestData.id == data_id,
            FormTestData.project_id == project_id,
            FormTestData.active.is_(True),
        )
    )
    if result is None:
        raise HTTPException(404, "Active form test data not found")
    return result


@router.post("", response_model=FormTestDataResponse, status_code=201)
async def create_form_test_data(
    project_id: uuid.UUID,
    body: FormTestDataCreate,
    session: AsyncSession = Depends(get_db_session),
):
    await ensure_project(session, project_id)
    await ensure_case(session, project_id, body.test_case_id)

    row = FormTestData(
        project_id=project_id,
        **body.model_dump(),
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


@router.get("", response_model=list[FormTestDataResponse])
async def list_form_test_data(
    project_id: uuid.UUID,
    form_group: str | None = None,
    test_type: Literal[
        "positive", "negative", "validation", "edge"
    ] | None = None,
    tag: list[str] | None = Query(default=None),
    test_case_id: uuid.UUID | None = None,
    session: AsyncSession = Depends(get_db_session),
):
    await ensure_project(session, project_id)

    query = select(FormTestData).where(
        FormTestData.project_id == project_id,
        FormTestData.active.is_(True),
    )

    if form_group:
        query = query.where(FormTestData.form_group == form_group)
    if test_case_id:
        query = query.where(FormTestData.test_case_id == test_case_id)

    if test_type:
        query = query.where(FormTestData.test_type == test_type)

    if tag:
        # PostgreSQL JSONB contains all supplied tags.
        query = query.where(FormTestData.tags.contains(tag))

    result = await session.scalars(
        query.order_by(FormTestData.created_at.desc())
    )
    return list(result.all())


@router.get("/smart-template")
async def get_smart_template(
    project_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
):
    from infra.test_data_service import smart_template

    await ensure_project(session, project_id)
    try:
        return await smart_template(session, project_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/{data_id}", response_model=FormTestDataResponse)
async def get_form_test_data(
    project_id: uuid.UUID,
    data_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
):
    await ensure_project(session, project_id)
    return await get_record(session, project_id, data_id)


@router.patch("/{data_id}", response_model=FormTestDataResponse)
async def update_form_test_data(
    project_id: uuid.UUID,
    data_id: uuid.UUID,
    body: FormTestDataUpdate,
    session: AsyncSession = Depends(get_db_session),
):
    await ensure_project(session, project_id)
    row = await get_record(session, project_id, data_id)

    changes = body.model_dump(exclude_unset=True)
    if "test_case_id" in changes:
        await ensure_case(session, project_id, changes["test_case_id"], data_id)

    for field, value in changes.items():
        if value is None and field not in {"expected_result", "test_case_id"}:
            raise HTTPException(
                422, f"{field} cannot be null"
            )
        setattr(row, field, value)

    await session.commit()
    await session.refresh(row)
    return row


@router.delete("/{data_id}", status_code=204)
async def deactivate_form_test_data(
    project_id: uuid.UUID,
    data_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
):
    await ensure_project(session, project_id)
    row = await get_record(session, project_id, data_id)
    row.active = False
    await session.commit()


# ---------------------------------------------------------------------------
# Smart template helpers
# ---------------------------------------------------------------------------

_CREDENTIAL_NAMES = re.compile(
    r"\b(password|passphrase|pin|username|user.?name|e.?mail|login.?id|employee.?id|user.?id|captcha)\b",
    re.IGNORECASE,
)


def _is_credential_field(element: dict) -> bool:
    """Return True for login-credential fields that should NOT appear in form test data."""
    name = str(element.get("name") or "")
    input_type = str(element.get("input_type") or element.get("type") or "")
    if input_type.lower() == "password":
        return True
    blob = f"{name} {input_type}"
    return bool(_CREDENTIAL_NAMES.search(blob))


def _form_fields_for_case(
    version: TestCaseVersion,
    states_by_code: dict[str, "ApplicationMapState"],
) -> list[dict]:
    """Extract non-credential fill/select fields for a test case version.

    Returns a list of dicts: {field_name, state_code, url_pattern, element_type}
    deduplicated and ordered by step number.
    """
    seen: set[tuple[str, str]] = set()
    fields: list[dict] = []
    for step in version.steps or []:
        action = str(step.get("action") or "")
        if action not in {"fill", "select"}:
            continue
        target = step.get("target") or {}
        state_code = str(target.get("state_code") or "")
        element_name = str(target.get("element_name") or "")
        if not element_name:
            continue
        state = states_by_code.get(state_code)
        element = next(
            (
                e for e in (state.elements if state else [])
                if str(e.get("element_code") or "") == str(target.get("element_code") or "")
                or str(e.get("name") or "").strip().lower() == element_name.strip().lower()
            ),
            {},
        )
        if _is_credential_field({**element, "name": element_name}):
            continue
        key = (state_code, element_name.strip().lower())
        if key in seen:
            continue
        seen.add(key)
        url = str(state.url_pattern if state else "") or ""
        fields.append({
            "field_name": element_name.strip(),
            "state_code": state_code,
            "url_pattern": url,
            "element_type": str(element.get("role") or element.get("type") or "textbox"),
        })
    return fields


async def _build_smart_rows(
    project_id: uuid.UUID,
    session: AsyncSession,
) -> list[dict]:
    """Core logic: read all approved/draft test cases, extract their fill fields,
    look up any existing FormTestData values, and return one dict per field.
    """
    # Load all test cases with their current version
    cases_result = await session.execute(
        select(TestCase, TestCaseVersion)
        .join(
            TestCaseVersion,
            (TestCaseVersion.test_case_id == TestCase.id)
            & (TestCaseVersion.version == TestCase.current_version),
        )
        .where(
            TestCase.project_id == project_id,
            TestCase.status.notin_(["OUTDATED", "REJECTED"]),
        )
        .order_by(TestCase.tc_code)
    )
    pairs: list[tuple[TestCase, TestCaseVersion]] = list(cases_result.all())
    if not pairs:
        return []

    # Load latest application map for the project
    latest_map = await session.scalar(
        select(ApplicationMap)
        .where(
            ApplicationMap.project_id == project_id,
            ApplicationMap.status.in_(["COMPLETE", "PARTIAL"]),
        )
        .order_by(ApplicationMap.version.desc())
    )
    states_by_code: dict[str, ApplicationMapState] = {}
    if latest_map is not None:
        full_map = await ApplicationMapRepository(session).get_with_states(latest_map.id)
        if full_map:
            states_by_code = {s.state_code: s for s in full_map.states}

    # Load existing FormTestData for the project so we can pre-fill values
    existing_fixtures = await session.scalars(
        select(FormTestData).where(
            FormTestData.project_id == project_id,
            FormTestData.active.is_(True),
            FormTestData.test_case_id.isnot(None),
        )
    )
    fixture_by_case: dict[uuid.UUID, dict[str, str]] = {}
    for fixture in existing_fixtures:
        existing_values = dict(fixture.test_data or {})
        if fixture.test_case_id:
            fixture_by_case[fixture.test_case_id] = existing_values

    rows: list[dict] = []
    for tc, version in pairs:
        fields = _form_fields_for_case(version, states_by_code)
        if not fields:
            continue
        saved_values = fixture_by_case.get(tc.id, {})
        form_group = re.sub(r"[^a-z0-9]+", "-", (fields[0]["url_pattern"] or version.title).lower()).strip("-") or "form"
        for field in fields:
            saved_value = (
                saved_values.get(field["field_name"])
                or saved_values.get(field["field_name"].lower())
                or ""
            )
            rows.append({
                "test_case_code": tc.tc_code,
                "test_case_title": version.title,
                "category": version.category,
                "field_name": field["field_name"],
                "value": saved_value,
                "form_group": form_group,
                "url_pattern": field["url_pattern"],
            })
    return rows


@router.get("/smart-fields")
async def get_smart_fields(
    project_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
):
    """Return all non-credential fill fields across all test cases as JSON.

    Used by the UI to render an inline fill form.  Each item carries:
      - test_case_code, test_case_title, category  (read-only context)
      - field_name, url_pattern                    (what field to fill)
      - value                                       (current saved value, empty if not yet set)

    The frontend groups rows by test_case_code and renders one input per field.
    The user only fills the value column; everything else is auto-populated.
    """
    await ensure_project(session, project_id)
    rows = await _build_smart_rows(project_id, session)
    return {"fields": rows, "total": len(rows)}


@router.post("/smart-fields")
async def save_smart_fields(
    project_id: uuid.UUID,
    body: dict,
    session: AsyncSession = Depends(get_db_session),
):
    """Save values submitted from the inline UI fill form.

    Expects body: {"values": [{"test_case_code": "TC-001", "field_name": "Email", "value": "user@example.com"}, ...]}

    Groups by test_case_code, upserts one FormTestData record per test case
    (deactivates the old active record first so there is always exactly one).
    """
    await ensure_project(session, project_id)
    submitted: list[dict] = body.get("values") or []
    if not submitted:
        raise HTTPException(400, "No values provided")

    # Group by test_case_code
    by_code: dict[str, dict[str, str]] = {}
    for item in submitted:
        code = str(item.get("test_case_code") or "").strip()
        field = str(item.get("field_name") or "").strip()
        value = str(item.get("value") or "")
        if not code or not field:
            continue
        by_code.setdefault(code, {})[field] = value

    if not by_code:
        raise HTTPException(400, "No valid field values found")

    saved = 0
    for tc_code, fields in by_code.items():
        tc = await session.scalar(
            select(TestCase).where(
                TestCase.project_id == project_id,
                TestCase.tc_code == tc_code,
            )
        )
        if tc is None:
            continue
        version = await session.scalar(
            select(TestCaseVersion).where(
                TestCaseVersion.test_case_id == tc.id,
                TestCaseVersion.version == tc.current_version,
            )
        )
        category = str(version.category if version else "POSITIVE").lower()
        test_type = category if category in ALLOWED_TEST_TYPES else "positive"

        # Deactivate any existing active record for this test case
        existing = await session.scalar(
            select(FormTestData).where(
                FormTestData.project_id == project_id,
                FormTestData.test_case_id == tc.id,
                FormTestData.active.is_(True),
            )
        )
        if existing is not None:
            existing.active = False

        session.add(FormTestData(
            project_id=project_id,
            test_case_id=tc.id,
            label=tc_code,
            form_group="ui-fill",
            test_type=test_type,
            test_data=fields,
        ))
        saved += 1

    if saved:
        await session.commit()

    return {"saved_count": saved}


@router.get("/smart-template")
async def download_smart_template(
    project_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
):
    """Download a pre-filled Excel template built from the real test cases.

    Unlike the generic /template endpoint, this reads your actual test cases
    and application map and generates one row per non-credential field per
    test case.  The user only needs to fill the 'value' column.

    Columns (read-only except value):
      test_case_code | test_case_title | category | field_name | value | form_group | url_pattern
    """
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.utils import get_column_letter
    from fastapi.responses import StreamingResponse

    await ensure_project(session, project_id)
    rows = await _build_smart_rows(project_id, session)

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Test Data"

    headers = [
        "test_case_code",
        "test_case_title",
        "category",
        "field_name",
        "value",
        "form_group",
        "url_pattern",
    ]
    # Header row style
    header_fill = PatternFill("solid", fgColor="2563EB")
    header_font = Font(bold=True, color="FFFFFF")
    for col, header in enumerate(headers, start=1):
        cell = sheet.cell(row=1, column=col, value=header)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center")

    if not rows:
        # No test cases yet — add a placeholder row so the user knows the format
        sheet.append(["", "No test cases found — generate test cases first", "", "", "", "", ""])
    else:
        prev_code = None
        for row in rows:
            code = row["test_case_code"]
            # Light alternating row colour per test case
            fill_color = "EFF6FF" if code != prev_code and (prev_code is None or _row_parity(rows, code)) else "FFFFFF"
            sheet_row = [
                row["test_case_code"],
                row["test_case_title"],
                row["category"],
                row["field_name"],
                row["value"],        # ← user fills this
                row["form_group"],
                row["url_pattern"],
            ]
            sheet.append(sheet_row)
            excel_row = sheet.max_row
            # Mark read-only columns grey; value column stays white
            readonly_fill = PatternFill("solid", fgColor="F3F4F6")
            value_fill = PatternFill("solid", fgColor="FEF9C3")  # yellow — "fill me in"
            for col_idx in range(1, len(headers) + 1):
                cell = sheet.cell(row=excel_row, column=col_idx)
                if col_idx == 5:  # value column
                    cell.fill = value_fill
                else:
                    cell.fill = readonly_fill
            prev_code = code

    # Column widths
    widths = [14, 40, 12, 25, 30, 20, 35]
    for i, width in enumerate(widths, start=1):
        sheet.column_dimensions[get_column_letter(i)].width = width

    sheet.freeze_panes = "E2"  # freeze everything left of and above value column

    # Instructions sheet
    info = workbook.create_sheet("Instructions")
    info.column_dimensions["A"].width = 90
    instructions = [
        "HOW TO FILL THIS TEMPLATE",
        "",
        "1. Only fill the YELLOW 'value' column. All other columns are auto-generated.",
        "2. Each row is one field for one test case.",
        "3. The 'category' column tells you whether this is a POSITIVE or NEGATIVE test.",
        "   - POSITIVE: provide a valid value (e.g. a real email address)",
        "   - NEGATIVE: provide an invalid value (e.g. 'not-an-email' or leave blank to test required field)",
        "4. Login credentials (username/password) are NOT shown here — they come from the Credentials vault.",
        "5. Save and upload this file using the 'Import Test Data' button.",
        "",
        "EXAMPLE:",
        "  test_case_code | field_name  | value (FILL THIS)  | category",
        "  TC-001         | First Name  | John               | POSITIVE",
        "  TC-001         | Last Name   | Doe                | POSITIVE",
        "  TC-002         | First Name  |                    | NEGATIVE (leave blank to test empty field)",
    ]
    for line in instructions:
        info.append([line])
    info["A1"].font = Font(bold=True, size=13)

    output = io.BytesIO()
    workbook.save(output)
    workbook.close()
    output.seek(0)
    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="test-data-template.xlsx"'},
    )


def _row_parity(rows: list[dict], code: str) -> bool:
    """Return True if the code is at an even position in the unique-codes list."""
    seen: list[str] = []
    for row in rows:
        c = row["test_case_code"]
        if not seen or seen[-1] != c:
            seen.append(c)
    try:
        return seen.index(code) % 2 == 0
    except ValueError:
        return False


@router.get("/template")
async def form_test_data_import_template(
    project_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
):
    """Download the Excel template accepted by the form test data import endpoint.

    One row per field. Rows with the same test_case_code are grouped together
    into a single FormTestData record, so you can have as many fields as you
    need for one test case.
    """
    from openpyxl import Workbook
    from fastapi.responses import StreamingResponse

    await ensure_project(session, project_id)
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Form test data"
    sheet.append([
        "test_case_code",
        "field_name",
        "value",
        "test_type",
        "form_group",
        "label",
        "tags",
    ])
    sheet.append(["TC-001", "Email",       "user@example.com", "positive", "login-form", "Login form", "smoke"])
    sheet.append(["TC-001", "Password",    "P@ssword1!",        "positive", "login-form", "Login form", "smoke"])
    sheet.append(["TC-002", "Search Term", "laptop",            "positive", "search",     "Search",     ""])
    sheet.append(["TC-003", "Quantity",    "0",                 "negative", "order-form", "Edge case",  "negative"])
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = f"A1:G{sheet.max_row}"
    output = io.BytesIO()
    workbook.save(output)
    workbook.close()
    output.seek(0)
    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="form-test-data-template.xlsx"'},
    )


@router.post("/import", status_code=200)
async def import_form_test_data(
    project_id: uuid.UUID,
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_db_session),
):
    """Import form test data from an .xlsx file.

    Accepts the flat one-row-per-field format produced by the /template endpoint.
    Rows sharing the same test_case_code + form_group are merged into one
    FormTestData record. Each test_case_code may only appear once per form_group;
    importing again for the same code deactivates the previous record.

    Also accepts the legacy format with a JSON test_data column.
    """
    await ensure_project(session, project_id)

    if not file.filename or not file.filename.lower().endswith(".xlsx"):
        raise HTTPException(400, "Upload an .xlsx file")

    content = await file.read(MAX_FILE_SIZE + 1)

    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(413, "Excel file exceeds the 5 MB limit")

    try:
        workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        sheet = workbook.active
        rows_iter = sheet.iter_rows(values_only=True)
        raw_headers = next(rows_iter, None)
        if not raw_headers:
            raise HTTPException(400, "Excel file is empty")
        headers = [str(v).strip().lower() if v is not None else "" for v in raw_headers]

        # Detect format: flat (field_name + value) or legacy (test_data JSON column)
        flat_mode = "field_name" in headers and "value" in headers
        legacy_mode = "test_data" in headers
        if not flat_mode and not legacy_mode:
            raise HTTPException(400, {
                "message": "Missing required columns. Use the /template endpoint to download the correct format.",
                "flat_format_required": ["test_case_code", "field_name", "value", "test_type", "form_group", "label"],
                "received_columns": headers,
            })

        col = {name: headers.index(name) for name in set(headers) if name}

        def cell(values: tuple, name: str, default=None):
            idx = col.get(name)
            return values[idx] if idx is not None and idx < len(values) else default

        all_values = list(rows_iter)
        errors: list[dict] = []
        imported_count = 0

        if flat_mode:
            # ── Flat format: group rows by (test_case_code, form_group, label) ──────
            # Each unique combination becomes one FormTestData record.
            # Rows that share test_case_code but differ in form_group/label are
            # separate records (e.g. positive vs negative fixture for same case).
            from collections import defaultdict

            # Key: (test_case_code, form_group, label, test_type)
            groups: dict[tuple, dict] = {}
            group_rows: dict[tuple, list[int]] = defaultdict(list)

            for row_number, values in enumerate(all_values, start=2):
                if not any(v is not None for v in values):
                    continue
                tc_code = str(cell(values, "test_case_code") or "").strip()
                field_name = str(cell(values, "field_name") or "").strip()
                value = cell(values, "value")
                test_type = str(cell(values, "test_type") or "positive").strip().lower()
                form_group = str(cell(values, "form_group") or "default").strip()
                label = str(cell(values, "label") or form_group).strip()
                tags_raw = cell(values, "tags")
                tags = [p.strip() for p in str(tags_raw or "").split(",") if p.strip()]

                try:
                    if not tc_code:
                        raise ValueError("test_case_code is required")
                    if not field_name:
                        raise ValueError("field_name is required")
                    if test_type not in ALLOWED_TEST_TYPES:
                        raise ValueError(f"test_type must be one of {sorted(ALLOWED_TEST_TYPES)}")
                    value_str = str(value) if value is not None else ""
                    key = (tc_code, form_group, label, test_type)
                    if key not in groups:
                        groups[key] = {"tc_code": tc_code, "form_group": form_group, "label": label,
                                       "test_type": test_type, "tags": tags, "fields": {}}
                    groups[key]["fields"][field_name] = value_str
                    group_rows[key].append(row_number)
                except (ValueError, TypeError) as exc:
                    errors.append({"row": row_number, "error": str(exc)})

            # Now persist each group
            for key, group in groups.items():
                tc_code = group["tc_code"]
                row_numbers = group_rows[key]
                try:
                    matched_case = await session.scalar(
                        select(TestCase).where(
                            TestCase.project_id == project_id,
                            TestCase.tc_code == tc_code,
                        )
                    )
                    if matched_case is None:
                        for rn in row_numbers:
                            errors.append({"row": rn, "error": f"No test case with code {tc_code!r} in this project"})
                        continue

                    # Deactivate any existing active record for this case+group
                    existing = await session.scalar(
                        select(FormTestData).where(
                            FormTestData.project_id == project_id,
                            FormTestData.test_case_id == matched_case.id,
                            FormTestData.form_group == group["form_group"],
                            FormTestData.active.is_(True),
                        )
                    )
                    if existing is not None:
                        existing.active = False

                    row = FormTestData(
                        project_id=project_id,
                        test_case_id=matched_case.id,
                        label=group["label"],
                        form_group=group["form_group"],
                        test_type=group["test_type"],
                        test_data=group["fields"],
                        tags=group["tags"],
                    )
                    session.add(row)
                    imported_count += 1
                except (ValueError, TypeError, json.JSONDecodeError, ValidationError) as exc:
                    for rn in row_numbers:
                        errors.append({"row": rn, "error": str(exc)})

        else:
            # ── Legacy format: one row = one FormTestData record with JSON test_data ──
            required = {"label", "form_group", "test_type", "test_data"}
            if not required.issubset(set(headers)):
                raise HTTPException(400, {
                    "message": "Missing required columns for legacy format",
                    "required_columns": sorted(required),
                    "received_columns": headers,
                })

            seen: set[tuple] = set()
            for row_number, values in enumerate(all_values, start=2):
                if not any(v is not None for v in values):
                    continue
                try:
                    label = str(cell(values, "label") or "").strip()
                    form_group = str(cell(values, "form_group") or "").strip()
                    test_type = str(cell(values, "test_type") or "").strip().lower()
                    if not label or not form_group:
                        raise ValueError("label and form_group are required")
                    if test_type not in ALLOWED_TEST_TYPES:
                        raise ValueError(f"test_type must be one of {sorted(ALLOWED_TEST_TYPES)}")

                    raw_td = cell(values, "test_data")
                    if isinstance(raw_td, dict):
                        test_data = raw_td
                    elif isinstance(raw_td, str):
                        test_data = json.loads(raw_td)
                    else:
                        raise ValueError("test_data must be a JSON object")
                    if not isinstance(test_data, dict):
                        raise ValueError("test_data must be a JSON object")

                    tags_raw = cell(values, "tags")
                    tags = [p.strip() for p in str(tags_raw or "").split(",") if p.strip()]
                    expected_raw = cell(values, "expected_result")
                    expected_result = str(expected_raw).strip() if expected_raw is not None else None

                    tc_code = str(cell(values, "test_case_code") or "").strip()
                    test_case_id: uuid.UUID | None = None
                    if tc_code:
                        matched = await session.scalar(
                            select(TestCase).where(
                                TestCase.project_id == project_id,
                                TestCase.tc_code == tc_code,
                            )
                        )
                        if matched is None:
                            raise ValueError(f"No test case with code {tc_code!r} in this project")
                        test_case_id = matched.id

                    dup_key = (form_group, label)
                    if dup_key in seen:
                        raise ValueError("Duplicate label and form_group in upload")
                    seen.add(dup_key)

                    if test_case_id:
                        existing = await session.scalar(
                            select(FormTestData).where(
                                FormTestData.project_id == project_id,
                                FormTestData.test_case_id == test_case_id,
                                FormTestData.form_group == form_group,
                                FormTestData.active.is_(True),
                            )
                        )
                        if existing is not None:
                            existing.active = False

                    session.add(FormTestData(
                        project_id=project_id,
                        test_case_id=test_case_id,
                        label=label,
                        form_group=form_group,
                        test_type=test_type,
                        test_data=test_data,
                        expected_result=expected_result,
                        tags=tags,
                    ))
                    imported_count += 1
                except (ValueError, TypeError, json.JSONDecodeError, ValidationError) as exc:
                    errors.append({"row": row_number, "error": str(exc)})

        if imported_count:
            await session.commit()

        return {"imported_count": imported_count, "error_count": len(errors), "errors": errors}

    except HTTPException:
        raise
    except Exception as exc:
        await session.rollback()
        raise HTTPException(400, "Could not read the Excel file") from exc
    finally:
        if "workbook" in locals():
            workbook.close()
        await file.close()
