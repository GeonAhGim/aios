// task-10800 (ADR-2026-10-01-A D1): perceived-performance measurement helper for the
// J1~J3 Playwright journeys. This leaf only collects a baseline (no budget assertions —
// see ADR Decision D1 "설계 방향만 제시" and CLAUDE.md #6/12 CI-load caution on wall-clock
// assertions). Pure functions (median/buildRecord/summarizeMedians/mergeRecords) stay
// side-effect-free so they are unit-testable without a browser; the fs helpers below them
// are the only I/O boundary.
import { existsSync, mkdirSync, readFileSync, renameSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

// frontend/e2e/perf-results/perceived-perf.json. Deliberately NOT under Playwright's
// own outputDir (test-results/) -- Playwright empties that directory at the start of
// every run, which would silently drop prior baseline samples instead of accumulating
// them (observed while building this helper: 3 sequential `playwright test` runs left
// only the last run's 8 records).
export const DEFAULT_RESULTS_PATH = join(
  dirname(fileURLToPath(import.meta.url)),
  "..",
  "perf-results",
  "perceived-perf.json",
);

export function median(values) {
  if (!Array.isArray(values) || values.length === 0) {
    throw new Error("median: values must be a non-empty array");
  }
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 === 0 ? (sorted[mid - 1] + sorted[mid]) / 2 : sorted[mid];
}

export function buildRecord(journey, screen, metric, ms, measuredAt = new Date().toISOString()) {
  if (!journey || !screen || !metric) {
    throw new Error("buildRecord: journey, screen, metric are all required");
  }
  if (!Number.isFinite(ms) || ms < 0) {
    throw new Error(`buildRecord: ms must be a non-negative finite number, got ${ms}`);
  }
  return { journey, screen, metric, ms: Math.round(ms), measured_at: measuredAt };
}

export function mergeRecords(existing, incoming) {
  return [...existing, ...incoming];
}

// §4 실측 열은 3회 이상 반복한 중앙값만 쓴다(spec: "단발 값으로 단언하지 않는다") — fewer
// samples report median_ms: null rather than a misleading single-run number.
export function summarizeMedians(records, minSamples = 3) {
  const groups = new Map();
  for (const record of records) {
    const key = `${record.journey}::${record.screen}::${record.metric}`;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(record.ms);
  }
  const summary = [];
  for (const [key, values] of groups) {
    const [journey, screen, metric] = key.split("::");
    summary.push({
      journey,
      screen,
      metric,
      samples: values.length,
      median_ms: values.length >= minSamples ? median(values) : null,
    });
  }
  return summary;
}

export function loadResults(filePath) {
  if (!existsSync(filePath)) return [];
  const raw = readFileSync(filePath, "utf-8").trim();
  if (!raw) return [];
  return JSON.parse(raw);
}

function writeResultsAtomic(filePath, records) {
  mkdirSync(dirname(filePath), { recursive: true });
  const tmpPath = `${filePath}.${process.pid}.${Date.now()}.tmp`;
  writeFileSync(tmpPath, JSON.stringify(records, null, 2));
  renameSync(tmpPath, filePath);
}

const MAX_APPEND_RETRIES = 5;

// Multiple Playwright workers can flush concurrently; this is optimistic
// read-merge-write (retried, not locked) — acceptable for this baseline-collection
// leaf since a lost record only shrinks the sample count, it does not corrupt one.
export function appendResults(filePath, newRecords) {
  let lastError;
  for (let attempt = 0; attempt < MAX_APPEND_RETRIES; attempt += 1) {
    try {
      const merged = mergeRecords(loadResults(filePath), newRecords);
      writeResultsAtomic(filePath, merged);
      return merged;
    } catch (err) {
      lastError = err;
    }
  }
  throw lastError;
}

export function createJourneyTimer(journey) {
  const startedAt = Date.now();
  const records = [];
  return {
    mark(screen, metric) {
      const record = buildRecord(journey, screen, metric, Date.now() - startedAt);
      records.push(record);
      return record;
    },
    records,
  };
}

export function flushJourneyTimer(timer, filePath = DEFAULT_RESULTS_PATH) {
  if (timer.records.length === 0) return [];
  return appendResults(filePath, timer.records);
}
