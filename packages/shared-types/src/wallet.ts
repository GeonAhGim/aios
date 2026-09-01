// src/services/wallet_service.py, src/api/schemas/wallet.py 1:1 대응.
// ADR-2026-08-29 §1 — 마켓플레이스 거래 통화는 플랫폼 내부 크레딧(1 크레딧
// = 1원 고정)이다. 화면 표시 단위는 "크레딧".

export interface WalletBalance {
  userId: string;
  balance: string;
}

export interface TopupRequestBody {
  amount: string;
}

export interface WalletTopupRequest {
  id: number;
  userId: string;
  requestedAmount: string;
  status: string;
  requestedAt: string;
  confirmedAt: string | null;
  confirmedBy: string | null;
}

export interface WalletTopupPage {
  items: WalletTopupRequest[];
  total: number;
  page: number;
  pageSize: number;
}

export interface WalletTopupConfirmResult {
  id: number;
  status: string;
  balanceAfter: string | null;
  confirmedAt: string | null;
}
