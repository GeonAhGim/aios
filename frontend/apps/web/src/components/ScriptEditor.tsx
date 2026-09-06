import { useRef } from "react";

export interface ScriptEditorMarker {
  line: number;
  col: number;
  message: string;
}

export interface ScriptEditorProps {
  value: string;
  onChange: (value: string) => void;
  marker?: ScriptEditorMarker | null;
  disabled?: boolean;
  rows?: number;
}

const LINE_HEIGHT_EM = 1.5;

// DSL-13a: 편집기를 "무엇을 렌더하는가"(값·변경 콜백·오류 마커)와 "어떻게
// 렌더하는가"(textarea + 줄번호 거터)로 나눈다. 13b(Monaco 반입, CA 승인
// 후)는 이 컴포넌트의 내부만 갈아끼우면 되고 ScriptEditorPage.tsx를 포함한
// 호출부는 ScriptEditorProps 계약을 그대로 쓴다. DSL-12 compile 응답의
// 오류는 details.code/line/col 하나뿐이라(4종 중 1개, 배열이 아니다) marker도
// 단일값이다.
export function ScriptEditor({ value, onChange, marker = null, disabled = false, rows = 20 }: ScriptEditorProps) {
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
            const isErrorLine = marker?.line === lineNumber;
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
      {marker && (
        <p data-testid="script-editor-marker" className="text-xs text-danger">
          {marker.line}행 {marker.col}열: {marker.message}
        </p>
      )}
    </div>
  );
}
