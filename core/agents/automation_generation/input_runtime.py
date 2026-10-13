"""Generated checkpoint client. No LLM dependencies in the browser runner."""

RUNTIME_SOURCE = r'''import fs from "fs";
import path from "path";
import { randomUUID } from "crypto";
import { setTimeout as delay } from "timers/promises";
import { test } from "@playwright/test";

type Answer = { mode: "value" | "empty"; value: string; source?: string };
function read(file: string): any {
  if (!fs.existsSync(file)) return undefined;
  return JSON.parse(fs.readFileSync(file, "utf8"));
}
function write(file: string, value: unknown): void {
  const temp = `${file}.${randomUUID()}.tmp`;
  fs.writeFileSync(temp, JSON.stringify(value), { mode: 0o600 });
  fs.renameSync(temp, file);
}

/** Input values are fixed at the consuming checkpoint, never changed mid-action. */
export async function executionInput(key: string, fallback: string | null): Promise<string> {
  const directory = process.env.EXECUTION_INPUT_DIR;
  if (!directory) {
    if (fallback !== null && fallback !== "") return fallback;
    const binding = read(path.resolve(__dirname, "../manifest.json"))?.input_bindings?.find((item: any) => item.key === key);
    if (fallback === "" && binding?.allow_empty) return "";
    throw new Error(`Unresolved input ${key}. Run this suite through the execution input coordinator.`);
  }
  const binding = read(path.join(directory, "bindings.json"))?.[key];
  if (!binding) throw new Error(`Unknown or stale execution input binding: ${key}`);
  const identifier = randomUUID();
  const saved = read(path.join(directory, "snapshot.json"))?.values?.[key] as Answer | undefined;
  let answer = saved;
  if (!answer && fallback !== null && (fallback !== "" || binding.allow_empty)) {
    answer = { mode: fallback === "" ? "empty" : "value", value: fallback, source: "case" };
  }
  if (!answer) {
    const waitMs = 300_000;
    const info = test.info();
    info.setTimeout(info.timeout + waitMs);
    const request = { id: identifier, key, case_code: binding.case_code, field: binding.field,
      category: binding.category, objective: binding.objective, allow_empty: binding.allow_empty,
      sensitive: binding.sensitive, status: "RESOLVING_INPUT", expires_at: (Date.now() + waitMs) / 1000 };
    write(path.join(directory, `${identifier}.request.json`), request);
    console.log(`Resolving input: ${binding.case_code} / ${binding.field}`);
    const deadline = Date.now() + waitMs;
    while (Date.now() < deadline) {
      // Manual answers always take priority over an unconsumed automatic answer.
      answer = read(path.join(directory, `${identifier}.manual.json`)) || read(path.join(directory, `${identifier}.auto.json`));
      if (answer) break;
      await delay(250);
    }
    if (!answer) {
      write(path.join(directory, `${identifier}.request.json`), { ...request, status: "EXPIRED" });
      test.skip(true, `Blocked: input wait expired for ${binding.field}`);
      throw new Error("Input wait expired");
    }
  }
  if (!answer || !["value", "empty"].includes(answer.mode) || typeof answer.value !== "string") {
    throw new Error(`Invalid execution input: ${key}`);
  }
  if (answer.mode === "empty" && !binding.allow_empty) throw new Error(`Empty input not permitted: ${key}`);
  const value = answer.mode === "empty" ? "" : answer.value;
  write(path.join(directory, `${identifier}.consumed.json`), {
    id: identifier, key, case_code: binding.case_code, field: binding.field, sensitive: binding.sensitive,
    mode: answer.mode, value, source: answer.source || "manual", resolved_at: new Date().toISOString(),
  });
  return value;
}
'''
