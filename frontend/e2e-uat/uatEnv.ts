import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

// task-25677: 실제 UAT 스택(웹 18080 / API 18000, PAPER 전용)을 대상으로 한 스모크 설정.
// 이 폴더는 기본 e2e(frontend/e2e, mockBackend)와 분리돼 있고, 기본 playwright.config.ts의
// testDir(./e2e)에는 포함되지 않는다 -- 기본 CI가 수집하지 않도록 별도 config(playwright.uat.config.ts)로만 돈다.

const HERE = path.dirname(fileURLToPath(import.meta.url));
// frontend/e2e-uat -> 저장소 루트
const REPO_ROOT = path.resolve(HERE, "..", "..");

export const UAT_WEB_URL = process.env.UAT_WEB_URL ?? "http://127.0.0.1:18080";

export interface UatDemoAccount {
  email: string;
  password: string;
}

// .env.uat 형식(KEY=VALUE, '#' 주석, 선택적 따옴표)만 지원하는 최소 파서.
// 값은 반환 객체로만 전달하고, 이 모듈은 어떤 값도 로그로 남기지 않는다.
export function parseDotEnv(text: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const rawLine of text.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line || line.startsWith("#")) continue;
    const eq = line.indexOf("=");
    if (eq <= 0) continue;
    const key = line.slice(0, eq).trim();
    let value = line.slice(eq + 1).trim();
    if (
      value.length >= 2 &&
      (value.startsWith('"') || value.startsWith("'")) &&
      value.endsWith(value[0])
    ) {
      value = value.slice(1, -1);
    }
    out[key] = value;
  }
  return out;
}

export function loadUatDemoAccount(): UatDemoAccount {
  let email = process.env.UAT_DEMO_EMAIL ?? "";
  let password = process.env.UAT_DEMO_PASSWORD ?? "";
  if (!email || !password) {
    const envPath = path.join(REPO_ROOT, ".env.uat");
    if (existsSync(envPath)) {
      const parsed = parseDotEnv(readFileSync(envPath, "utf-8"));
      email = email || parsed.UAT_DEMO_EMAIL || "";
      password = password || parsed.UAT_DEMO_PASSWORD || "";
    }
  }
  if (!email || !password) {
    throw new Error(
      "UAT 데모 계정이 없습니다: UAT_DEMO_EMAIL/UAT_DEMO_PASSWORD 환경변수 또는 저장소 루트 .env.uat를 설정하세요.",
    );
  }
  return { email, password };
}

// 로그를 남길 때 쓰는 마스킹 값. 비밀번호 원문은 어디에도 출력하지 않는다.
export const MASKED = "********";
