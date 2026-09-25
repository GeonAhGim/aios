import type { Locator, Page } from "@playwright/test";

// @aios/ui-web Field.tsx는 label에 htmlFor를 넣지 않는다(호출부 대부분이
// 넘기지 않음) — getByLabel은 for/aria-labelledby/wrapping 중 하나가 있어야
// 매칭되므로 여기서는 못 쓴다. 실제 DOM은 label과 input/select가 같은 Field
// wrapper div의 형제 노드이므로, 이름으로 label을 찾고 바로 다음 형제
// 컨트롤을 집는다.
export function fieldControl(page: Page, label: string): Locator {
  return page
    .locator(`label:text-is("${label}")`)
    .locator("xpath=following-sibling::*[self::input or self::select][1]");
}
