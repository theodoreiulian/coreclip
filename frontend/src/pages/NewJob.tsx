import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { createJob } from '../api/client';
import { SparkIcon, BoltIcon, XIcon, ArrowRightIcon } from '../components/Icons';
import Select from '../components/Select';

export default function NewJob() {
  const [url, setUrl] = useState('');
  const [model, setModel] = useState('gemini-2.5-flash');
  const [error, setError] = useState<any>(null);
  const navigate = useNavigate();

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);

    try {
      const res = await createJob({
        youtube_url: url,
        target_platforms: ['tiktok'],  // only TikTok is enabled for now
        gemini_model: model
      });
      navigate(`/jobs/${res.data.id}`);
    } catch (err: any) {
      if (err.response?.status === 409) {
        setError(err.response.data.detail);
      } else {
        setError(err.response?.data?.detail || err.message);
      }
    }
  };

  return (
    <div className="fade-in mx-auto max-w-2xl">
      {/* Header */}
      <div className="mb-6 flex items-center gap-4">
        <span className="grid h-12 w-12 place-items-center rounded-2xl bg-gradient-to-br from-blue-600 via-blue-500 to-cyan-400 text-white shadow-[0_10px_28px_-10px_rgba(37,99,235,0.85)] ring-1 ring-white/20">
          <SparkIcon width={22} height={22} />
        </span>
        <div>
          <h2 className="font-display text-3xl font-bold tracking-tight">New Clipping Job</h2>
          <p className="mt-1 text-sm text-[var(--text-dim)]">
            Drop a video in — CoreClip finds the viral moments and cuts them for you.
          </p>
        </div>
      </div>

      <div className="glass sheen p-7">
        {error && (
          <div className="mb-6 flex items-start gap-3 rounded-xl border border-rose-500/30 bg-rose-500/10 p-4 text-sm text-rose-200">
            <XIcon width={18} height={18} className="mt-0.5 flex-none text-rose-300" />
            <div>
              <span>{error.message || String(error)}</span>
              {error.job_id && (
                <a
                  href={`/jobs/${error.job_id}`}
                  className="ml-2 inline-flex items-center gap-1 font-semibold text-cyan-300 underline-offset-2 hover:underline"
                >
                  View existing job <ArrowRightIcon width={13} height={13} />
                </a>
              )}
            </div>
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-6">
          <div>
            <label className="mb-2 block text-sm font-semibold text-[var(--text)]">
              YouTube URL
            </label>
            <input
              required type="url" value={url} onChange={e => setUrl(e.target.value)}
              className="input"
              placeholder="https://youtube.com/watch?v=..."
            />
          </div>

          <div>
            <label className="mb-2 block text-sm font-semibold text-[var(--text)]">
              Target platform
            </label>
            <div className="flex flex-wrap items-center gap-3">
              <span className="badge badge-rose">
                <span className="dot" />
                TikTok
              </span>
              <span className="text-xs text-[var(--text-faint)]">
                Other platforms are temporarily disabled.
              </span>
            </div>
          </div>

          <div>
            <label className="mb-2 block text-sm font-semibold text-[var(--text)]">
              Gemini model
            </label>
            <Select
              value={model}
              onChange={setModel}
              options={[
                { value: 'gemini-2.5-flash', label: 'Gemini 2.5 Flash · fast' },
                { value: 'gemini-2.5-pro', label: 'Gemini 2.5 Pro · highest quality' },
              ]}
            />
            <p className="mt-2 text-xs text-[var(--text-faint)]">
              Flash is quicker and cheaper; Pro reasons harder about what will go viral.
            </p>
          </div>

          <button type="submit" className="btn btn-primary btn-lg w-full">
            <BoltIcon width={18} height={18} />
            Start Job
          </button>
        </form>
      </div>
    </div>
  );
}
