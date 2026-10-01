// task-10800 unit tests for perceivedPerf.mjs (no browser needed — pure logic + fs
// round trip). Mirrors scripts/check_i18n_literals.test.mjs's node:test pattern.
import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  appendResults,
  buildRecord,
  createJourneyTimer,
  flushJourneyTimer,
  loadResults,
  median,
  mergeRecords,
  summarizeMedians,
} from "./perceivedPerf.mjs";

test("median: odd-length array returns the middle value", () => {
  assert.equal(median([300, 100, 200]), 200);
});

test("median: even-length array returns the average of the two middle values", () => {
  assert.equal(median([100, 200, 300, 400]), 250);
});

test("[negative] median: throws on empty array", () => {
  assert.throws(() => median([]), /non-empty array/);
});

test("[negative] buildRecord: throws when journey/screen/metric missing", () => {
  assert.throws(() => buildRecord("", "dashboard", "displayed", 10), /required/);
  assert.throws(() => buildRecord("J1", "", "displayed", 10), /required/);
  assert.throws(() => buildRecord("J1", "dashboard", "", 10), /required/);
});

test("[negative] buildRecord: throws on negative or non-finite ms", () => {
  assert.throws(() => buildRecord("J1", "dashboard", "displayed", -5), /non-negative finite/);
  assert.throws(() => buildRecord("J1", "dashboard", "displayed", NaN), /non-negative finite/);
});

test("buildRecord: rounds ms and keeps the given measured_at", () => {
  const record = buildRecord("J1", "dashboard", "displayed", 123.6, "2026-10-01T00:00:00.000Z");
  assert.deepEqual(record, {
    journey: "J1",
    screen: "dashboard",
    metric: "displayed",
    ms: 124,
    measured_at: "2026-10-01T00:00:00.000Z",
  });
});

test("summarizeMedians: reports median_ms null below the minimum sample count", () => {
  const records = [
    buildRecord("J1", "dashboard", "displayed", 100),
    buildRecord("J1", "dashboard", "displayed", 200),
  ];
  const [summary] = summarizeMedians(records);
  assert.equal(summary.samples, 2);
  assert.equal(summary.median_ms, null);
});

test("summarizeMedians: computes a median once minSamples is reached", () => {
  const records = [
    buildRecord("J1", "dashboard", "displayed", 100),
    buildRecord("J1", "dashboard", "displayed", 300),
    buildRecord("J1", "dashboard", "displayed", 200),
  ];
  const [summary] = summarizeMedians(records);
  assert.equal(summary.samples, 3);
  assert.equal(summary.median_ms, 200);
});

test("summarizeMedians: keeps journey/screen/metric groups separate", () => {
  const records = [
    buildRecord("J1", "dashboard", "displayed", 100),
    buildRecord("J1", "dashboard", "interactive", 150),
    buildRecord("J2", "screener", "displayed", 400),
  ];
  const groups = summarizeMedians(records, 1).map((s) => `${s.journey}:${s.screen}:${s.metric}`);
  assert.deepEqual(new Set(groups), new Set(["J1:dashboard:displayed", "J1:dashboard:interactive", "J2:screener:displayed"]));
});

test("mergeRecords: concatenates without mutating inputs", () => {
  const a = [buildRecord("J1", "dashboard", "displayed", 100)];
  const b = [buildRecord("J1", "dashboard", "interactive", 150)];
  const merged = mergeRecords(a, b);
  assert.equal(merged.length, 2);
  assert.equal(a.length, 1);
  assert.equal(b.length, 1);
});

test("loadResults: returns [] when the file does not exist", () => {
  const dir = mkdtempSync(join(tmpdir(), "perceived-perf-"));
  try {
    assert.deepEqual(loadResults(join(dir, "missing.json")), []);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("[failure-injection] loadResults: throws on a corrupt (non-JSON) results file", () => {
  const dir = mkdtempSync(join(tmpdir(), "perceived-perf-"));
  const filePath = join(dir, "perceived-perf.json");
  writeFileSync(filePath, "{not valid json");
  try {
    assert.throws(() => loadResults(filePath), SyntaxError);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("appendResults: round-trips and accumulates across repeated calls", () => {
  const dir = mkdtempSync(join(tmpdir(), "perceived-perf-"));
  const filePath = join(dir, "nested", "perceived-perf.json");
  try {
    appendResults(filePath, [buildRecord("J1", "dashboard", "displayed", 100)]);
    const merged = appendResults(filePath, [buildRecord("J1", "dashboard", "displayed", 200)]);
    assert.equal(merged.length, 2);
    assert.deepEqual(loadResults(filePath), merged);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("createJourneyTimer + flushJourneyTimer: marks accumulate and flush to disk", () => {
  const dir = mkdtempSync(join(tmpdir(), "perceived-perf-"));
  const filePath = join(dir, "perceived-perf.json");
  try {
    const timer = createJourneyTimer("J1");
    timer.mark("dashboard", "displayed");
    timer.mark("dashboard", "interactive");
    assert.equal(timer.records.length, 2);
    assert.ok(timer.records.every((r) => r.journey === "J1" && r.ms >= 0));

    const flushed = flushJourneyTimer(timer, filePath);
    assert.equal(flushed.length, 2);
    assert.equal(loadResults(filePath).length, 2);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("flushJourneyTimer: no-op (and no file write) when the timer recorded nothing", () => {
  const dir = mkdtempSync(join(tmpdir(), "perceived-perf-"));
  const filePath = join(dir, "perceived-perf.json");
  try {
    const timer = createJourneyTimer("J1");
    assert.deepEqual(flushJourneyTimer(timer, filePath), []);
    assert.deepEqual(loadResults(filePath), []);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});
