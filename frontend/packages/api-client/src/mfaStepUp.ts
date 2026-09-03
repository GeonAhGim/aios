// L4 platform spec §3.3 error taxonomy: "AUTH_MFA_REQUIRED ... step-up 유도" +
// §3.4 인증 토큰·세션(auth_level PASSWORD|MFA_VERIFIED, MFA_STEP_UP_WINDOW
// 15분). http.ts는 라우터/스토어를 직접 import하지 않으므로(순환 의존 방지 +
// 계층 분리, tokenRefresh.ts/configureUnauthorizedHandler와 동일한 이유) 실제
// step-up UI(다이얼로그 오픈 → 사용자 TOTP 입력 → authClient.verifyMfa 호출)는
// 상위 계층(앱 부트스트랩)이 이 훅으로 주입한다. 성공하면 true(재시도
// 가능), 사용자 취소·AUTH_MFA_INVALID 등 실패하면 false(원래의 403 ApiError를
// 그대로 던질 대상)를 반환해야 한다.
export type MfaStepUpHandler = () => Promise<boolean>;

let stepUpHandler: MfaStepUpHandler | null = null;
let inFlightStepUp: Promise<boolean> | null = null;

export function configureMfaStepUpHandler(handler: MfaStepUpHandler | null): void {
  stepUpHandler = handler;
  inFlightStepUp = null;
}

// tokenRefresh.ts의 refreshAccessToken과 동일한 이유로 single-flight —
// 동시에 여러 요청이 403 AUTH_MFA_REQUIRED를 받아도 step-up 다이얼로그는
// 1번만 뜨고, 대기 중인 요청 전부가 같은 결과(true/false)를 공유한다.
export function requestMfaStepUp(): Promise<boolean> {
  if (!stepUpHandler) return Promise.resolve(false);
  if (!inFlightStepUp) {
    const handler = stepUpHandler;
    inFlightStepUp = handler().finally(() => {
      inFlightStepUp = null;
    });
  }
  return inFlightStepUp;
}
