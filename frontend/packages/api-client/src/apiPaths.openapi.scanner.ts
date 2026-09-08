// task-1165(§A/§B)이 만든 순수 스캐너 함수들의 단일 출처. task-2098(P6 300줄 상한)과
// 같은 이유로 apiPaths.openapi.test.ts(vitest describe/it)에서 분리했다 — 동작 변경 없음,
// 이 파일은 테스트 프레임워크에 의존하지 않는 순수 함수만 담는다.

// legacyPath의 ":param" / ":param:literal"(예: ":deploymentId:start") 세그먼트를
// 스냅샷의 "{param}" 세그먼트와 비교 가능한 와일드카드 템플릿으로 바꾼다.
export function legacyPathToTemplate(legacyPath: string): string {
  return legacyPath
    .split("/")
    .map((segment) => {
      if (!segment.startsWith(":")) return segment;
      const parts = segment.slice(1).split(":");
      return parts.length === 1 ? "*" : `*:${parts.slice(1).join(":")}`;
    })
    .join("/");
}

export function snapshotPathToTemplate(snapshotPath: string): string {
  return snapshotPath.replace(/\{[^}]+\}/g, "*");
}

// 정방향(apiPaths → snapshot): 정확일치 우선, 실패 시 템플릿 매칭. 어느 쪽도 안 맞으면
// null(호출부가 화이트리스트 여부를 판단한다 — 이 함수는 화이트리스트를 모른다).
export function resolveInOpenApi(legacyPath: string, paths: readonly string[]): string | null {
  if (paths.includes(legacyPath)) return legacyPath;
  const template = legacyPathToTemplate(legacyPath);
  return paths.find((p) => snapshotPathToTemplate(p) === template) ?? null;
}

// 역방향(snapshot → apiPaths, task-2168 §E): snapshot의 각 경로가 legacyPaths 중 어느
// 하나와도(정확일치든 템플릿이든) 매칭되지 않으면 "미등록"으로 본다. resolveInOpenApi와
// 동일한 매칭 규칙을 반대 방향으로 적용한다 — 매칭 판정 로직 자체를 중복 구현하지 않는다.
export function findUnregisteredSnapshotPaths(
  snapshotPaths: readonly string[],
  legacyPaths: readonly string[],
): string[] {
  const legacyExact = new Set(legacyPaths);
  const legacyTemplates = new Set(legacyPaths.map(legacyPathToTemplate));
  return snapshotPaths.filter((snapshotPath) => {
    if (legacyExact.has(snapshotPath)) return false;
    return !legacyTemplates.has(snapshotPathToTemplate(snapshotPath));
  });
}

// 매칭된 스냅샷 경로의 methods 객체에서 2xx 응답 스키마가 ApiResponse_*를 참조하는지
// 본다(§3.3 봉투 판정: FastAPI가 ApiResponse[T]를 감싼 라우터는 responses.200.content.
// application/json.schema.$ref가 "#/components/schemas/ApiResponse_..."다). 메서드가
// 여럿이면(GET+POST 등) 전부 같은 값이어야 하고, 판단할 데이터가 전혀 없으면 null.
export function computeEnvelopeFromOpenApi(pathItem: Record<string, unknown>): boolean | null {
  const values = new Set<boolean>();
  for (const method of ["get", "post", "put", "patch", "delete"]) {
    const op = pathItem[method] as { responses?: Record<string, unknown> } | undefined;
    if (!op?.responses) continue;
    for (const [code, resp] of Object.entries(op.responses)) {
      if (!code.startsWith("2")) continue;
      const schema = (resp as { content?: { ["application/json"]?: { schema?: Record<string, unknown> } } })
        ?.content?.["application/json"]?.schema;
      if (!schema) continue;
      const ref =
        (schema.$ref as string | undefined) ??
        ((schema.items as Record<string, unknown> | undefined)?.$ref as string | undefined);
      values.add(Boolean(ref?.split("/").pop()?.startsWith("ApiResponse")));
    }
  }
  if (values.size !== 1) return values.size === 0 ? null : null;
  return [...values][0];
}
