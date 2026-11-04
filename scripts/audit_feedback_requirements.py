"""Read-only test-data requirement summary without exposing saved values."""
import asyncio
import json
from uuid import UUID
from sqlalchemy import text
from infra.db.session import AsyncSessionLocal, engine
from infra.test_data_service import smart_template


async def main():
    async with AsyncSessionLocal() as session:
        await session.execute(text('SET TRANSACTION READ ONLY'))
        data = await smart_template(session, UUID('f76b1664-fc27-42c7-ba90-df4f4bee19b4'))
        print(json.dumps({'case_count': data['case_count'], 'total_input_rows': len(data['rows']),
            'feedback_rows': [{k: row.get(k) for k in ['test_case_code', 'field_code', 'field_name',
                'field_type', 'page', 'required', 'missing', 'source', 'runtime_only']}
                for row in data['rows'] if 'feedback' in str(row.get('page', '')).lower()]}, indent=2))
    await engine.dispose()


asyncio.run(main())
