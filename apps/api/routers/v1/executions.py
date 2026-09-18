"""
Test execution endpoints — triggers Test Execution Agent (enqueues
apps/worker/tasks/run_execution.py) and surfaces test_runs/test_results
(Section 14). Phase 0 stub.
"""

from fastapi import APIRouter

router = APIRouter(prefix="/executions", tags=["executions"])

# TODO (Phase 1): POST to trigger a run, GET run status/results
