import { useEffect, useState } from 'react';
import { api } from '../lib/api';
import { useAsync } from '../lib/useAsync';
import { Spinner } from '../components/Spinner';
import { ErrorBanner } from '../components/ErrorBanner';
import { Badge } from '../components/Badge';
import { useToast } from '../components/Toast';
import { formatDate, humanize } from '../lib/format';
import { PageShell } from '../components/PageShell';

interface TestResult {
  ok?: boolean;
  message?: string;
  latency_ms?: number;
  models_available?: number;
  [key: string]: unknown;
}

export default function ProviderSettings() {
  const { push } = useToast();
  const { data: catalogue, error: catError, loading: catLoading } = useAsync(() => api.providerCatalogue(), []);
  const { data: configs, loading: cfgLoading, reload } = useAsync(() => api.providers(), []);

  const [name, setName] = useState('primary');
  const [providerType, setProviderType] = useState('offline');
  const [baseUrl, setBaseUrl] = useState('');
  const [model, setModel] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [timeout, setTimeoutSeconds] = useState(60);
  const [maxTokens, setMaxTokens] = useState(1200);
  const [temperature, setTemperature] = useState(0);
  const [allowPaid, setAllowPaid] = useState(false);
  const [budget, setBudget] = useState(2);
  const [tokenBudget, setTokenBudget] = useState(0);
  const [makeActive, setMakeActive] = useState(true);

  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<TestResult | null>(null);
  const [formError, setFormError] = useState<unknown>(null);
  const [models, setModels] = useState<string[]>([]);
  // null until a catalogue has been fetched: "we have not looked yet" is not
  // the same claim as "this provider publishes no prices".
  const [pricingUnknown, setPricingUnknown] = useState<boolean | null>(null);
  const [loadingModels, setLoadingModels] = useState(false);

  const selected = catalogue?.providers.find((p) => p.provider_type === providerType);

  useEffect(() => {
    if (selected && !baseUrl) setBaseUrl(selected.default_base_url || '');
    // Only prefill when the field is still untouched for this provider choice.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [providerType]);

  function loadExisting(configName: string) {
    const config = configs?.find((c) => c.name === configName);
    if (!config) return;
    setName(config.name);
    setProviderType(config.provider_type);
    setBaseUrl(config.base_url ?? '');
    setModel(config.model ?? '');
    setApiKey('');
    setTimeoutSeconds(config.timeout_seconds);
    setMaxTokens(config.max_tokens);
    setTemperature(config.temperature);
    setAllowPaid(config.allow_paid_requests);
    setBudget(config.request_budget_usd);
    setTokenBudget(config.token_budget ?? 0);
    setMakeActive(config.is_active);
    setTestResult(null);
  }

  async function save() {
    setSaving(true);
    setFormError(null);
    try {
      await api.saveProvider({
        name,
        provider_type: providerType,
        base_url: baseUrl || null,
        model: model || null,
        api_key: apiKey || null,
        timeout_seconds: timeout,
        max_tokens: maxTokens,
        temperature,
        allow_paid_requests: allowPaid,
        request_budget_usd: budget,
        token_budget: tokenBudget,
        make_active: makeActive,
      });
      push('success', `Saved provider configuration “${name}”. The API key is encrypted at rest and never returned.`);
      setApiKey('');
      reload();
    } catch (err) {
      setFormError(err);
    } finally {
      setSaving(false);
    }
  }

  async function test(runInference: boolean) {
    setTesting(true);
    setTestResult(null);
    setFormError(null);
    try {
      const result = (await api.testProvider({
        provider_type: providerType,
        base_url: baseUrl || null,
        model: model || null,
        api_key: apiKey || null,
        run_inference: runInference,
      })) as TestResult;
      setTestResult(result);
    } catch (err) {
      setFormError(err);
    } finally {
      setTesting(false);
    }
  }

  async function fetchModels() {
    setLoadingModels(true);
    setFormError(null);
    try {
      const result = await api.providerModels({
        provider_type: providerType,
        base_url: baseUrl || undefined,
        api_key: apiKey || undefined,
      });
      setModels(result.models.map((m) => m.id));
      setPricingUnknown(
        result.models.length > 0 &&
          result.models.every((m) => m.prompt_price_per_1k == null),
      );
      push('success', `Found ${result.models.length} models (source: ${result.source}).`);
    } catch (err) {
      setFormError(err);
    } finally {
      setLoadingModels(false);
    }
  }

  async function remove(id: number, configName: string) {
    try {
      await api.deleteProvider(id);
      push('success', `Deleted configuration “${configName}”.`);
      reload();
    } catch (err) {
      push('error', err instanceof Error ? err.message : 'Delete failed');
    }
  }

  if (catLoading || catError) {
    return (
      <PageShell
        title="AI provider settings"
        description="Choose how AI-assisted analysis runs. Deterministic checks never depend on this."
        loading={catLoading}
        error={catError}
        loadingLabel="Loading provider catalogue"
      >
        {null}
      </PageShell>
    );
  }

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-bold text-slate-900 dark:text-slate-100">AI provider settings</h1>
        <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
          Optional. The deterministic engine runs without any provider — an AI provider only adds advisory
          observations that still require verification.
        </p>
      </div>

      <section className="rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-900 dark:border-amber-700 dark:bg-amber-950/60 dark:text-amber-100">
        <p className="font-semibold">Keys are write-only.</p>
        <p className="mt-1">
          An API key you enter here is encrypted before storage and is never sent back to this screen, written to
          logs, or included in exports. Only the last four characters are shown for identification.
        </p>
      </section>

      {catalogue && catalogue.notes.length > 0 && (
        <ul className="space-y-1 rounded-lg bg-slate-100 px-4 py-3 text-xs text-slate-600 dark:bg-slate-900 dark:text-slate-400">
          {catalogue.notes.map((note) => (
            <li key={note}>• {note}</li>
          ))}
        </ul>
      )}

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_320px]">
        <section className="card p-5">
          <h2 className="text-base font-semibold text-slate-900 dark:text-slate-100">Configuration</h2>

          <div className="mt-4 space-y-4">
            <div className="grid gap-3 sm:grid-cols-2">
              <div>
                <label className="label" htmlFor="p-name">Configuration name</label>
                <input id="p-name" className="input" value={name} onChange={(e) => setName(e.target.value)} />
              </div>
              <div>
                <label className="label" htmlFor="p-type">Provider</label>
                <select
                  id="p-type"
                  className="input"
                  value={providerType}
                  onChange={(e) => {
                    setProviderType(e.target.value);
                    const next = catalogue?.providers.find((p) => p.provider_type === e.target.value);
                    setBaseUrl(next?.default_base_url ?? '');
                    setModels([]);
                    setTestResult(null);
                  }}
                >
                  {catalogue?.providers.map((provider) => (
                    <option key={provider.provider_type} value={provider.provider_type}>
                      {humanize(provider.provider_type)}
                      {provider.is_paid ? ' (paid)' : ' (free/local)'}
                    </option>
                  ))}
                </select>
              </div>
            </div>

            {selected && (
              <p className="rounded-lg bg-slate-50 px-3 py-2 text-xs text-slate-600 dark:bg-slate-800/60 dark:text-slate-400">
                {selected.description}
                {selected.requires_api_key && ' An API key is required.'}
              </p>
            )}

            <div className="grid gap-3 sm:grid-cols-2">
              <div>
                <label className="label" htmlFor="p-url">Base URL</label>
                <input
                  id="p-url"
                  className="input"
                  value={baseUrl}
                  onChange={(e) => setBaseUrl(e.target.value)}
                  placeholder="https://openrouter.ai/api/v1"
                />
              </div>
              <div>
                <label className="label" htmlFor="p-model">Model</label>
                <div className="flex gap-2">
                  <input
                    id="p-model"
                    className="input"
                    value={model}
                    onChange={(e) => setModel(e.target.value)}
                    placeholder="e.g. openai/gpt-4o-mini"
                    list="model-options"
                  />
                  <datalist id="model-options">
                    {models.map((id) => (
                      <option key={id} value={id} />
                    ))}
                  </datalist>
                  <button
                    type="button"
                    className="btn-secondary shrink-0"
                    onClick={fetchModels}
                    disabled={loadingModels}
                  >
                    {loadingModels ? '…' : 'List'}
                  </button>
                </div>
              </div>
            </div>

            <div>
              <label className="label" htmlFor="p-key">
                API key {selected?.requires_api_key ? '(required)' : '(not needed for this provider)'}
              </label>
              <input
                id="p-key"
                type="password"
                className="input"
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                autoComplete="off"
                placeholder={selected?.requires_api_key ? 'sk-…' : 'leave blank'}
              />
              <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
                Leave blank to keep the key already stored for this configuration.
              </p>
            </div>

            <div className="grid gap-3 sm:grid-cols-3">
              <div>
                <label className="label" htmlFor="p-timeout">Timeout (s)</label>
                <input id="p-timeout" type="number" min={5} max={300} className="input" value={timeout} onChange={(e) => setTimeoutSeconds(Number(e.target.value))} />
              </div>
              <div>
                <label className="label" htmlFor="p-tokens">Max tokens</label>
                <input id="p-tokens" type="number" min={64} max={32000} className="input" value={maxTokens} onChange={(e) => setMaxTokens(Number(e.target.value))} />
              </div>
              <div>
                <label className="label" htmlFor="p-temp">Temperature</label>
                <input id="p-temp" type="number" min={0} max={2} step={0.1} className="input" value={temperature} onChange={(e) => setTemperature(Number(e.target.value))} />
              </div>
            </div>

            <div className="rounded-lg border border-slate-200 p-3 dark:border-slate-700">
              <label className="flex items-start gap-2">
                <input
                  type="checkbox"
                  checked={allowPaid}
                  onChange={(e) => setAllowPaid(e.target.checked)}
                  className="mt-0.5 h-4 w-4 rounded border-slate-300 text-brand-600 focus:ring-brand-500"
                />
                <span>
                  <span className="block text-sm font-medium text-slate-900 dark:text-slate-100">
                    Allow chargeable requests
                  </span>
                  <span className="block text-xs text-slate-500 dark:text-slate-400">
                    Off by default. While this is off, every request that would cost money is refused before it
                    is sent.
                  </span>
                </span>
              </label>
              <div className="mt-3 grid gap-3 sm:grid-cols-2">
                <div>
                  <label className="label" htmlFor="p-budget">Hard spend cap for this configuration (USD)</label>
                  <input
                    id="p-budget"
                    type="number"
                    min={0}
                    step={0.5}
                    className="input max-w-[180px]"
                    value={budget}
                    onChange={(e) => setBudget(Number(e.target.value))}
                  />
                </div>
                <div>
                  <label className="label" htmlFor="p-token-budget">Token ceiling (0 = no ceiling)</label>
                  <input
                    id="p-token-budget"
                    type="number"
                    min={0}
                    step={1000}
                    className="input max-w-[180px]"
                    value={tokenBudget}
                    onChange={(e) => setTokenBudget(Number(e.target.value))}
                    aria-describedby="p-token-budget-help"
                  />
                  <p id="p-token-budget-help" className="mt-1 text-xs text-slate-500 dark:text-slate-400">
                    One audit of a typical SOP uses roughly 2,000–4,000 tokens.
                  </p>
                </div>
              </div>

              {/* The USD cap is computed from a published price list. A provider
                  that does not publish prices yields a $0.00 estimate, which no
                  cap can ever exceed, so the spend ceiling silently stops
                  applying. Operators need to be told this rather than discover
                  it on a bill. */}
              {pricingUnknown && (
                <p className="mt-3 rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-900 dark:border-amber-500/40 dark:bg-amber-500/10 dark:text-amber-200">
                  <strong>This provider publishes no pricing.</strong> The USD cap above cannot be
                  enforced, because the estimated cost of every request is $0.00. Set a token
                  ceiling instead, and check your provider&rsquo;s own billing for actual spend.
                </p>
              )}
            </div>

            <label className="flex items-center gap-2 text-sm text-slate-700 dark:text-slate-300">
              <input
                type="checkbox"
                checked={makeActive}
                onChange={(e) => setMakeActive(e.target.checked)}
                className="h-4 w-4 rounded border-slate-300 text-brand-600 focus:ring-brand-500"
              />
              Make this the active configuration
            </label>

            {Boolean(formError) && <ErrorBanner error={formError} />}

            {testResult && (
              <div
                className={`rounded-lg border px-4 py-3 text-sm ${
                  testResult.ok
                    ? 'border-emerald-300 bg-emerald-50 text-emerald-900 dark:border-emerald-800 dark:bg-emerald-950 dark:text-emerald-100'
                    : 'border-red-300 bg-red-50 text-red-900 dark:border-red-800 dark:bg-red-950 dark:text-red-100'
                }`}
                role="status"
              >
                <p className="font-semibold">{testResult.ok ? 'Connection succeeded' : 'Connection failed'}</p>
                <p className="mt-1">{testResult.message}</p>
                <dl className="mt-2 flex flex-wrap gap-x-5 gap-y-1 text-xs">
                  {Object.entries(testResult)
                    .filter(([key]) => !['ok', 'message'].includes(key))
                    .map(([key, value]) => (
                      <div key={key} className="flex gap-1.5">
                        <dt>{humanize(key)}:</dt>
                        <dd className="font-medium">{typeof value === 'object' ? JSON.stringify(value) : String(value)}</dd>
                      </div>
                    ))}
                </dl>
              </div>
            )}

            <div className="flex flex-wrap gap-2">
              <button type="button" className="btn-secondary" onClick={() => test(false)} disabled={testing}>
                {testing ? 'Testing…' : 'Test connection'}
              </button>
              <button type="button" className="btn-secondary" onClick={() => test(true)} disabled={testing}>
                Test with a real completion
              </button>
              <button type="button" className="btn-primary ml-auto" onClick={save} disabled={saving}>
                {saving ? 'Saving…' : 'Save configuration'}
              </button>
            </div>
          </div>
        </section>

        <section className="card p-5">
          <h2 className="text-base font-semibold text-slate-900 dark:text-slate-100">Saved configurations</h2>
          {cfgLoading && <Spinner label="Loading" />}
          {configs && configs.length === 0 && (
            <p className="mt-3 text-sm text-slate-500 dark:text-slate-400">
              None saved. The system runs in deterministic offline mode.
            </p>
          )}
          {configs && configs.length > 0 && (
            <ul className="mt-3 space-y-3">
              {configs.map((config) => (
                <li key={config.id} className="rounded-lg border border-slate-200 p-3 dark:border-slate-700">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-medium text-slate-900 dark:text-slate-100">{config.name}</span>
                    {config.is_active && (
                      <Badge className="bg-emerald-100 text-emerald-800 ring-emerald-600/20 dark:bg-emerald-950 dark:text-emerald-200 dark:ring-emerald-400/30">
                        active
                      </Badge>
                    )}
                    {config.allow_paid_requests ? (
                      <Badge className="bg-amber-100 text-amber-900 ring-amber-600/20 dark:bg-amber-950 dark:text-amber-200 dark:ring-amber-400/30">
                        paid enabled
                      </Badge>
                    ) : (
                      <Badge>paid blocked</Badge>
                    )}
                  </div>
                  <dl className="mt-2 space-y-0.5 text-xs text-slate-600 dark:text-slate-400">
                    <div className="flex justify-between gap-2"><dt>Provider</dt><dd>{humanize(config.provider_type)}</dd></div>
                    <div className="flex justify-between gap-2"><dt>Model</dt><dd className="truncate font-mono">{config.model || '—'}</dd></div>
                    <div className="flex justify-between gap-2"><dt>Key</dt><dd className="font-mono">{config.api_key_last4 ? `…${config.api_key_last4}` : 'none'}</dd></div>
                    <div className="flex justify-between gap-2"><dt>Spent / cap</dt><dd>${config.spent_usd.toFixed(4)} / ${config.request_budget_usd.toFixed(2)}</dd></div>
                    <div className="flex justify-between gap-2">
                      <dt>Tokens used</dt>
                      <dd>
                        {config.spent_tokens.toLocaleString()}
                        {config.token_budget > 0 ? ` / ${config.token_budget.toLocaleString()}` : ' / no ceiling'}
                      </dd>
                    </div>
                    {config.last_test_at && (
                      <div className="flex justify-between gap-2">
                        <dt>Last test</dt>
                        <dd className={config.last_test_ok ? 'text-emerald-600 dark:text-emerald-400' : 'text-red-600 dark:text-red-400'}>
                          {config.last_test_ok ? 'ok' : 'failed'} · {formatDate(config.last_test_at)}
                        </dd>
                      </div>
                    )}
                  </dl>
                  {config.last_test_message && (
                    <p className="mt-1.5 text-xs text-slate-500 dark:text-slate-400">{config.last_test_message}</p>
                  )}
                  <div className="mt-2 flex gap-2">
                    <button type="button" className="btn-ghost text-xs" onClick={() => loadExisting(config.name)}>
                      Load
                    </button>
                    <button
                      type="button"
                      className="btn-ghost text-xs text-red-600 dark:text-red-400"
                      onClick={() => remove(config.id, config.name)}
                    >
                      Delete
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          )}

          {catalogue && (
            <div className="mt-5 border-t border-slate-200 pt-4 dark:border-slate-800">
              <p className="text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
                Environment default
              </p>
              <dl className="mt-2 space-y-0.5 text-xs text-slate-600 dark:text-slate-400">
                {Object.entries(catalogue.env_default).map(([key, value]) => (
                  <div key={key} className="flex justify-between gap-2">
                    <dt>{humanize(key)}</dt>
                    <dd className="truncate font-mono">{String(value || '—')}</dd>
                  </div>
                ))}
              </dl>
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
