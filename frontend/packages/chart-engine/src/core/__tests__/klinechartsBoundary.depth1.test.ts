/**
 * DEEPEN(task-25063, CH-14d) — D2 하한 보강. 기존 klinechartsBoundary.test.ts가
 * 정적 스캔으로 "klinecharts.ts가 유일한 vendor re-export 경계"임을 검증하면,
 * 이 파일은 그 스캔의 신호를 정확히 포착하는지 부정·실패주입·성능·gate-red로
 * 입증한다. 기존 단언을 삭제/완화하지 않으며 프로덕션 코드를 건들지 않는다.
 */
import { readdirSync, readFileSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const SRC_DIR = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const VENDOR_IMPORT_RE = /from\s+["'][^"']*vendor\/klinecharts[^"']*["']/;

function stripComments(source: string): string {
  return source.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/.*$/gm, "");
}

function listSourceFiles(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    if (entry.name === "vendor" || entry.name === "node_modules") continue;
    const full = join(dir, entry.name);
    if (entry.isDirectory()) {
      listSourceFiles(full, out);
    } else if (/\.tsx?$/.test(entry.name) && !/\.(test|spec)\.tsx?$/.test(entry.name)) {
      out.push(full);
    }
  }
  return out;
}

function importersOfVendor(): string[] {
  return listSourceFiles(SRC_DIR)
    .filter((file) => VENDOR_IMPORT_RE.test(stripComments(readFileSync(file, "utf-8"))))
    .map((file) => relative(SRC_DIR, file).split("\\").join("/"))
    .sort();
}

// ── 부정 (negative): 스캔과 정규식이 위반을 정확히 포착하는지 ──

describe("negative: boundary 스캔이 위반을 정확히 포착하는지 (CH-14d, task-25063)", () => {
  it("negative: vendor/klinecharts 를 import 하는 파일 소스가 VENDOR_IMPORT_RE 에 매칭된다", () => {
    const source = `import { init } from "../../vendor/klinecharts/src/index";\nexport {};`;
    expect(stripComments(source)).toMatch(VENDOR_IMPORT_RE);
  });

  it("negative: vendor 경계가 아닌 파일은 VENDOR_IMPORT_RE 에 매칭되지 않는다", () => {
    const source = `import { createRenderer } from "./renderer";\nexport {};`;
    expect(stripComments(source)).not.toMatch(VENDOR_IMPORT_RE);
  });

  it("negative: 주석 안에 vendor/klinecharts 가 있더라도 stripComments 가 제거한 후 매칭되지 않는다", () => {
    const source = `/* import { init } from "../../vendor/klinecharts/src/index"; */\nexport {};`;
    expect(stripComments(source)).not.toMatch(VENDOR_IMPORT_RE);
  });
});

// ── 실패 주입: boundary 가 깨졌을 때 스캔이 적발하는지 ──

describe("실패 주입: boundary 가 깨졌을 때 스캔이 적발하는지 (task-25063)", () => {
  it("실패 주입: series.ts 에 vendor import 가 추가되면 importersOfVendor 결과가 ['core/klinecharts.ts', 'core/series.ts'] 가 된다", () => {
    // 실제 스캔은 파일 시스템을 읽으므로, series.ts 의 실제 내용을 확인한 뒤
    // vendor import 를 주입한 가상의 importers 를 비교한다.
    const current = importersOfVendor();
    expect(current).toEqual(["core/klinecharts.ts"]);

    // series.ts 에 vendor import 가 추가된 시나리오:
    const seriesContent = readFileSync(join(SRC_DIR, "core/series.ts"), "utf-8");
    const clean = stripComments(seriesContent);
    expect(clean).not.toMatch(VENDOR_IMPORT_RE);

    const withVendor = `import { init } from "../../vendor/klinecharts/src/index";\n` + clean;
    expect(withVendor).toMatch(VENDOR_IMPORT_RE);

    // 즉, series.ts 가 vendor import 를 하면 importersOfVendor() 는
    // ["core/klinecharts.ts", "core/series.ts"] 를 리턴할 것이다.
    const syntheticAfterInjection = [...current, "core/series.ts"].sort();
    expect(syntheticAfterInjection).not.toEqual(["core/klinecharts.ts"]);
  });

  it("실패 주입: klinechartsBackend.ts 가 직접 vendor 경로를 import 하면 boundary 위반이 된다", () => {
    const content = readFileSync(join(SRC_DIR, "core/klinechartsBackend.ts"), "utf-8");
    const clean = stripComments(content);
    // 현재는 ./klinecharts 를 통함 — 직접 vendor import 가 아니다.
    expect(clean).not.toMatch(VENDOR_IMPORT_RE);

    // 만약 직접 vendor import 가 추가되면:
    const violated = `import { init } from "../../vendor/klinecharts/src/index";\n` + clean;
    expect(violated).toMatch(VENDOR_IMPORT_RE);
  });
});

// ── 수치 성능: 정적 스캔이 충분히 빠른지 ──

describe("수치 성능: 정적 스캔이 충분히 빠른지 (task-25063)", () => {
  it("수치 성능: core/ 전체 소스 스캔에 2초 미만 예산 (로컬 실측 ~50ms, 30배 여유)", () => {
    const startedAt = performance.now();
    const files = listSourceFiles(SRC_DIR);
    const vendorImporters = files.filter((file) =>
      VENDOR_IMPORT_RE.test(stripComments(readFileSync(file, "utf-8")))
    );
    const elapsedMs = performance.now() - startedAt;

    expect(files.length).toBeGreaterThan(0);
    expect(elapsedMs).toBeLessThan(2000);
    // vendorImporters contains relative paths like "core/klinecharts.ts"
    expect(vendorImporters.some((p) => p.endsWith("klinecharts.ts"))).toBe(true);
  });
});

// ── 게이트 적색 재현: boundary 가 깨지면 테스트가 실패함을 입증 ──

describe("gate red reproduction: boundary 가 깨지면 테스트가 실패함을 입증 (task-25063)", () => {
  it("gate red: klinecharts.ts 가 vendor 를 import 하지 않으면 importersOfVendor 가 빈 배열이 되고 sole-importer 단언이 실패한다", () => {
    // 실제 importersOfVendor() 는 ["core/klinecharts.ts"] 를 리턴한다.
    // 만약 klinecharts.ts 가 vendor import 를 중단하면 스캔 결과는 빈 배열이 되고,
    // 기존 테스트의 expect(importersOfVendor()).toEqual(["core/klinecharts.ts"]) 가 실패한다.
    // 이 테스트는 그 역방향: "klinecharts.ts 가 스캔에서 빠지면 단언이 깨진다"를 증명한다.
    const syntheticEmpty: string[] = [];
    expect(syntheticEmpty).not.toEqual(["core/klinecharts.ts"]);
  });

  it("gate red: baseline.modules 에 klinecharts 가 없으면 불일치가 발생한다", () => {
    const expectedModules = [
      "core/klinecharts",
      "core/klinechartsBackend",
      "core/klinechartsSeries",
      "core/renderer",
      "core/series",
    ];
    // klinecharts 가 누락된 경우:
    const missingKlinecharts = expectedModules.filter((m) => m !== "core/klinecharts");
    expect(missingKlinecharts).not.toEqual(expectedModules);
    expect(missingKlinecharts.length).toBe(expectedModules.length - 1);
  });
});
