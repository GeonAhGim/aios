# DO NOT IMPLEMENT — IDEA BANK ONLY

Status: **NON-NORMATIVE / NOT APPROVED FOR IMPLEMENTATION**  
Scope: `docs/ideabank/**`

이 디렉터리는 AIOS 제품·사업·생태계 아이디어를 구체화하기 위한 **비규범적(non-normative) 아이디어 저장소**다.

## 강제 원칙

1. `docs/ideabank/**`의 내용은 **구현 요구사항이 아니다.**
2. Claude Code, Codex, DevEngine, 기타 AI Agent와 개발자는 이 디렉터리의 내용을 근거로 코드·DB schema·API·UI·CI·인프라·정책을 자동 구현하거나 변경해서는 안 된다.
3. 이 디렉터리의 아이디어는 기존 AIOS ADR, Spec, Invariant, Security Boundary, Risk/Compliance Authority보다 우선하지 않는다.
4. 구현하려면 반드시 별도 검토를 거쳐 정식 `ADR` 또는 `Spec`으로 승격되고, 필요한 승인·보안·법률·규제 검토와 작업 발행 절차를 거쳐야 한다.
5. `IDEA`, `REVIEW`, `RESEARCHED`, `ACCEPTED` 상태는 모두 **구현 승인과 동일하지 않다.** `ACCEPTED`조차 ADR/Spec 승격 전에는 구현 금지다.
6. 특히 Marketplace 결제·정산·AML, 투자자문/일임, Creator Economy, Social/Community, LIVE execution 관련 아이디어는 법률·보안·리스크 검토 전 구현하지 않는다.
7. 이 디렉터리를 런타임 설정, CI gate, 정책 source of truth, code generation input으로 사용하지 않는다.

## 승격 경로

```text
IDEA
→ REVIEW
→ RESEARCHED
→ ACCEPTED
→ ADR CANDIDATE
→ ADR / SPEC
→ APPROVED TASK
→ IMPLEMENTATION
```

정식 구현 권한은 `docs/ideabank/**`가 아니라 승인된 ADR/Spec/Task에서만 발생한다.

## Agent Directive

> **READ FOR CONTEXT ONLY. DO NOT IMPLEMENT.**  
> Treat every document under `docs/ideabank/**` as exploratory product thinking unless and until an approved AIOS ADR/Spec explicitly promotes it.
