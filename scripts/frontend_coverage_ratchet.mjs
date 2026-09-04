#!/usr/bin/env node
// 커버리지 래칫 — PLT-37 프론트 축 (scripts/coverage_ratchet.py의 Node 대응).
//
// 절대 임계치는 두지 않는다. frontend/coverage-baseline.txt에 적힌 직전
// 측정치보다 허용 오차(기본 0.5%p)를 넘겨 하락하면 실패하고, 상승하면
// baseline을 그 값으로 갱신한다 — 한 번 오른 커버리지는 조용히 떨어질 수 없다.
//
// vitest --coverage(provider: v8, reporter: json-summary)가 생성하는
// coverage/coverage-summary.json의 total.lines.pct만 읽는다 — 테스트를
// 재실행하지 않으므로 로컬·CI 양쪽에서 그대로 재사용된다.
//
// 사용: node scripts/frontend_coverage_ratchet.mjs (저장소 루트에서,
// coverage-summary.json이 이미 생성돼 있어야 함). 종료코드 0=통과, 1=하락/오류.

import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const DEFAULT_SUMMARY = resolve(
  ROOT,
  "frontend/apps/web/coverage/coverage-summary.json",
);
const DEFAULT_BASELINE = resolve(ROOT, "frontend/coverage-baseline.txt");
const DEFAULT_TOLERANCE_PP = 0.5;

class CoverageRatchetError extends Error {}

function parseArgs(argv) {
  const args = {
    summary: DEFAULT_SUMMARY,
    baseline: DEFAULT_BASELINE,
    tolerance: DEFAULT_TOLERANCE_PP,
  };
  for (let i = 0; i < argv.length; i += 1) {
    const flag = argv[i];
    const value = argv[i + 1];
    if (flag === "--coverage-summary") {
      args.summary = resolve(value);
      i += 1;
    } else if (flag === "--baseline") {
      args.baseline = resolve(value);
      i += 1;
    } else if (flag === "--tolerance") {
      args.tolerance = Number.parseFloat(value);
      i += 1;
    }
  }
  return args;
}

function readCurrentCoveragePercent(summaryPath) {
  if (!existsSync(summaryPath)) {
    throw new CoverageRatchetError(`coverage-summary.json 없음: ${summaryPath}`);
  }
  let parsed;
  try {
    parsed = JSON.parse(readFileSync(summaryPath, "utf-8"));
  } catch (exc) {
    throw new CoverageRatchetError(`coverage-summary.json 파싱 실패: ${exc.message}`);
  }
  const pct = parsed?.total?.lines?.pct;
  if (typeof pct !== "number" || Number.isNaN(pct)) {
    throw new CoverageRatchetError("coverage-summary.json: total.lines.pct 없음/숫자 아님");
  }
  return Math.round(pct * 100) / 100;
}

function readBaselinePercent(baselinePath) {
  if (!existsSync(baselinePath)) {
    return null;
  }
  const text = readFileSync(baselinePath, "utf-8").trim();
  if (!text) {
    throw new CoverageRatchetError(`baseline 파일이 비어 있음: ${baselinePath}`);
  }
  const value = Number.parseFloat(text);
  if (Number.isNaN(value)) {
    throw new CoverageRatchetError(`baseline 값이 숫자가 아님: ${text}`);
  }
  return Math.round(value * 100) / 100;
}

function writeBaselinePercent(baselinePath, percent) {
  writeFileSync(baselinePath, `${percent.toFixed(2)}\n`, "utf-8");
}

function main(argv) {
  const args = parseArgs(argv);

  let current;
  let baseline;
  try {
    current = readCurrentCoveragePercent(args.summary);
    baseline = readBaselinePercent(args.baseline);
  } catch (exc) {
    if (exc instanceof CoverageRatchetError) {
      console.log(`FAIL: ${exc.message}`);
      return 1;
    }
    throw exc;
  }

  if (baseline === null) {
    writeBaselinePercent(args.baseline, current);
    console.log(`BASELINE 초기화: ${current.toFixed(2)}% -> ${args.baseline}`);
    return 0;
  }

  const delta = current - baseline;
  if (delta < -args.tolerance) {
    console.log(
      `FAIL: 커버리지 하락 ${baseline.toFixed(2)}% -> ${current.toFixed(2)}% ` +
        `(${delta >= 0 ? "+" : ""}${delta.toFixed(2)}%p, 허용 오차 ${args.tolerance.toFixed(2)}%p 초과)`,
    );
    return 1;
  }

  if (current > baseline) {
    writeBaselinePercent(args.baseline, current);
    console.log(`OK: 커버리지 상승, baseline 갱신 ${baseline.toFixed(2)}% -> ${current.toFixed(2)}%`);
    return 0;
  }

  console.log(
    `OK: 커버리지 ${current.toFixed(2)}% (baseline ${baseline.toFixed(2)}%, 허용 오차 ${args.tolerance.toFixed(2)}%p 이내)`,
  );
  return 0;
}

process.exitCode = main(process.argv.slice(2));
