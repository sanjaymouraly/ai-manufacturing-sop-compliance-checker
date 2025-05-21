import { describe, expect, it } from 'vitest';
import {
  CLASSIFICATION_HELP,
  DISCLAIMER,
  formatDay,
  formatDuration,
  humanize,
} from './format';

describe('humanize', () => {
  it('turns snake_case enum values into readable labels', () => {
    expect(humanize('confirmed_deviation')).toBe('Confirmed Deviation');
    expect(humanize('blocked_unresolved_authority')).toBe('Blocked Unresolved Authority');
  });

  it('keeps domain acronyms capitalised rather than title-cased', () => {
    expect(humanize('sop_conflict')).toBe('SOP Conflict');
    expect(humanize('outdated_sop')).toBe('Outdated SOP');
    expect(humanize('ocr_used')).toBe('OCR Used');
    expect(humanize('llm')).toBe('LLM');
    expect(humanize('ph')).toBe('pH');
  });

  it('renders empty values as an em dash instead of "Null"', () => {
    expect(humanize(null)).toBe('—');
    expect(humanize(undefined)).toBe('—');
    expect(humanize('')).toBe('—');
  });
});

describe('formatDuration', () => {
  it('uses milliseconds below a second and seconds above', () => {
    expect(formatDuration(13)).toBe('13 ms');
    expect(formatDuration(999)).toBe('999 ms');
    expect(formatDuration(1500)).toBe('1.50 s');
  });

  it('distinguishes a missing duration from zero', () => {
    expect(formatDuration(null)).toBe('—');
    expect(formatDuration(undefined)).toBe('—');
    expect(formatDuration(0)).toBe('0 ms');
  });
});

describe('formatDay', () => {
  it('passes through text that is not a date rather than printing "Invalid Date"', () => {
    expect(formatDay('not-a-date')).toBe('not-a-date');
    expect(formatDay(null)).toBe('—');
  });

  it('formats a real ISO date', () => {
    expect(formatDay('2024-06-01')).toMatch(/2024/);
  });
});

describe('compliance wording guardrails', () => {
  it('never claims to certify compliance', () => {
    const allCopy = [DISCLAIMER, ...Object.values(CLASSIFICATION_HELP)].join(' ').toLowerCase();
    expect(allCopy).not.toMatch(/\bis compliant\b/);
    expect(allCopy).not.toMatch(/\bcertifies\b/);
    expect(allCopy).not.toMatch(/\bapproved for release\b/);
  });

  it('states the decision-support limitation explicitly', () => {
    expect(DISCLAIMER).toMatch(/does not certify compliance/i);
    expect(DISCLAIMER).toMatch(/human reviewer/i);
  });

  it('explains "no detected deviation" without implying a pass', () => {
    expect(CLASSIFICATION_HELP.no_detected_deviation).toMatch(/not a certification/i);
  });

  it('covers every classification the API can return', () => {
    expect(Object.keys(CLASSIFICATION_HELP).sort()).toEqual([
      'confirmed_deviation',
      'insufficient_evidence',
      'no_detected_deviation',
      'possible_deviation',
      'sop_conflict',
    ]);
  });
});
