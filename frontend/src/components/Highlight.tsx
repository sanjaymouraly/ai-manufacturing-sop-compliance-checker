import { useEffect, useRef } from 'react';

export interface HighlightSpan {
  start: number;
  end: number;
  id: string;
  tone?: 'evidence' | 'requirement' | 'warning';
}

const TONES: Record<string, string> = {
  evidence: 'bg-amber-200 dark:bg-amber-500/40',
  requirement: 'bg-brand-200 dark:bg-brand-500/40',
  warning: 'bg-red-200 dark:bg-red-500/40',
};

/**
 * Renders source text with character-offset spans highlighted. Offsets come straight
 * from stored citations, so what a reviewer sees highlighted is literally the span the
 * backend verified - no re-searching, no approximation.
 */
export function Highlight({
  text,
  spans,
  activeId,
  className = '',
}: {
  text: string;
  spans: HighlightSpan[];
  activeId?: string | null;
  className?: string;
}) {
  const activeRef = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    const node = activeRef.current;
    // scrollIntoView is missing in jsdom and in a few embedded webviews; highlighting
    // must still work there, so treat scrolling as a progressive enhancement.
    if (activeId && typeof node?.scrollIntoView === 'function') {
      node.scrollIntoView({ block: 'center', behavior: 'smooth' });
    }
  }, [activeId]);

  const clean = spans
    .filter((s) => s.start >= 0 && s.end > s.start && s.start < text.length)
    .map((s) => ({ ...s, end: Math.min(s.end, text.length) }))
    .sort((a, b) => a.start - b.start);

  // Drop overlaps so the output stays a simple flat sequence of segments.
  const flat: HighlightSpan[] = [];
  let cursor = 0;
  for (const span of clean) {
    if (span.start < cursor) continue;
    flat.push(span);
    cursor = span.end;
  }

  const nodes: React.ReactNode[] = [];
  let position = 0;
  flat.forEach((span, index) => {
    if (span.start > position) nodes.push(text.slice(position, span.start));
    const isActive = span.id === activeId;
    nodes.push(
      <span
        key={`${span.id}-${index}`}
        id={`span-${span.id}`}
        ref={isActive ? activeRef : undefined}
        data-testid={`highlight-${span.id}`}
        className={`rounded px-0.5 ${TONES[span.tone ?? 'evidence']} ${
          isActive ? 'ring-2 ring-brand-600 ring-offset-1 dark:ring-offset-slate-900' : ''
        }`}
      >
        {text.slice(span.start, span.end)}
      </span>,
    );
    position = span.end;
  });
  if (position < text.length) nodes.push(text.slice(position));

  return (
    <pre
      className={`whitespace-pre-wrap break-words font-mono text-[13px] leading-relaxed text-slate-800 dark:text-slate-200 ${className}`}
    >
      {nodes}
    </pre>
  );
}
