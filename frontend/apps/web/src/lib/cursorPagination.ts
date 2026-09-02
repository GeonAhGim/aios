// L4_platform_observability_tenancy_api_v1.0.md §3.3 PageMeta.next_cursor 기반 목록
// 내비게이션. total이 없는(total=null) 커서 방식 목록은 "전체 페이지 수"를 알 수 없으므로
// 총 페이지 표시 대신 방문한 커서를 스택으로 쌓아 뒤로가기(prev)를 흉내낸다.
// total이 있는 목록의 페이지 상태는 여전히 pagination.ts(derivePageState)가 맡는다 —
// 이 모듈은 순수하게 "다음/이전 커서 이동"만 책임진다.

export interface CursorNavigatorMeta {
  next_cursor: string | null;
}

export interface CursorNavigatorState {
  /** 다음 목록 조회 요청에 그대로 실어 보낼 커서. 첫 페이지면 undefined. */
  cursor: string | undefined;
  hasPrev: boolean;
}

export class CursorNavigationError extends Error {}

export interface CursorNavigator {
  getState(): CursorNavigatorState;
  /**
   * meta.next_cursor를 방문 스택에 쌓고 그 커서로 이동한다.
   * next_cursor가 없으면(마지막 페이지) 상태를 바꾸지 않고 거부한다.
   */
  next(meta: CursorNavigatorMeta): void;
  /**
   * 스택에서 바로 이전 커서로 되돌아간다.
   * 방문 이력이 없으면(첫 페이지) 상태를 바꾸지 않고 거부한다.
   */
  prev(): void;
  /** 첫 페이지 상태로 되돌리고 방문 이력을 모두 지운다. */
  reset(): void;
}

/**
 * 방문한 커서를 스택으로 관리하는 내비게이터를 만든다.
 * 스택[0]은 항상 첫 페이지(cursor=undefined)이고, next()는 현재 위치 뒤의 이력을
 * 잘라낸 뒤 새 커서를 쌓는다 — prev()로 되돌아간 뒤 다시 next()하면 예전 "다음" 이력이
 * 아니라 이번에 받은 next_cursor로 대체된다(브라우저 히스토리와 동일한 동작).
 */
export function createCursorNavigator(): CursorNavigator {
  const history: Array<string | undefined> = [undefined];
  let index = 0;

  return {
    getState(): CursorNavigatorState {
      return { cursor: history[index], hasPrev: index > 0 };
    },

    next(meta: CursorNavigatorMeta): void {
      if (meta.next_cursor == null) {
        throw new CursorNavigationError(
          "next_cursor가 없어 다음 페이지로 이동할 수 없습니다.",
        );
      }
      history.length = index + 1;
      history.push(meta.next_cursor);
      index += 1;
    },

    prev(): void {
      if (index === 0) {
        throw new CursorNavigationError("이전 페이지 방문 이력이 없습니다.");
      }
      index -= 1;
    },

    reset(): void {
      history.length = 1;
      index = 0;
    },
  };
}
