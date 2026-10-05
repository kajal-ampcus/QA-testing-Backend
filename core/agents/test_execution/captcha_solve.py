"""Read a captcha image the Playwright login already displayed.

Math, numbers, letters, and mixed characters use the same reader as discovery.
The image bytes are the picture on screen. This does not fetch a new challenge.
"""

from __future__ import annotations

import sys
from pathlib import Path

from core.tool_gateway.mcp_clients.chrome_devtools_client import _captcha_answer_from_reading


def solve_image(png: bytes) -> str | None:
    if not png:
        return None
    return _captcha_answer_from_reading("", "", png)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        return 2
    answer = solve_image(Path(args[0]).read_bytes())
    if answer:
        sys.stdout.write(answer)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
