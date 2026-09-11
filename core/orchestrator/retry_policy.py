"""
Two distinct retry concepts, deliberately kept separate (Section 25):

- Infra retries: transient tool failures (network blip, LLM API timeout).
  Exponential backoff, capped at 2-3.
- Content retries: validation-rejection cycles (Test Case Validation,
  Automation Review). Capped at 3. Burning this budget on a flaky network
  call instead of a real rejection would be a bug, not a feature — hence
  the separation.

Phase 0 stub.
"""
