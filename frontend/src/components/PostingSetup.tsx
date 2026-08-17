import React, { useEffect, useMemo, useState } from 'react';
import { Job, Account, PostingPlan, Clip } from '../types';
import {
  getPostingPlan, connectAccount, disconnectAccount, schedulePosts,
  getTiktokConfigStatus, startTiktokLogin, checkTiktokLoginStatus, connectTiktokExisting,
} from '../api/client';
import {
  SendIcon, ExternalIcon, CalendarIcon, UserIcon, LinkIcon, BoltIcon,
} from './Icons';

// Only TikTok is enabled for now. To re-enable the others, add them back here
// (the labels below already cover them, and old posts still render by platform).
const PLATFORMS = ['tiktok'];
const PLATFORM_LABEL: Record<string, string> = {
  tiktok: 'TikTok', instagram: 'Instagram', youtube: 'YouTube Shorts', snapchat: 'Snapchat',
};

function pad(n: number) { return String(n).padStart(2, '0'); }

function toLocalInput(d: Date): string {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}` +
    `T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function parseUTC(s: string): Date {
  return new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(s) ? s : s + 'Z');
}

// Gap (in minutes) between two consecutive posts: the average ± a random jitter,
// drawn uniformly from [avg - jitter, avg + jitter]. Clamped to ≥ 1 min so a large
// jitter on a small average can never produce a zero/negative gap. Spreading posts
// off a fixed cadence is what keeps the account from looking like a bot.
function jitteredGapMin(avgMin: number, jitterMin: number): number {
  const jitter = (Math.random() * 2 - 1) * jitterMin;
  return Math.max(1, avgMin + jitter);
}

// Soft-badge variant for a scheduled post's status.
function statusColor(status: string): string {
  if (status === 'posted') return 'badge-emerald';
  if (status === 'failed') return 'badge-rose';
  if (status === 'posting') return 'badge-amber';
  if (status === 'cancelled') return 'badge-slate';
  return 'badge-sky';
}

