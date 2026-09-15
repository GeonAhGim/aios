// UX-1 (task-2685): i18n 프레임워크 진입점. react-i18next를 선택한 이유는
// (a) TanStack Query/React 19와 같은 hooks 패턴이라 이 코드베이스와 결이 맞고,
// (b) i18next+react-i18next 런타임 경로만 트리셰이킹하면 gzip 기준 10~15KB대로
// 기존 번들(react-query+zustand+tailwind) 대비 예산에 부담이 없으며, (c) 둘 다
// MIT 라이선스다. main.tsx가 앱 마운트 전에 이 모듈을 import해 1회 초기화한다
// (registerServiceWorker와 같은 "루트 1곳" 관용, main.tsx 주석 참고).
// UX-2(task-2686): catalog.en 초벌을 리소스로만 등록한다 -- 언어 전환 UI/설정은
// 이 태스크 범위 밖이라 DEFAULT_LANGUAGE는 그대로 "ko"다.
import i18next, { type i18n as I18nInstance } from "i18next";
import { initReactI18next } from "react-i18next";
import { catalogKo } from "./catalog.ko";
import { catalogEn } from "./catalog.en";

export const DEFAULT_LANGUAGE = "ko";

let initialized = false;

/** Idempotent: a second call returns the already-initialized instance without re-registering resources. */
export function initI18n(): I18nInstance {
  if (initialized) return i18next;
  initialized = true;
  void i18next.use(initReactI18next).init({
    lng: DEFAULT_LANGUAGE,
    fallbackLng: DEFAULT_LANGUAGE,
    resources: {
      ko: { translation: catalogKo },
      en: { translation: catalogEn },
    },
    interpolation: {
      escapeValue: false, // React already escapes rendered output
    },
    returnNull: false,
  });
  return i18next;
}

initI18n();

export default i18next;
