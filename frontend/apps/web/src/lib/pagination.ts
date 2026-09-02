// ApiResponse 봉투의 pagination meta(L4_platform_observability_tenancy_api_v1.0.md
// §3.3 PageMeta, §9 PLT-12)를 소비해 화면에 필요한 페이지 상태로 변환하는 순수 함수.
// 서버는 page=0 등을 400으로 막지만(§9 test_pagination.py), 클라이언트도 방어적으로
// 어떤 입력에서도 예외 없이 안전한 값으로 수렴해야 한다.

import type { ApiResponsePageMeta } from "@aios/api-client";

export interface PageState {
  /** 1 이상으로 보정된 현재 페이지 번호. */
  page: number;
  /** 1 이상으로 보정된 페이지 크기. */
  size: number;
  /** 서버가 카운트를 제공하지 않는 커서 방식 목록이면 null. 음수는 0으로 보정. */
  total: number | null;
  /** total이 없으면 null. total=0이면 0. */
  totalPages: number | null;
  hasPrev: boolean;
  hasNext: boolean;
  /** 화면에 표시할 1-based 항목 범위. 표시할 항목이 없으면 둘 다 0. */
  rangeStart: number;
  rangeEnd: number;
}

const DEFAULT_SIZE = 20;

export interface DerivePageStateOptions {
  /** meta 자체가 없을 때(봉투에 pagination이 없는 응답) 쓸 페이지 크기. */
  defaultSize?: number;
}

function normalizeSize(size: number | undefined, fallback: number): number {
  if (typeof size !== "number" || !Number.isFinite(size) || size < 1) {
    return fallback;
  }
  return Math.floor(size);
}

function normalizePage(page: number | null | undefined): number {
  if (typeof page !== "number" || !Number.isFinite(page) || page < 1) {
    return 1;
  }
  return Math.floor(page);
}

/**
 * 서버 PageMeta(page/size/total/next_cursor)를 받아 총 페이지수·다음/이전 존재·
 * 현재 표시 범위를 계산한다. meta가 없거나 필드가 비정상이어도 예외를 던지지 않고
 * 안전한 기본값으로 수렴한다.
 */
export function derivePageState(
  meta: ApiResponsePageMeta | null | undefined,
  options: DerivePageStateOptions = {},
): PageState {
  const fallbackSize = normalizeSize(options.defaultSize, DEFAULT_SIZE);

  if (!meta) {
    return {
      page: 1,
      size: fallbackSize,
      total: null,
      totalPages: null,
      hasPrev: false,
      hasNext: false,
      rangeStart: 0,
      rangeEnd: 0,
    };
  }

  const size = normalizeSize(meta.size, fallbackSize);
  const requestedPage = normalizePage(meta.page);

  if (meta.total == null || !Number.isFinite(meta.total)) {
    // 카운트를 모르는 커서 방식 목록: totalPages/range는 알 수 없으므로
    // next_cursor 유무로만 다음 페이지 존재를 판단한다.
    const page = requestedPage;
    return {
      page,
      size,
      total: null,
      totalPages: null,
      hasPrev: page > 1,
      hasNext: meta.next_cursor != null,
      rangeStart: (page - 1) * size + 1,
      rangeEnd: page * size,
    };
  }

  const total = Math.max(0, Math.floor(meta.total));
  const totalPages = total === 0 ? 0 : Math.ceil(total / size);
  const page = totalPages === 0 ? 1 : Math.min(Math.max(requestedPage, 1), totalPages);

  const rangeStart = totalPages === 0 ? 0 : (page - 1) * size + 1;
  const rangeEnd = totalPages === 0 ? 0 : Math.min(page * size, total);

  return {
    page,
    size,
    total,
    totalPages,
    hasPrev: page > 1,
    hasNext: totalPages > 0 && page < totalPages,
    rangeStart,
    rangeEnd,
  };
}
