export interface Command {
  readonly id: string;
  readonly label: string;
  readonly to: string;
  /** 라벨 외에 검색이 걸려야 하는 별칭(경로 등) -- 화면에는 노출하지 않는다. */
  readonly keywords?: string;
}

function normalize(value: string | undefined): string {
  // UX-19: 서버/상위 목록이 손상된 항목(label undefined 등)을 섞어 보내도
  // digest.ts(UX-18)의 UNKNOWN_DATE_KEY 격리와 동일하게 그 항목만 매칭 대상에서
  // 빠지고(빈 문자열이라 대부분 쿼리에 안 걸림) throw로 전체 팔레트를 죽이지 않는다.
  return (value ?? "").toLowerCase();
}

/**
 * 명령 팔레트 검색: query가 비어 있으면 입력 순서 그대로 전체를 반환하고,
 * 아니면 label/keywords/to를 합친 문자열에서 부분 문자열이 나타나는 위치(score,
 * 앞쪽일수록 작음)로 정렬한다. O(n log n) -- 정렬 1회로 끝낸다.
 */
export function matchCommands(commands: readonly Command[], query: string): Command[] {
  const needle = query.trim().toLowerCase();
  if (needle === "") return [...commands];

  const ranked: { command: Command; score: number }[] = [];
  for (const command of commands) {
    const haystack = `${normalize(command.label)} ${normalize(command.keywords)} ${normalize(command.to)}`;
    const score = haystack.indexOf(needle);
    if (score === -1) continue;
    ranked.push({ command, score });
  }
  ranked.sort((a, b) => a.score - b.score);
  return ranked.map((entry) => entry.command);
}
