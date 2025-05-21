import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { Highlight } from './Highlight';

const TEXT = 'Step 4.2: Preheat the joint to at least 120 degC before striking the arc.';

describe('Highlight', () => {
  it('marks exactly the stored character span, not a re-search of the text', () => {
    // "Preheat" starts at index 10.
    render(<Highlight text={TEXT} spans={[{ start: 10, end: 17, id: 'c1' }]} />);
    expect(screen.getByTestId('highlight-c1')).toHaveTextContent('Preheat');
  });

  it('renders the untouched text around the highlight', () => {
    const { container } = render(<Highlight text={TEXT} spans={[{ start: 10, end: 17, id: 'c1' }]} />);
    expect(container.textContent).toBe(TEXT);
  });

  it('renders plain text when there are no spans', () => {
    const { container } = render(<Highlight text={TEXT} spans={[]} />);
    expect(container.textContent).toBe(TEXT);
    expect(container.querySelector('[data-testid^="highlight-"]')).toBeNull();
  });

  it('drops overlapping spans so the output stays a flat sequence', () => {
    const { container } = render(
      <Highlight
        text={TEXT}
        spans={[
          { start: 10, end: 17, id: 'a' },
          { start: 12, end: 20, id: 'b' },
        ]}
      />,
    );
    expect(screen.getByTestId('highlight-a')).toBeInTheDocument();
    expect(screen.queryByTestId('highlight-b')).toBeNull();
    expect(container.textContent).toBe(TEXT);
  });

  it('clamps a span that runs past the end of the text instead of throwing', () => {
    const { container } = render(
      <Highlight text={TEXT} spans={[{ start: 60, end: 9999, id: 'over' }]} />,
    );
    expect(container.textContent).toBe(TEXT);
    expect(screen.getByTestId('highlight-over')).toHaveTextContent('the arc.');
  });

  it('ignores malformed spans rather than rendering a broken document', () => {
    const { container } = render(
      <Highlight
        text={TEXT}
        spans={[
          { start: -5, end: 3, id: 'negative' },
          { start: 20, end: 20, id: 'empty' },
          { start: 9000, end: 9100, id: 'past-end' },
        ]}
      />,
    );
    expect(container.textContent).toBe(TEXT);
    expect(container.querySelectorAll('[data-testid^="highlight-"]')).toHaveLength(0);
  });

  it('visually distinguishes the active span for evidence navigation', () => {
    render(
      <Highlight
        text={TEXT}
        spans={[
          { start: 0, end: 4, id: 'one' },
          { start: 10, end: 17, id: 'two' },
        ]}
        activeId="two"
      />,
    );
    expect(screen.getByTestId('highlight-two').className).toMatch(/ring-2/);
    expect(screen.getByTestId('highlight-one').className).not.toMatch(/ring-2/);
  });

  it('applies a different tone per source type', () => {
    render(
      <Highlight
        text={TEXT}
        spans={[
          { start: 0, end: 4, id: 'req', tone: 'requirement' },
          { start: 10, end: 17, id: 'ev', tone: 'evidence' },
        ]}
      />,
    );
    expect(screen.getByTestId('highlight-req').className).not.toBe(
      screen.getByTestId('highlight-ev').className,
    );
  });
});