export default function PostingSetup({ job, clips, onUpdate }: { job: Job; clips: Clip[]; onUpdate: () => void }) {
  const [plan, setPlan] = useState<PostingPlan | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [times, setTimes] = useState<Record<string, string>>({});
  const [connectFor, setConnectFor] = useState<string | null>(null);
  const [connectName, setConnectName] = useState('');
  const [quickStart, setQuickStart] = useState(toLocalInput(new Date(Date.now() + 60_000)));
  const [quickInterval, setQuickInterval] = useState(240);
  const [quickJitter, setQuickJitter] = useState(30);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [tiktokReady, setTiktokReady] = useState<boolean | null>(null);
  const [tiktokLabel, setTiktokLabel] = useState<string>('');
  const [connectingTikTok, setConnectingTikTok] = useState(false);
  const [showLoginWindow, setShowLoginWindow] = useState(false);
  // Live clock so the scheduler always knows the current time (the page can sit open from
  // when the job started). Drives the inputs' `min`, the quick-fill start, and the
  // can't-schedule-in-the-past guard at submit.
  const [now, setNow] = useState(() => Date.now());

  const isSetup = job.status === 'ready_for_review';

  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 30_000);
    return () => clearInterval(id);
  }, []);

  // Keep the "Start" field live: if it has slipped into the past (e.g. left open since the
  // job started), bump it to ~now so a quick-fill can never begin in the past.
  useEffect(() => {
    setNow(Date.now());
    setQuickStart(prev =>
      new Date(prev).getTime() < Date.now() ? toLocalInput(new Date(Date.now() + 60_000)) : prev
    );
  }, [job.status]);
  useEffect(() => {
    setQuickStart(prev =>
      new Date(prev).getTime() < now ? toLocalInput(new Date(now + 60_000)) : prev
    );
  }, [now]);

  const fetchPlan = async () => {
    try {
      const { data } = await getPostingPlan(job.id);
      setPlan(data);
      setTimes(prev => {
        const next = { ...prev };
        // Seed default times for any clip without one, cumulatively jittered so the
        // placeholder schedule already reflects the avg ± random spacing. A still-valid
        // (future) entry is preserved and re-anchors the running clock so approving/rejecting
        // clips never re-rolls times the user is looking at — but an entry that has slipped
        // into the past (e.g. seeded when the page first loaded) is dropped and re-seeded so
        // defaults always track the live clock rather than the job's start time.
        let t = Date.now() + 60_000;
        data.clips.forEach((c, i) => {
          const ms = next[c.id] ? new Date(next[c.id]).getTime() : NaN;
          if (!Number.isNaN(ms) && ms >= Date.now()) {
            t = ms;
            return;
          }
          if (i > 0) t += jitteredGapMin(quickInterval, quickJitter) * 60_000;
          next[c.id] = toLocalInput(new Date(t));
        });
        return next;
      });
    } catch (e) {
      console.error(e);
    }
  };

  // Re-fetch when clip statuses change too (approve/reject) — the job status
  // stays 'ready_for_review' through review, so keying only on it would leave
  // the posting plan showing a stale "0 approved" snapshot.
  const clipStatusSig = clips.map(c => `${c.id}:${c.status}`).join(',');
  useEffect(() => { fetchPlan(); }, [job.id, job.status, clipStatusSig]);

  useEffect(() => {
    getTiktokConfigStatus()
      .then(r => { setTiktokReady(r.data.configured); setTiktokLabel(r.data.label); })
      .catch(() => setTiktokReady(false));
  }, []);

  const accountsByPlatform = useMemo(() => {
    const m: Record<string, Account[]> = {};
    PLATFORMS.forEach(p => { m[p] = []; });
    plan?.accounts.forEach(a => { (m[a.platform] ||= []).push(a); });
    return m;
  }, [plan]);

  const accountName = (id: string) =>
    plan?.accounts.find(a => a.id === id)?.display_name || 'account';

  const toggleAccount = (id: string) => {
    setSelected(prev => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  };

  const handleConnect = async (platform: string) => {
    if (!connectName.trim()) return;
    try {
      await connectAccount(platform, connectName.trim());
      setConnectName('');
      setConnectFor(null);
      await fetchPlan();
    } catch (e) { console.error(e); }
  };

  // TikTok (primary): reuse the existing login from the user's real Chrome.
  // Avoids the login form, which TikTok blocks inside an automated browser.
  const handleConnectTikTokExisting = async () => {
    setError(null);
    setConnectingTikTok(true);
    try {
      await connectTiktokExisting();
      await fetchPlan();
    } catch (e: any) {
      setError(e?.response?.data?.detail || 'Could not reuse your Chrome login');
    } finally {
      setConnectingTikTok(false);
    }
  };

  // TikTok (fallback): backend spawns a browser login window; we poll /login-status.
  const handleConnectTikTok = async () => {
    setError(null);
    setConnectingTikTok(true);
    try {
      const { data } = await startTiktokLogin();
      const started = Date.now();
      const poll = async () => {
        if (Date.now() - started > 300_000) {
          setConnectingTikTok(false);
          setError('Login timed out. Try again.');
          return;
        }
        try {
          const res = await checkTiktokLoginStatus(data.state);
          if (res.data.error) {
            setConnectingTikTok(false);
            setError(res.data.error);
            return;
          }
          if (res.data.handle) {
            setConnectingTikTok(false);
            await fetchPlan();
            return;
          }
          setTimeout(poll, 2000);
        } catch { setTimeout(poll, 2000); }
      };
      setTimeout(poll, 2000);
    } catch (e: any) {
      setConnectingTikTok(false);
      setError(e?.response?.data?.detail || 'Could not start TikTok login');
    }
  };

  const handleDisconnect = async (id: string) => {
    try {
      await disconnectAccount(id);
      setSelected(prev => { const n = new Set(prev); n.delete(id); return n; });
      await fetchPlan();
    } catch (e) { console.error(e); }
  };

  const applyQuickFill = () => {
    if (!plan) return;
    // First clip lands at the start; each subsequent clip is one jittered gap later.
    // Re-clicking re-rolls the random gaps. Floor the start at ~now (read live, not from
    // the possibly-stale `quickStart`) so a quick-fill can never begin in the past.
    let t = Math.max(new Date(quickStart).getTime(), Date.now() + 60_000);
    const next: Record<string, string> = {};
    plan.clips.forEach((c, i) => {
      if (i > 0) t += jitteredGapMin(quickInterval, quickJitter) * 60_000;
      next[c.id] = toLocalInput(new Date(t));
    });
    setTimes(next);
  };

  const handleSchedule = async () => {
    if (!plan) return;
    setError(null);

    // Final guard: nothing may be scheduled in the past. If the earliest time has slipped
    // behind the live clock (the plan was filled a while ago), shift the WHOLE plan forward
    // by that delta so the spacing the user chose is preserved, and reflect the correction
    // in the inputs so it's visible rather than silent.
    const floor = Date.now() + 30_000;
    const msById: Record<string, number> = {};
    plan.clips.forEach(c => { msById[c.id] = new Date(times[c.id]).getTime(); });
    const valid = Object.values(msById).filter(ms => !Number.isNaN(ms));
    const earliest = valid.length ? Math.min(...valid) : floor;
    const delta = Math.max(0, floor - earliest);

    const isoById: Record<string, string> = {};
    const localById: Record<string, string> = {};
    plan.clips.forEach(c => {
      const ms = (Number.isNaN(msById[c.id]) ? floor : msById[c.id]) + delta;
      isoById[c.id] = new Date(ms).toISOString();
      localById[c.id] = toLocalInput(new Date(ms));
    });
    if (delta > 0) setTimes(prev => ({ ...prev, ...localById }));

    setSaving(true);
    try {
      await schedulePosts(
        job.id,
        [...selected],
        plan.clips.map(c => ({
          clip_id: c.id,
          scheduled_time: isoById[c.id],
        })),
      );
      await onUpdate();
    } catch (e: any) {
      setError(e?.response?.data?.detail || 'Failed to schedule posts');
    } finally {
      setSaving(false);
    }
  };

  if (!plan) {
    return (
      <div className="glass sheen flex items-center gap-3 p-6 text-[var(--text-dim)]">
        <span className="spinner" /> Loading posting plan…
      </div>
    );
  }

  // ── Monitor mode: job already posting / completed ──────────────────
  if (!isSetup) {
    const byClip: Record<string, typeof plan.scheduled_posts> = {};
    plan.scheduled_posts.forEach(p => { (byClip[p.clip_id] ||= []).push(p); });
    return (
      <div className="glass sheen space-y-4 p-6">
        <div className="flex items-center gap-3">
          <span className="grid h-10 w-10 place-items-center rounded-xl bg-cyan-500/15 text-cyan-300 ring-1 ring-inset ring-cyan-400/25">
            <SendIcon width={18} height={18} />
          </span>
          <div>
            <h3 className="font-display text-xl font-bold">Posting</h3>
            <p className="text-sm text-[var(--text-dim)]">
              Job status: <span className="font-mono text-[var(--text)]">{job.status}</span> ·{' '}
              {plan.scheduled_posts.length} scheduled post(s)
            </p>
          </div>
        </div>
        {plan.clips.map(clip => {
          const posts = byClip[clip.id] || [];
          return (
            <div key={clip.id} className="rounded-xl border border-white/[0.07] bg-white/[0.02] p-4">
              <div className="mb-3 font-display font-semibold text-white">{`Clip ${clip.clip_index}`}</div>
              <div className="space-y-2">
                {posts.map(p => (
                  <div key={p.id} className="flex items-center justify-between gap-3 text-sm">
                    <span className="flex items-center gap-2 text-[var(--text-dim)]">
                      <UserIcon width={14} height={14} className="text-[var(--text-faint)]" />
                      {PLATFORM_LABEL[p.platform] || p.platform} · {accountName(p.account_id)}
                    </span>
                    <span className="flex items-center gap-3">
                      <span className="text-[var(--text-faint)]">{parseUTC(p.scheduled_time).toLocaleString()}</span>
                      {p.result_url && (
                        <a href={p.result_url} target="_blank" rel="noreferrer"
                           className="inline-flex items-center gap-1 text-cyan-300 hover:underline">
                          view <ExternalIcon width={12} height={12} />
                        </a>
                      )}
                      <span className={`badge ${statusColor(p.status)}`} title={p.error_message || ''}>
                        {p.status}
                      </span>
                    </span>
                  </div>
                ))}
                {!posts.length && (
                  <div className="text-sm text-[var(--text-faint)]">No posts scheduled.</div>
                )}
              </div>
            </div>
          );
        })}
      </div>
    );
  }

  // ── Setup mode: job ready_for_review ───────────────────────────────
  const noClips = plan.clips.length === 0;
  const canSchedule = !noClips && selected.size > 0 && !saving;
  const totalPosts = plan.clips.length * selected.size;

  return (
    <div className="glass sheen space-y-7 p-6">
      <div className="flex items-center gap-3">
        <span className="grid h-10 w-10 place-items-center rounded-xl bg-gradient-to-br from-blue-500 to-cyan-500 text-white ring-1 ring-white/20">
          <SendIcon width={18} height={18} />
        </span>
        <div>
          <h3 className="font-display text-xl font-bold">Set up posting</h3>
          <p className="text-sm text-[var(--text-dim)]">
            Connect accounts, choose where each clip posts, and set the times.
          </p>
        </div>
      </div>

      {/* Accounts / channels */}
      <div>
        <h4 className="mb-3 flex items-center gap-2 font-display font-semibold">
          <UserIcon width={16} height={16} className="text-blue-300" /> Accounts
        </h4>
        <div className="grid gap-3 sm:grid-cols-2">
          {PLATFORMS.map(platform => {
            const isTikTok = platform === 'tiktok';
            return (
              <div key={platform} className="rounded-xl border border-white/[0.08] bg-white/[0.02] p-4">
                <div className="mb-3 flex items-center justify-between">
                  <span className="font-semibold">{PLATFORM_LABEL[platform]}</span>
                  {isTikTok ? (
                    <button
                      onClick={handleConnectTikTokExisting}
                      disabled={tiktokReady !== true || connectingTikTok}
                      className="btn btn-pink btn-sm"
                      title={tiktokReady === false
                        ? (tiktokLabel || 'TikTok automation not ready')
                        : 'Reuse your TikTok login from Google Chrome'}
                    >
                      <LinkIcon width={14} height={14} />
                      {connectingTikTok ? 'Connecting…' : 'Connect TikTok'}
                    </button>
                  ) : (
                    <button
                      onClick={() => { setConnectFor(connectFor === platform ? null : platform); setConnectName(''); }}
                      className="btn btn-ghost btn-sm"
                    >+ Connect</button>
                  )}
                </div>

                <div className="space-y-1.5">
                  {accountsByPlatform[platform].map(acc => (
                    <label
                      key={acc.id}
                      className="flex items-center gap-2.5 rounded-lg border border-white/[0.06] bg-white/[0.02] px-2.5 py-2 text-sm transition hover:border-white/15"
                    >
                      <input
                        type="checkbox"
                        checked={selected.has(acc.id)}
                        onChange={() => toggleAccount(acc.id)}
                      />
                      {acc.avatar_url && (
                        <img src={acc.avatar_url} alt="" className="h-6 w-6 rounded-full ring-1 ring-white/15" />
                      )}
                      <span className="flex-1 truncate">{acc.display_name}</span>
                      <button
                        onClick={() => handleDisconnect(acc.id)}
                        className="text-xs text-rose-300/80 transition hover:text-rose-300"
                      >disconnect</button>
                    </label>
                  ))}
                  {!accountsByPlatform[platform].length && (
                    <div className="rounded-lg border border-dashed border-white/10 px-2.5 py-3 text-center text-xs text-[var(--text-faint)]">
                      No accounts connected.
                    </div>
                  )}
                </div>

                {isTikTok && tiktokReady === false && (
                  <p className="mt-3 text-xs text-amber-300/90">
                    {tiktokLabel === 'Google Chrome not installed' ? (
                      <>Google Chrome is required. Install it from{' '}
                      <code className="rounded bg-black/40 px-1 py-0.5">google.com/chrome</code>, then restart.</>
                    ) : (
                      <>TikTok browser automation isn't ready ({tiktokLabel || 'unavailable'}).
                      Install the Playwright package in the backend venv, then restart.</>
                    )}
                  </p>
                )}

                {isTikTok && tiktokReady && (
                  <div className="mt-3 space-y-2">
                    <p className="text-[11px] leading-snug text-[var(--text-faint)]">
                      Connecting <strong className="text-[var(--text-dim)]">reuses your existing TikTok login from Google
                      Chrome</strong>. Make sure you're logged into TikTok in Chrome (ideally
                      quit Chrome first so its cookies can be read). A brief Chrome window opens
                      to confirm — and a one-time macOS Keychain prompt may appear; click
                      <strong className="text-[var(--text-dim)]"> Allow</strong>.
                    </p>
                    <div>
                      <button
                        onClick={() => setShowLoginWindow(s => !s)}
                        className="text-[11px] text-cyan-300 transition hover:text-cyan-200"
                      >{showLoginWindow ? 'Cancel' : 'Not logged into TikTok in Chrome? Open a login window'}</button>
                      {showLoginWindow && (
                        <button
                          onClick={handleConnectTikTok}
                          disabled={connectingTikTok}
                          className="btn btn-ghost btn-sm mt-1.5 block"
                        >{connectingTikTok ? 'Connecting…' : 'Open Google Chrome login window'}</button>
                      )}
                    </div>
                  </div>
                )}

                {/* Stub connect input (non-TikTok platforms only) */}
                {!isTikTok && connectFor === platform && (
                  <div className="mt-3 flex gap-2">
                    <input
                      autoFocus
                      value={connectName}
                      onChange={e => setConnectName(e.target.value)}
                      onKeyDown={e => e.key === 'Enter' && handleConnect(platform)}
                      placeholder="@handle or channel name"
                      className="input input-sm flex-1"
                    />
                    <button
                      onClick={() => handleConnect(platform)}
                      className="btn btn-success btn-sm"
                    >Connect</button>
                  </div>
                )}
              </div>
            );
          })}
        </div>
        <p className="mt-3 text-xs text-[var(--text-faint)]">
          At each post's scheduled time, CoreClip <strong className="text-[var(--text-dim)]">publishes the TikTok video for you</strong>{' '}
          through the connected browser session — so CoreClip needs to be running then.
        </p>
      </div>

      {/* Schedule */}
      <div>
        <div className="mb-3 flex items-center justify-between">
          <h4 className="flex items-center gap-2 font-display font-semibold">
            <CalendarIcon width={16} height={16} className="text-blue-300" /> When to post
          </h4>
        </div>
        <div className="mb-3 flex flex-wrap items-end gap-3 rounded-xl border border-white/[0.07] bg-black/25 p-3">
          <label className="text-sm">
            <div className="mb-1 text-[var(--text-dim)]">Start</div>
            <input type="datetime-local" value={quickStart} min={toLocalInput(new Date(now))}
              onChange={e => setQuickStart(e.target.value)}
              className="input input-sm" />
          </label>
          <label className="text-sm">
            <div className="mb-1 text-[var(--text-dim)]">Avg interval (min)</div>
            <input type="number" min={1} value={quickInterval}
              onChange={e => setQuickInterval(Number(e.target.value))}
              className="input input-sm w-24" />
          </label>
          <label className="text-sm">
            <div className="mb-1 text-[var(--text-dim)]">± random (min)</div>
            <input type="number" min={0} value={quickJitter}
              onChange={e => setQuickJitter(Number(e.target.value))}
              className="input input-sm w-24" />
          </label>
          <button onClick={applyQuickFill} className="btn btn-ghost btn-sm">
            <BoltIcon width={14} height={14} /> Apply to all clips
          </button>
        </div>
        <p className="mb-3 text-xs text-[var(--text-faint)]">
          Posts are spaced by the average ± a random amount — e.g. 240 ± 30 means each gap is a
          random 3h30–4h30 — so the cadence isn't a robotic fixed interval. Click "Apply to all
          clips" again to re-roll the random times.
        </p>
        {noClips && <p className="text-sm text-[var(--text-faint)]">No approved clips — approve clips below first.</p>}
        <div className="space-y-2">
          {plan.clips.map(clip => (
            <div key={clip.id} className="flex items-center justify-between gap-3 rounded-lg border border-white/[0.06] bg-white/[0.02] px-3 py-2">
              <span className="flex-1 truncate text-sm">
                {`Clip ${clip.clip_index}`}
              </span>
              <input
                type="datetime-local"
                value={times[clip.id] || ''}
                min={toLocalInput(new Date(now))}
                onChange={e => setTimes(prev => ({ ...prev, [clip.id]: e.target.value }))}
                className="input input-sm w-auto"
              />
            </div>
          ))}
        </div>
      </div>

      {error && (
        <div className="rounded-xl border border-rose-500/30 bg-rose-500/10 px-4 py-3 text-sm text-rose-200">
          {error}
        </div>
      )}

      <div className="flex flex-wrap items-center justify-between gap-3 border-t border-white/[0.07] pt-5">
        <span className="text-sm text-[var(--text-dim)]">
          {selected.size} account(s) · {plan.clips.length} clip(s) → <span className="font-semibold text-[var(--text)]">{totalPosts}</span> post(s)
        </span>
        <button
          onClick={handleSchedule}
          disabled={!canSchedule}
          className="btn btn-success btn-lg"
        >
          <SendIcon width={16} height={16} />
          {saving ? 'Scheduling…' : `Schedule ${totalPosts} post(s)`}
        </button>
      </div>
    </div>
  );
}
