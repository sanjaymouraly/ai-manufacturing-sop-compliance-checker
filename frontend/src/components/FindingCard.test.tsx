import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { FindingCard } from './FindingCard';
import type { CitationOut, FindingOut } from '../lib/api';

function citation(overrides: Partial<CitationOut> = {}): CitationOut {
  return {
    id: 1,
    source_type: 'sop',
    document_label: 'SOP-WLD-700 rev 1',
    quote: 'Preheat the joint to at least 120 degC before striking the arc.',
    char_start: 594,
    char_end: 659,
    page: 1,
    section_path: '4.2',
    verified: true,
    verification_note: null,
    revision_id: 13,
    report_id: null,
    ...overrides,
  };
}

function finding(overrides: Partial<FindingOut> = {}): FindingOut {
  return {
    id: 42,
    finding_code: 'FND-00042',
    classification: 'confirmed_deviation',
    category: 'parameter_deviation',
    severity: 'critical',
    title: 'Preheat temperature below the required minimum',
    expected: 'temperature >= 120 degC',
    observed: '96 degC',
    explanation: 'The recorded preheat temperature is below the limit stated in the governing SOP.',
    suggested_action: 'Confirm the thermocouple calibration and review the weld for acceptance.',
    evidence_sufficiency: 'sufficient',
    confidence: 0.94,
    detector: 'deterministic',
    verification_status: 'verified',
    verification_notes: [],
    uncertainty_notes: [],
    requirement_key: 'SOP-WLD-700:1:R002',
    sop_code: 'SOP-WLD-700',
    revision_code: '1',
    review_status: 'open',
    reviewer_comment: null,
    reviewed_at: null,
    citations: [citation()],
    metrics: {},
    audit_id: 7,
    ...overrides,
  };
}

describe('FindingCard', () => {
  it('shows severity, classification and category without expanding', () => {
    render(<FindingCard finding={finding()} />);
    expect(screen.getByText('Critical')).toBeInTheDocument();
    expect(screen.getByText('Confirmed Deviation')).toBeInTheDocument();
    expect(screen.getByText('Parameter Deviation')).toBeInTheDocument();
    expect(screen.getByText('FND-00042')).toBeInTheDocument();
  });

  it('hides the evidence until the reviewer opens the card', async () => {
    render(<FindingCard finding={finding()} />);
    expect(screen.queryByText(/SOP expects/)).toBeNull();
    await userEvent.click(screen.getByRole('button', { expanded: false }));
    expect(screen.getByText('SOP expects')).toBeInTheDocument();
    expect(screen.getByText('temperature >= 120 degC')).toBeInTheDocument();
    expect(screen.getByText('96 degC')).toBeInTheDocument();
  });

  it('shows the verbatim quote with its page, section and character offsets', async () => {
    render(<FindingCard finding={finding()} defaultOpen />);
    expect(screen.getByText(/Preheat the joint to at least 120 degC/)).toBeInTheDocument();
    expect(screen.getByText('§4.2')).toBeInTheDocument();
    expect(screen.getByText('p.1')).toBeInTheDocument();
    expect(screen.getByText('chars 594–659')).toBeInTheDocument();
    expect(screen.getByText('✓ verified against source')).toBeInTheDocument();
  });

  it('flags unverifiable citations prominently, including in the collapsed header', () => {
    const bad = finding({
      citations: [citation({ verified: false, verification_note: 'Quote not found in stored source text.' })],
      classification: 'insufficient_evidence',
    });
    render(<FindingCard finding={bad} defaultOpen />);
    expect(screen.getByText('1 unverifiable quote')).toBeInTheDocument();
    expect(screen.getByText('✕ could not be verified')).toBeInTheDocument();
    expect(screen.getByText('Quote not found in stored source text.')).toBeInTheDocument();
  });

  it('says so explicitly when a finding carries no quote at all', () => {
    render(<FindingCard finding={finding({ citations: [] })} defaultOpen />);
    expect(screen.getByText(/No source quote could be attached/)).toBeInTheDocument();
  });

  it('explains the classification in plain language for a non-technical reviewer', () => {
    render(<FindingCard finding={finding({ classification: 'no_detected_deviation' })} defaultOpen />);
    expect(screen.getByText(/This is not a certification of compliance/)).toBeInTheDocument();
  });

  it('surfaces uncertainty notes rather than burying them', () => {
    render(
      <FindingCard
        finding={finding({ uncertainty_notes: ['Execution date was not stated in the report.'] })}
        defaultOpen
      />,
    );
    expect(screen.getByText('Uncertainty')).toBeInTheDocument();
    expect(screen.getByText(/Execution date was not stated/)).toBeInTheDocument();
  });

  it('marks AI-assisted findings so they are never mistaken for deterministic ones', () => {
    render(<FindingCard finding={finding({ detector: 'llm' })} defaultOpen />);
    expect(screen.getByText('LLM')).toBeInTheDocument();
  });

  it('lets the reviewer jump from a citation to the source document', async () => {
    const onCitationClick = vi.fn();
    render(<FindingCard finding={finding()} onCitationClick={onCitationClick} defaultOpen />);
    await userEvent.click(screen.getByRole('button', { name: 'Show in source' }));
    expect(onCitationClick).toHaveBeenCalledWith(expect.objectContaining({ char_start: 594 }));
  });

  it('does not colour a successful verification note as an error', () => {
    render(
      <FindingCard
        finding={finding({
          citations: [citation({ verified: true, verification_note: 'Quote matches the source span exactly.' })],
        })}
        defaultOpen
      />,
    );
    // Red next to a green tick tells the reviewer two opposite things.
    const note = screen.getByText('Quote matches the source span exactly.');
    expect(note.className).not.toMatch(/red/);
  });

  it('still colours a failed verification note as an error', () => {
    render(
      <FindingCard
        finding={finding({
          citations: [citation({ verified: false, verification_note: 'Quote not found in stored source text.' })],
        })}
        defaultOpen
      />,
    );
    expect(screen.getByText('Quote not found in stored source text.').className).toMatch(/red/);
  });

  it('does not repeat a note that is already shown beside its quote', () => {
    const note = 'Quote matches the source span exactly.';
    render(
      <FindingCard
        finding={finding({
          citations: [citation({ verification_note: note })],
          verification_notes: [note, 'Evidence base included 2 documents.'],
        })}
        defaultOpen
      />,
    );
    expect(screen.getAllByText(new RegExp(note.replace(/\./g, '\\.')))).toHaveLength(1);
    expect(screen.getByText(/Evidence base included 2 documents/)).toBeInTheDocument();
  });

  it('renders a reviewer note when one has been recorded', () => {
    render(
      <FindingCard
        finding={finding({ review_status: 'accepted', reviewer_comment: 'Verified against the batch record.' })}
        defaultOpen
      />,
    );
    expect(screen.getByText('Accepted')).toBeInTheDocument();
    expect(screen.getByText(/Verified against the batch record/)).toBeInTheDocument();
  });
});
