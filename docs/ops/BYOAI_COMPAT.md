# BYOAI 에이전트 호환성 (자동 생성 -- 수정하지 말 것)

원천: `docs/ops/byoai_compat.json`.
생성: `python scripts/byoai_compat.py --render`. 규칙: ADR-2026-11-20-B D7·D9.
등급은 자동 검사가 **내리기만** 한다.
Verified는 실제 클라이언트로 확인한 증거(날짜·근거)가 있을 때만 사람이 올린다.
운영 상태(health)는 관측이라 자동으로 오르내린다(ADR-2026-11-20-B D10).
확인 주기: 90일. 기한이 지나면 대시보드·소견이 '재확인 필요'로 표시한다.

| 에이전트 | 채널 | 등급 | 마지막 확인 | 다음 확인 | 운영 상태 | 출처 |
|---|---|---|---|---|---|---|
| Claude (Claude Code·Desktop·웹 커넥터) | T1 원격 MCP + 토큰 | 미확인 | - | - | - | - |
| ChatGPT (개발자 모드 사용자 지정 MCP 커넥터) | T1 원격 MCP + 토큰/OAuth | 미확인 | - | - | - | [링크](https://help.openai.com/en/articles/12584461-developer-mode-and-full-mcp-connectors-in-chatgpt), [링크](https://developers.openai.com/api/docs/guides/developer-mode.md) |
| Gemini CLI | T1 원격 MCP + 토큰/OAuth | 미확인 | - | - | - | [링크](https://geminicli.com/docs/tools/mcp-server/) |
| Gemini API (함수 호출) | T3 도구 스키마 내보내기 | 미확인 | - | - | - | - |
| GitHub Copilot (VS Code·JetBrains·CLI) | T1 원격 MCP + 토큰(PAT)/OAuth | 미확인 | - | - | - | [링크](https://docs.github.com/en/copilot/concepts/context/mcp) |
| Microsoft Copilot Studio | T1 원격 MCP + 인증 입력 | 미확인 | - | - | - | [링크](https://learn.microsoft.com/en-us/microsoft-copilot-studio/agent-extend-action-mcp) |
| 로컬 LLM (Ollama + MCP 클라이언트) | T1 원격 MCP(클라이언트 경유) 또는 T3 도구 스키마 | 미확인 | - | - | - | [링크](https://pypi.org/project/mcp-client-for-ollama/0.24.1/) |
| 붙여 넣기(에이전트 없이) | T4 화면에 제안 붙여 넣기·업로드 | 미확인 | - | - | - | - |
