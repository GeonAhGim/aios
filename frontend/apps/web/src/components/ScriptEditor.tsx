import { useRef } from "react";

export interface ScriptEditorMarker {
  line: number;
  col: number;
  message: string;
}

export interface ScriptEditorProps {
  value: string;
  onChange: (value: string) => void;
  markers?: ScriptEditorMarker[];
  disabled?: boolean;
  rows?: number;
}

const LINE_HEIGHT_EM = 1.5;

// DSL-13a/b: 편집기를 "무엇을 렌더하는가"(값·변경 콜백·오류 마커 목록)와
// "어떻게 렌더하는가"(textarea + 줄번호 거터)로 나눈다. 13c(Monaco 반입, CA
// 승인 후)는 이 컴포넌트의 내부만 갈아끼우면 되고 ScriptEditorPage.tsx를
// 포함한 호출부는 ScriptEditorProps 계약을 그대로 쓴다.
// DSL-12 compile 라우터(src/api/routers/scripts.py)는 첫 오류에서 멈추는
// 컴파일러라 실제로는 항상 0~1개만 채워지지만(details.code/line/col 하나
// 뿐, 배열이 아니다), 컴포넌트 자체는 임의 개수의 마커를 그릴 수 있게 배열로
// 일반화한다 — 호출부가 여러 진단을 합쳐 보여줘야 하는 경우가 와도 이 파일을
// 다시 고칠 필요가 없다.
export function ScriptEditor({ value, onChange, markers = [], disabled = false, rows = 20 }: ScriptEditorProps) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const gutterRef = useRef<HTMLDivElement>(null);

  const lines = value.length === 0 ? [""] : value.split("\n");
  const heightStyle = { height: `${rows * LINE_HEIGHT_EM}em` };

  function syncGutterScroll() {
    if (gutterRef.current && textareaRef.current) {
      gutterRef.current.scrollTop = textareaRef.current.scrollTop;
    }
  }

  return (
    <div className="space-y-2">
      <div className="flex overflow-hidden rounded-md border border-border bg-surface">
        <div
          ref={gutterRef}
          data-testid="script-editor-gutter"
          aria-hidden="true"
          className="select-none overflow-hidden border-r border-border px-2 py-2 text-right font-mono text-xs text-fg-muted"
          style={heightStyle}
        >
          {lines.map((_, index) => {
            const lineNumber = index + 1;
            const isErrorLine = markers.some((m) => m.line === lineNumber);
            return (
              <div
                key={lineNumber}
                style={{ lineHeight: `${LINE_HEIGHT_EM}em` }}
                className={isErrorLine ? "font-semibold text-danger" : undefined}
              >
                {lineNumber}
              </div>
            );
          })}
        </div>
        <textarea
          ref={textareaRef}
          data-testid="script-editor-textarea"
          aria-label="스크립트 소스"
          value={value}
          disabled={disabled}
          spellCheck={false}
          onChange={(e) => onChange(e.target.value)}
          onScroll={syncGutterScroll}
          style={{ ...heightStyle, lineHeight: `${LINE_HEIGHT_EM}em` }}
          className="w-full resize-none border-0 bg-transparent px-3 py-2 font-mono text-xs text-fg outline-none disabled:opacity-50"
        />
      </div>
      {markers.length > 0 && (
        <div data-testid="script-editor-markers" className="space-y-1">
          {markers.map((m, index) => (
            <p key={`${m.line}:${m.col}:${index}`} data-testid={`script-editor-marker-${index}`} className="text-xs text-danger">
              {m.line}행 {m.col}열: {m.message}
            </p>
          ))}
        </div>
      )}
    </div>
  );
}
