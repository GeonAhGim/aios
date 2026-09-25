// task-2632(U-10): 백엔드 feature flag 서비스가 아직 없어(그렙 결과 0건) 프런트
// 로컬 스토리지 오버라이드 + 하드코딩 기본값만으로 최소 게이트를 둔다. 서버
// 발급 플래그가 생기면 이 모듈의 isFeatureEnabled 시그니처만 유지한 채 내부를
// API 조회로 바꾸면 되고, 호출부(FeatureFlagGate)는 변경할 필요가 없다.
export type FeatureFlagKey = "onboarding_connection_wizard" | "onboarding_demo_mode";

const DEFAULT_FLAGS: Record<FeatureFlagKey, boolean> = {
  onboarding_connection_wizard: true,
  onboarding_demo_mode: true,
};

const STORAGE_PREFIX = "aios_feature_flag:";

function readStorageOverride(key: FeatureFlagKey): boolean | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(`${STORAGE_PREFIX}${key}`);
    if (raw === "true") return true;
    if (raw === "false") return false;
    return null;
  } catch {
    return null;
  }
}

export function isFeatureEnabled(key: FeatureFlagKey): boolean {
  const override = readStorageOverride(key);
  if (override !== null) return override;
  return DEFAULT_FLAGS[key];
}

/** 테스트/QA에서 플래그를 강제 on·off 하기 위한 헬퍼. value=null이면 오버라이드를 지운다. */
export function setFeatureFlagOverride(key: FeatureFlagKey, value: boolean | null): void {
  if (typeof window === "undefined") return;
  if (value === null) {
    window.localStorage.removeItem(`${STORAGE_PREFIX}${key}`);
  } else {
    window.localStorage.setItem(`${STORAGE_PREFIX}${key}`, String(value));
  }
}
