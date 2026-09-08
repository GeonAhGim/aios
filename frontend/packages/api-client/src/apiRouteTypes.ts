// L4 platform spec §3.3(ApiResponse 봉투는 "/api/v1 경로에만 적용, 레거시 alias는
// 구형 그대로 반환") + §9 PLT-16(mount_v1)/PLT-17~21(라우터별 봉투 이관).
// `ApiRouteDefinition`/`route()`/`defineApiRoutes()` — apiPaths.ts의 route 등록
// 표(`apiRoutes.ts`)가 쓰는 타입·헬퍼. task-2098(P6 300줄 상한)로 apiPaths.ts에서
// 분리했다 — 동작 변경 없음.

export interface ApiRouteDefinition {
  legacyPath: string;
  // 서버가 아직 그 라우터를 /api/v1로 이관하지 않았으면(PLT-17~21 미도달)
  // undefined다 — resolvePath는 이 경우 useV1=true여도 legacy로 폴백한다.
  v1Path?: string;
  // 이 라우트가 "지금"(legacy 경로 기준) ApiResponse 봉투로 응답하는지.
  // task-112(28cf21b)로 auth/users/admin 라우터는 legacy 경로에서도 이미 봉투를
  // 쓰고, 나머지는 PLT-17~21이 각 라우터를 이관할 때 함께 true로 바뀐다.
  // v1Path로 실제 해석된 경우는 스펙상 항상 봉투이므로 resolveEnvelope가
  // 이 값 대신 true를 강제한다.
  envelope: boolean;
  // task-1325: 서버에 이 엔드포인트(라우터) 자체가 아직 없는 유령 경로(§3.3)면
  // false다. 생략하면(옵셔널 — apiPaths.test.ts의 defineApiRoutes fixture 리터럴처럼
  // route() 헬퍼를 거치지 않는 경우가 있어 필수로 두지 않는다) isRouteImplemented가
  // true로 취급한다. route() 헬퍼는 4번째 인자를 생략하면 true를 명시적으로 채운다.
  // apiPaths.openapi.test.ts의 GHOST_PATH_WHITELIST와 반드시 일치해야 하고 §D 테스트가
  // 그 일치를 기계적으로 강제한다 — 라우터가 생기면 이 값과 화이트리스트를 함께 고친다.
  // sessions.ts처럼 실제 네트워크 호출 전에 typed 오류로 단락하려는 호출부가
  // isRouteImplemented로 참조한다.
  implemented?: boolean;
  // task-1333: §3.7 IdempotencyScope · §9 PLT-15 "금전(멱등 필수)" 표식의 단일
  // 출처. true인 라우트는 idempotencyScan.test.ts가 packages/api-client/src/**를
  // 소스 스캔해 postIdempotent/postEnvelopeIdempotent(httpIdempotent.ts) 경유로만
  // 호출되는지 양방향으로 대조한다 — 일반 post/patch/put/request 계열로 호출되면
  // FAIL, 반대로 그 두 메서드로 호출되는 라우트에 이 표식이 없어도 FAIL.
  // 미지정 시 false(금전 아님)다.
  idempotencyRequired?: boolean;
}

// 같은 경로를 여러 HTTP 메서드(GET/POST/PUT/...)가 공유하는 경우(예:
// "/executions" GET 목록 + POST 생성) 메서드별로 별도 항목을 만들지 않는다 —
// legacyPath는 "리소스 경로"의 단일 출처이지 "연산"의 단일 출처가 아니다.
// 이 레포의 실제 라우터는 같은 경로를 공유하는 메서드끼리 봉투 여부가 항상
// 동일하므로(라우터 단위로 이관되기 때문) 이 축약이 안전하다.
// task-1333: idempotencyRequired는 하위호환 추가(5번째) 파라미터다 — 기존
// 호출부(4개 인자까지, task-1325의 implemented 포함)는 한 글자도 바뀌지 않고
// 그대로 컴파일된다. v1Path·implemented를 생략하고 idempotencyRequired만
// 넘기고 싶으면 그 두 자리에 undefined를 명시해 각각의 default 표현식이 그대로
// 적용되게 한다(legacyPath/v1Path 값 자체는 이 리프에서 한 글자도 바꾸지
// 않는다는 decision을 지킨다).
export function route(
  legacyPath: string,
  envelope: boolean,
  v1Path: string | null = `/api/v1${legacyPath}`,
  implemented: boolean = true,
  idempotencyRequired = false,
): ApiRouteDefinition {
  return { legacyPath, envelope, v1Path: v1Path ?? undefined, implemented, idempotencyRequired };
}

export function defineApiRoutes<T extends Record<string, ApiRouteDefinition>>(routes: T): T {
  const seenLegacyPaths = new Set<string>();
  for (const [name, def] of Object.entries(routes)) {
    if (seenLegacyPaths.has(def.legacyPath)) {
      throw new Error(`apiPaths: legacyPath가 중복 등록되었습니다("${def.legacyPath}", route="${name}")`);
    }
    seenLegacyPaths.add(def.legacyPath);
  }
  return routes;
}
