// task-7500(J3 G-4): catalog.en.ts(490줄, P6 500줄 경고 임계 근접)에 직접 추가하면
// 곧 임계를 넘는다 — catalog.ko.extra.ts와 같은 분리 관용을 english 쪽에도 적용해
// 새 네임스페이스만 이 파일에 둔다.
export const catalogEnExtra = {
  riskVerdictPanel: {
    title: "Risk / compliance verdict",
    noVerdict: "No verdict yet.",
    pending: "Checking the verdict...",
    outcomeLabel: "Outcome",
    reasonCodesLabel: "Reason codes",
    reasonCodesEmpty: "No reason codes provided.",
    ruleBasisLabel: "Rule basis",
    evaluatedAtLabel: "Evaluated at",
    failClosed: "Could not confirm the verdict. Treating this as not allowed (fail-closed).",
  },
} as const;
