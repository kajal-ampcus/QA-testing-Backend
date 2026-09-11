"""
Shared FastAPI dependency-injection providers: db session, current_user,
and the tool_gateway handle that routers use to reach core/ agent logic.

Phase 0 stub — real providers land alongside infra/db/session.py (Phase 1).
"""

# TODO (Phase 1):
# async def get_db_session() -> AsyncIterator[AsyncSession]: ...
# async def get_current_user(...) -> User: ...
# def get_tool_gateway() -> ToolGateway: ...  # from core.tool_gateway.gateway
