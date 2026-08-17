import React, { useEffect, useState } from 'react';
import { getSettings, updateSettings, removeGeminiApiKey } from '../api/client';
import { SettingsIcon, CheckIcon, XIcon, ExternalIcon } from '../components/Icons';
import Select from '../components/Select';

const MODEL_OPTIONS = [
  { value: 'gemini-3.1-flash-lite', label: 'Gemini 3.1 Flash Lite' },
  { value: 'gemini-2.5-flash', label: 'Gemini 2.5 Flash' },
  { value: 'gemini-3-flash-preview', label: 'Gemini 3 Flash Preview' },
];

export default function Settings() {
  const [settings, setSettings] = useState<{
    default_gemini_model: string;
    gemini_api_key_set: boolean;
    gemini_api_key_last4?: string;
  } | null>(null);
  const [key, setKey] = useState('');
  const [model, setModel] = useState('gemini-3.1-flash-lite');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [modelSaving, setModelSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const fetchSettings = async () => {
    try {
      setLoading(true);
      setError(null);
      const res = await getSettings();
      setSettings(res.data);
      setModel(res.data.default_gemini_model);
    } catch (err: any) {
      setError(err.response?.data?.detail || err.message || 'Failed to load settings');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchSettings();
  }, []);

  const handleSaveKey = async (e: React.FormEvent) => {
    e.preventDefault();
    setSaving(true);
    setError(null);
    setSuccess(null);
    try {
      const res = await updateSettings({ gemini_api_key: key || null });
      setSettings(res.data);
      setKey('');
      setSuccess('API key saved.');
    } catch (err: any) {
      setError(err.response?.data?.detail || err.message || 'Failed to save API key');
    } finally {
      setSaving(false);
    }
  };

  const handleModelChange = async (value: string) => {
    setModel(value);
    setModelSaving(true);
    setError(null);
    try {
      const res = await updateSettings({ default_gemini_model: value });
      setSettings(res.data);
    } catch (err: any) {
      setError(err.response?.data?.detail || err.message || 'Failed to save model');
      setModel(settings?.default_gemini_model || 'gemini-3.1-flash-lite');
    } finally {
      setModelSaving(false);
    }
  };

  const handleRemove = async () => {
    if (!confirm('Remove the saved Gemini API key?')) {
      return;
    }
    setSaving(true);
    setError(null);
    setSuccess(null);
    try {
      const res = await removeGeminiApiKey();
      setSettings(res.data);
      setKey('');
      setSuccess('API key removed.');
    } catch (err: any) {
      setError(err.response?.data?.detail || err.message || 'Failed to remove key');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fade-in mx-auto max-w-2xl">
      {/* Header */}
      <div className="mb-6 flex items-center gap-4">
        <span className="grid h-12 w-12 place-items-center rounded-2xl bg-gradient-to-br from-blue-600 via-blue-500 to-cyan-400 text-white shadow-[0_10px_28px_-10px_rgba(37,99,235,0.85)] ring-1 ring-white/20">
          <SettingsIcon width={22} height={22} />
        </span>
        <div>
          <h2 className="font-display text-3xl font-bold tracking-tight">Settings</h2>
          <p className="mt-1 text-sm text-[var(--text-dim)]">
            Configure CoreClip to use your own API keys.
          </p>
        </div>
      </div>

      <div className="glass sheen p-7">
        {loading && !settings ? (
          <div className="py-8 text-center text-sm text-[var(--text-dim)]">Loading settings…</div>
        ) : (
          <>
            {error && (
              <div className="mb-6 flex items-start gap-3 rounded-xl border border-rose-500/30 bg-rose-500/10 p-4 text-sm text-rose-200">
                <XIcon width={18} height={18} className="mt-0.5 flex-none text-rose-300" />
                <span>{error}</span>
              </div>
            )}

            {success && (
              <div className="mb-6 flex items-start gap-3 rounded-xl border border-emerald-500/30 bg-emerald-500/10 p-4 text-sm text-emerald-200">
                <CheckIcon width={18} height={18} className="mt-0.5 flex-none text-emerald-300" />
                <span>{success}</span>
              </div>
            )}

            <div className="space-y-5">
              <div>
                <div className="mb-2 flex items-center justify-between">
                  <label className="text-sm font-semibold text-[var(--text)]">
                    Default Gemini model
                  </label>
                  {modelSaving && (
                    <span className="text-[11px] text-[var(--text-faint)]">Saving…</span>
                  )}
                </div>
                <Select value={model} onChange={handleModelChange} options={MODEL_OPTIONS} />
              </div>

              <form onSubmit={handleSaveKey} className="space-y-3">
                <div>
                  <label className="mb-2 block text-sm font-semibold text-[var(--text)]">
                    Gemini API key
                  </label>

                  {settings?.gemini_api_key_set && (
                    <div className="mb-2 flex items-center gap-2 text-xs">
                      <span className="inline-flex items-center gap-1 rounded-full bg-emerald-500/15 px-2 py-0.5 text-emerald-300 ring-1 ring-emerald-500/25">
                        <span className="h-1 w-1 rounded-full bg-emerald-400" />
                        Active
                      </span>
                      <span className="text-[var(--text-dim)]">···{settings.gemini_api_key_last4}</span>
                      <span className="text-[var(--text-faint)]">(saved)</span>
                    </div>
                  )}

                  {!settings?.gemini_api_key_set && (
                    <div className="mb-2 rounded-lg border border-amber-500/25 bg-amber-500/10 p-2 text-xs text-amber-200">
                      No Gemini API key is configured.
                    </div>
                  )}

                  <input
                    type="password"
                    value={key}
                    onChange={(e) => setKey(e.target.value)}
                    className="input"
                    placeholder="Paste your Gemini API key here"
                    autoComplete="off"
                  />
                  <p className="mt-1.5 text-[11px] leading-relaxed text-[var(--text-faint)]">
                    Stored locally in user_settings.json and used for all Gemini API calls.
                  </p>
                </div>

                <div className="flex items-center gap-3">
                  <button
                    type="submit"
                    disabled={saving}
                    className="btn btn-primary"
                  >
                    {saving ? 'Saving…' : 'Save API key'}
                  </button>

                  {settings?.gemini_api_key_set && (
                    <button
                      type="button"
                      onClick={handleRemove}
                      disabled={saving}
                      className="btn border-white/10 bg-white/5 text-[var(--text)] hover:bg-white/10"
                    >
                      Remove key
                    </button>
                  )}
                </div>
              </form>
            </div>

            <div className="mt-8 border-t border-white/10 pt-6">
              <h3 className="mb-2 text-sm font-semibold text-[var(--text)]">Need a key?</h3>
              <p className="text-sm text-[var(--text-dim)]">
                Get a Gemini API key from Google AI Studio. If you hit quota limits,
                make sure billing is enabled on your Google Cloud project.
              </p>
              <a
                href="https://aistudio.google.com/app/apikey"
                target="_blank"
                rel="noreferrer"
                className="mt-3 inline-flex items-center gap-1.5 text-sm font-medium text-cyan-300 hover:underline"
              >
                Open Google AI Studio <ExternalIcon width={14} height={14} />
              </a>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
