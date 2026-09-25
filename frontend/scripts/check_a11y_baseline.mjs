#!/usr/bin/env node
// UX-4 (task-2688) accessibility baseline: 포커스 관리는 @aios/ui-web의
// useDialogFocusTrap(packages/ui-web/src/a11y/focus.ts)로 이미 배선했다(AlertFromChart,
// RiskWarningModal, MfaStepUpDialog). 이 스크립트는 그 옆의 두 가지를 CI 게이트로
// 지킨다:
//   1) 레이블 ratchet -- check_hardcoded_colors.mjs/check_i18n_literals.mjs와 같은
//      정책(ADR-2026-09-09-C): 새 위반은 즉시 실패, 기존 위반은 baseline에 고정해
//      성장만 막는다. 실제 axe-core 규칙 id를 그대로 빌려 쓴다(image-alt/label/
//      dialog-name) -- 다만 검사는 실제 DOM/axe-core가 아니라 소스 텍스트 휴리스틱
//      이다(파서가 아니다, hardcoded-colors와 동일한 트레이드오프).
//   2) 대비 게이트 -- apps/web/src/index.css의 --color-* 토큰(다크는 @theme, 라이트는
//      [data-theme="light"])에서 본문 텍스트로 실제 쓰이는 전경색들이 배경 대비
//      WCAG AA 4.5:1을 만족하는지 계산한다(ratchet 없음 -- 토큰은 닫힌 집합이라
//      즉시 0 위반이 기준).
import { readFileSync, readdirSync } from "node:fs";
import { join, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

const EXCLUDED_SEGMENTS = new Set(["vendor", "node_modules", "dist", "coverage"]);
const DEFAULT_ROOTS = ["apps/web/src", "packages/ui-web/src"];

function toPosix(relPath) {
  return relPath.split(sep).join("/");
}

export function isExcluded(relPosixPath) {
  const segments = relPosixPath.split("/");
  if (segments.some((seg) => EXCLUDED_SEGMENTS.has(seg))) return true;
  const base = segments[segments.length - 1];
  if (/\.test\.tsx?$/.test(base)) return true;
  return !base.endsWith(".tsx");
}

// -- JSX tag scanning (heuristic, not a parser) ------------------------------------

/**
 * `from`부터 다음 "진짜" 태그 종료 `>`를 찾는다 -- `=>`(화살표 함수)나 `>=`
 * 비교 연산자 안의 `>`는 건너뛴다(JSX 속성에 흔한 두 패턴).
 */
export function findTagEnd(source, from) {
  let i = from;
  while (i < source.length) {
    const idx = source.indexOf(">", i);
    if (idx === -1) return -1;
    const prev = source[idx - 1];
    const next = source[idx + 1];
    if (prev !== "=" && next !== "=") return idx;
    i = idx + 1;
  }
  return -1;
}

// `=`를 요구하지 않는다 -- `aria-hidden`처럼 JSX 불리언 단축 표기(`aria-hidden`만
// 쓰고 `={true}` 생략)도 존재를 인정해야 오탐이 없다. 앞쪽에 word/hyphen 문자가
// 오면 매치하지 않는다 -- `data-id="x"`가 `id` 속성으로 오인되는 걸 막는다.
function hasAttr(tagText, names) {
  return names.some((name) => new RegExp(`(?<![\\w-])${name}\\b`, "i").test(tagText));
}

/** 소스 하나에서 image-alt/label/dialog-name 위반을 모두 찾는다. */
export function findA11yIssues(source) {
  const issues = [];
  const tagStart = /<([a-zA-Z][\w.]*)\b/g;
  let match;
  while ((match = tagStart.exec(source))) {
    const tagName = match[1];
    const end = findTagEnd(source, tagStart.lastIndex);
    if (end === -1) {
      tagStart.lastIndex = match.index + 1;
      continue;
    }
    const tagText = source.slice(match.index, end);
    tagStart.lastIndex = end + 1;

    if (tagName === "img") {
      if (!hasAttr(tagText, ["alt"]) && !hasAttr(tagText, ["aria-hidden"])) {
        issues.push({ rule: "image-alt", tagName });
      }
      continue;
    }
    if (tagName === "input" || tagName === "select" || tagName === "textarea") {
      // {...spread} 로 속성을 전달하는 제네릭 래퍼(예: ui-web/Input.tsx)는 정적으로
      // 호출부의 레이블 여부를 알 수 없다 -- 오탐을 피하려고 제외한다.
      if (!hasAttr(tagText, ["aria-label", "aria-labelledby", "id"]) && !tagText.includes("{...")) {
        issues.push({ rule: "label", tagName });
      }
      continue;
    }
    if (/role\s*=\s*["']dialog["']/i.test(tagText)) {
      if (!hasAttr(tagText, ["aria-label", "aria-labelledby"])) {
        issues.push({ rule: "dialog-name", tagName });
      }
    }
  }
  return issues;
}

export function countA11yIssues(source) {
  return findA11yIssues(source).length;
}

function walk(dir, out) {
  let entries;
  try {
    entries = readdirSync(dir, { withFileTypes: true });
  } catch {
    return;
  }
  for (const entry of entries) {
    const full = join(dir, entry.name);
    if (entry.isDirectory()) {
      if (EXCLUDED_SEGMENTS.has(entry.name)) continue;
      walk(full, out);
    } else if (entry.isFile()) {
      out.push(full);
    }
  }
}

export function scanFiles(roots, base) {
  const files = [];
  for (const root of roots) walk(root, files);
  const found = [];
  for (const full of files) {
    const relPosix = toPosix(relative(base, full));
    if (isExcluded(relPosix)) continue;
    const content = readFileSync(full, "utf-8");
    const count = countA11yIssues(content);
    if (count > 0) found.push({ path: relPosix, count });
  }
  found.sort((a, b) => a.path.localeCompare(b.path));
  return found;
}

/** hardcoded-colors/i18n-literals와 동일한 ratchet 정책. */
export function checkRatchet(scanned, baseline) {
  const violations = [];
  const improvable = [];
  const scannedByPath = new Map(scanned.map((s) => [s.path, s.count]));
  for (const { path, count } of scanned) {
    const base = baseline[path];
    if (base === undefined) {
      violations.push({ path, count, reason: "new a11y violation(s), not in the UX-4 baseline" });
      continue;
    }
    if (count > base) {
      violations.push({ path, count, reason: `grew past baseline (${base} -> ${count})` });
    } else if (count < base) {
      improvable.push({ path, count, baseline: base });
    }
  }
  for (const path of Object.keys(baseline)) {
    if (!scannedByPath.has(path)) improvable.push({ path, count: 0, baseline: baseline[path] });
  }
  return { violations, improvable };
}

// -- contrast gate ------------------------------------------------------------------

const HEX_RE = /^#?([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/;

function hexToRgb(hex) {
  const match = HEX_RE.exec(hex);
  if (!match) throw new Error(`invalid hex color: ${hex}`);
  const clean = match[1];
  const full = clean.length === 3 ? clean.split("").map((c) => c + c).join("") : clean;
  return [0, 2, 4].map((i) => parseInt(full.slice(i, i + 2), 16));
}

function toLinear(channel) {
  const c = channel / 255;
  return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
}

function relativeLuminance(hex) {
  const [r, g, b] = hexToRgb(hex).map(toLinear);
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

export function contrastRatio(hexA, hexB) {
  const [lighter, darker] = [relativeLuminance(hexA), relativeLuminance(hexB)].sort((a, b) => b - a);
  return (lighter + 0.05) / (darker + 0.05);
}

export const WCAG_AA_TEXT = 4.5;

/** index.css의 @theme{...}(다크) / [data-theme="light"]{...}(라이트) 블록에서 --color-* 값을 뽑는다. */
export function parseThemeBlocks(cssSource) {
  const blocks = {};
  const blockRe = /@theme\s*\{([^}]*)\}|\[data-theme="light"\]\s*\{([^}]*)\}/g;
  let match;
  while ((match = blockRe.exec(cssSource))) {
    const [, darkBody, lightBody] = match;
    const name = darkBody !== undefined ? "dark" : "light";
    const body = darkBody !== undefined ? darkBody : lightBody;
    const vars = blocks[name] ?? {};
    const varRe = /--color-([\w-]+):\s*(#[0-9a-fA-F]{3,8})\s*;/g;
    let varMatch;
    while ((varMatch = varRe.exec(body))) {
      vars[varMatch[1]] = varMatch[2];
    }
    blocks[name] = vars;
  }
  return blocks;
}

// text-accent/text-danger/text-success/text-warning(등)로 실제 본문 텍스트에 쓰이는
// 전경색 토큰 -- 배경 토큰 각각과 WCAG AA 본문 기준(4.5:1) 이상이어야 한다.
const TEXT_FOREGROUND_TOKENS = ["fg", "fg-secondary", "fg-muted", "accent", "danger", "success", "warning"];
const BACKGROUND_TOKENS = ["bg", "surface"];

export function checkContrast(themeVars) {
  const violations = [];
  for (const fgName of TEXT_FOREGROUND_TOKENS) {
    const fgHex = themeVars[fgName];
    if (!fgHex) continue;
    for (const bgName of BACKGROUND_TOKENS) {
      const bgHex = themeVars[bgName];
      if (!bgHex) continue;
      const ratio = contrastRatio(fgHex, bgHex);
      if (ratio < WCAG_AA_TEXT) {
        violations.push({ fg: fgName, bg: bgName, ratio: Number(ratio.toFixed(2)) });
      }
    }
  }
  return violations;
}

// -- CLI ------------------------------------------------------------------------

function loadBaseline(path) {
  const parsed = JSON.parse(readFileSync(path, "utf-8"));
  return parsed.files ?? {};
}

function parseArgs(argv) {
  const args = { roots: [] };
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === "--root") args.roots.push(argv[++i]);
    else if (arg === "--base") args.base = argv[++i];
    else if (arg === "--baseline") args.baseline = argv[++i];
    else if (arg === "--css") args.css = argv[++i];
    else if (arg === "--dump") args.dump = true;
  }
  return args;
}

