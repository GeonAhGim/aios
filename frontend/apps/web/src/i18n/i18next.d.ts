// UX-1 (task-2685): t("common.retry") 같은 키를 컴파일 타임에 catalogKo 구조로
// 검증하기 위한 react-i18next 표준 모듈 확장 패턴.
import "i18next";
import type { CatalogKo } from "./catalog.ko";

declare module "i18next" {
  interface CustomTypeOptions {
    defaultNS: "translation";
    resources: {
      translation: CatalogKo;
    };
  }
}
