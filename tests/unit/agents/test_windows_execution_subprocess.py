import asyncio
import os
import subprocess
import sys
from pathlib import Path

import pytest

from core.tool_gateway.playwright_client import _execute


@pytest.mark.skipif(os.name != "nt", reason="Windows selector-loop compatibility")
def test_selector_worker_runs_subprocess_and_streams_output():
    async def check():
        output = []
        parent_loop = asyncio.get_running_loop()

        async def emit(text):
            assert asyncio.get_running_loop() is parent_loop
            output.append(text)

        result = await _execute([sys.executable, "-c", "print('execution started')"], cwd=Path.cwd(),
                                env=dict(os.environ), timeout=10, runner=None, on_output=emit)
        assert result.returncode == 0
        assert "execution started" in result.stdout
        assert "execution started" in "".join(output)

    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(check())


@pytest.mark.skipif(os.name != "nt", reason="Windows selector-loop compatibility")
def test_selector_worker_enforces_subprocess_timeout():
    async def check():
        with pytest.raises(subprocess.TimeoutExpired):
            await _execute([sys.executable, "-c", "import time; time.sleep(20)"], cwd=Path.cwd(),
                           env=dict(os.environ), timeout=1, runner=None)

    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(check())


@pytest.mark.skipif(os.name != "nt", reason="Windows selector-loop compatibility")
def test_selector_worker_cancels_running_subprocess():
    async def check():
        started = asyncio.Event()
        async def emit(_text):
            started.set()
        task = asyncio.create_task(_execute([sys.executable, "-u", "-c", "import time; print('ready'); time.sleep(20)"],
            cwd=Path.cwd(), env=dict(os.environ), timeout=30, runner=None, on_output=emit))
        await asyncio.wait_for(started.wait(), 10)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 5)
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(check())