export function main(argv) {
  const scriptDir = fileURLToPath(new URL(".", import.meta.url));
  const frontendRoot = resolve(scriptDir, "..");
  const { roots, base, baseline, css, dump } = parseArgs(argv);
  const effectiveBase = base ? resolve(base) : frontendRoot;
  const effectiveRoots =
    roots.length > 0 ? roots.map((r) => resolve(r)) : DEFAULT_ROOTS.map((r) => join(frontendRoot, ...r.split("/")));
  const baselinePath = baseline ? resolve(baseline) : join(frontendRoot, "scripts", "a11y-baseline.json");
  const cssPath = css ? resolve(css) : join(frontendRoot, "apps", "web", "src", "index.css");

  const scanned = scanFiles(effectiveRoots, effectiveBase);

  if (dump) {
    for (const { path, count } of scanned) console.log(`${path}: ${count}`);
    return 0;
  }

  const baselineMap = loadBaseline(baselinePath);
  const { violations, improvable } = checkRatchet(scanned, baselineMap);

  let contrastViolations = [];
  try {
    const cssSource = readFileSync(cssPath, "utf-8");
    const blocks = parseThemeBlocks(cssSource);
    for (const [themeName, vars] of Object.entries(blocks)) {
      for (const v of checkContrast(vars)) contrastViolations.push({ theme: themeName, ...v });
    }
  } catch (err) {
    console.log(`FAIL: unable to read/parse theme CSS at ${cssPath}: ${err.message}`);
    return 1;
  }

  for (const v of violations) {
    console.log(`FAIL: ${v.path}:${v.count} — ${v.reason}`);
  }
  for (const i of improvable) {
    console.log(`INFO: ${i.path}:${i.count} is below baseline ${i.baseline} — baseline can be lowered (commit it by hand)`);
  }
  for (const c of contrastViolations) {
    console.log(`FAIL: contrast[${c.theme}] --color-${c.fg} vs --color-${c.bg} = ${c.ratio}:1 (< ${WCAG_AA_TEXT}:1)`);
  }

  const totalViolations = violations.length + contrastViolations.length;
  if (totalViolations > 0) {
    console.log(`a11y baseline: ${totalViolations} violation(s)`);
    return 1;
  }
  console.log("a11y baseline: OK");
  return 0;
}

const isMain = process.argv[1] && resolve(fileURLToPath(import.meta.url)) === resolve(process.argv[1]);
if (isMain) {
  process.exit(main(process.argv.slice(2)));
}
