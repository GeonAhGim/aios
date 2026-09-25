// spec L4_analytics_authoring_backtest_marketplace_v1.0.md §9.1 SIG-6 (source line
// 264) — 프론트 `SignalSourcesPage.tsx`(시크릿 발급·회전·최근 수신 로그)가 쓰는
// 타입. 선행 리프 SIG-1~5(`src/foundation/signals/*`, `src/api/routers/signals.py`,
// PLT-33 시크릿 회전)는 아직 없다(src/foundation 디렉터리에 signals 부재,
// src/api/routers에 signals.py 부재 — grep으로 직접 확인) — follow.ts(task-2699
// UX-15)와 동일한 유령 경로 사유로 프론트만 먼저 배선한다. 라우터가 생기면 실제
// 응답 스키마와 대조해 고친다.

export type SignalSourceStatus = "ACTIVE" | "DISABLED";

export interface SignalSourceResponse {
  id: number;
  name: string;
  webhookUrl: string;
  status: SignalSourceStatus;
  /** 마스킹된 표시용 조각(예: "sk_live_****ab12") — 원문 시크릿은 발급·회전 응답에만 1회 실린다. */
  secretPreview: string;
  secretRotatedAt: string;
  createdAt: string;
}

export interface SignalSourceListResponse {
  items: SignalSourceResponse[];
  total: number;
}

export interface SignalSourceCreateRequest {
  name: string;
}

// 시크릿 원문(plaintextSecret)은 발급 직후 이 응답 한 번만 노출된다 — 서버도
// 원문을 저장하지 않고(해시만 보관) 재조회 API가 없으므로, 이 필드를 놓치면
// 사용자는 회전 전까지 그 시크릿을 다시 볼 수 없다(SIG-5 DoD "시크릿 회전"과
// 동일 불변조건).
export interface SignalSourceSecretIssueResponse {
  source: SignalSourceResponse;
  plaintextSecret: string;
}

export type SignalReceiptStatus = "ACCEPTED" | "REJECTED";

export interface SignalReceiptResponse {
  id: number;
  sourceId: number;
  receivedAt: string;
  symbol: string;
  status: SignalReceiptStatus;
  /** 거부 사유(예: "서명 불일치", "재생 방지 nonce 중복") — ACCEPTED면 null. */
  rejectReason: string | null;
}

export interface SignalReceiptListResponse {
  items: SignalReceiptResponse[];
  total: number;
}
