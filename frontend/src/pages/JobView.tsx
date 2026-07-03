import React, { useEffect, useRef, useState, useCallback } from 'react';
import { Link, useParams } from 'react-router-dom';
import { getJob, getClips, generateMetadata } from '../api/client';
import { Job, Clip } from '../types';
import PipelineStatus from '../components/PipelineStatus';
import ClipCard from '../components/ClipCard';
import PostingSetup from '../components/PostingSetup';
import { useWebSocket } from '../hooks/useWebSocket';
import LogViewer from '../components/LogViewer';
import { ChevronLeftIcon, FilmIcon } from '../components/Icons';

const POSTING_STATUSES = ['ready_for_review', 'posting', 'completed'];
const WS_REFETCH_THROTTLE_MS = 5000;

function statusBadge(status: string): { cls: string; label: string } {
  if (status === 'completed') return { cls: 'badge-emerald', label: 'completed' };
  if (status === 'failed') return { cls: 'badge-rose', label: 'failed' };
  if (status === 'ready_for_review') return { cls: 'badge-sky', label: 'ready for review' };
  if (status === 'posting') return { cls: 'badge-amber', label: 'posting' };
  return { cls: 'badge-blue', label: status.replace(/_/g, ' ') };
}

export default function JobView() {
  const { id } = useParams<{ id: string }>();
  const [job, setJob] = useState<Job | null>(null);
  const [clips, setClips] = useState<Clip[]>([]);
  const [logTab, setLogTab] = useState<'all' | 'posting'>('all');
  const [generatingMeta, setGeneratingMeta] = useState(false);
  const { lastMessage, connectionStatus } = useWebSocket(job?.status !== 'completed' && job?.status !== 'failed' ? id : undefined);
  const lastStatusRef = useRef<string | null>(null);
  const lastFetchRef = useRef<number>(0);

  const fetchAll = useCallback(async () => {
    if (!id) return;
    try {
      const [jRes, cRes] = await Promise.all([getJob(id), getClips(id)]);
      setJob(jRes.data);
      setClips(cRes.data.items);
      lastStatusRef.current = jRes.data.status;
      lastFetchRef.current = Date.now();
    } catch (e) {
      console.error(e);
    }
  }, [id]);

  useEffect(() => {
    fetchAll();
  }, [fetchAll]);

  useEffect(() => {
    if (!lastMessage || !job) return;

    const statusChanged = lastMessage.status !== lastStatusRef.current;
    const throttled = Date.now() - lastFetchRef.current < WS_REFETCH_THROTTLE_MS;

    // Refetch immediately on stage transitions; otherwise throttle to avoid
    // hammering the API with every per-clip progress event.
    if (statusChanged || !throttled) {
      fetchAll();
    }
  }, [lastMessage, job, fetchAll]);

  if (!job) return (
    <div className="flex items-center gap-3 text-[var(--text-dim)]">
      <span className="spinner" /> Loading job…
    </div>
  );

  const showPosting = POSTING_STATUSES.includes(job.status);
  const missingMetadata = clips.some(clip => !clip.title);

  const handleGenerateMetadata = async () => {
    if (!id || generatingMeta) return;
    setGeneratingMeta(true);
    try {
      await generateMetadata(id);
      await fetchAll();
    } catch (e) {
      console.error(e);
    } finally {
      setGeneratingMeta(false);
    }
  };
  const sb = statusBadge(job.status);
  const wsConnected = connectionStatus === 'connected';

  return (
    <div className="fade-in space-y-7">
      <Link to="/" className="inline-flex items-center gap-1.5 text-sm text-[var(--text-dim)] transition hover:text-white">
        <ChevronLeftIcon width={15} height={15} /> Dashboard
      </Link>

      {/* Header + pipeline */}
      <div className="glass sheen p-6">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <h2 className="max-w-3xl font-display text-2xl font-bold leading-tight">
            {job.video_title || job.youtube_url}
          </h2>
          <div className="flex items-center gap-3">
            <span className={`badge ${sb.cls}`}>
              <span className="dot" />
              {sb.label}
            </span>
            <span className="flex items-center gap-2 rounded-full border border-white/10 bg-white/[0.04] px-2.5 py-1 text-[11px] text-[var(--text-dim)]">
              {wsConnected
                ? <span className="live-dot" />
                : <span className="h-2 w-2 rounded-full bg-[var(--text-faint)]" />}
              {connectionStatus}
            </span>
          </div>
        </div>
        <PipelineStatus currentStage={job.status} status={job.status} errorMessage={job.error_message} />
      </div>

      {showPosting && <PostingSetup job={job} clips={clips} onUpdate={fetchAll} />}

      {/* Clips */}
      <div>
        <div className="mb-4 flex items-center gap-3">
          <h3 className="font-display text-xl font-bold">Clips</h3>
          <span className="badge badge-blue">
            <FilmIcon width={12} height={12} /> {clips.length}
          </span>
          {showPosting && clips.length > 0 && missingMetadata && (
            <button
              onClick={handleGenerateMetadata}
              disabled={generatingMeta}
              className="ml-auto inline-flex items-center gap-2 rounded-full border border-white/10 bg-white/[0.04] px-3.5 py-1.5 text-xs font-semibold text-[var(--text-dim)] transition hover:text-white disabled:opacity-60"
            >
              {generatingMeta && <span className="spinner" />}
              {generatingMeta ? 'Generating metadata…' : 'Generate metadata'}
            </button>
          )}
        </div>
        {clips.length === 0 ? (
          <div className="glass flex items-center justify-center px-6 py-14 text-sm text-[var(--text-dim)]">
            No clips available yet.
          </div>
        ) : (
          <div className="grid gap-6 md:grid-cols-2 xl:grid-cols-3">
            {clips.map(clip => (
              <ClipCard key={clip.id} clip={clip} onUpdate={fetchAll} />
            ))}
          </div>
        )}
      </div>

      {/* Logs */}
      <div className="glass sheen p-6">
        <div className="mb-4 flex items-center gap-3">
          <h3 className="font-display text-xl font-bold">Logs</h3>
          <div className="seg">
            <button data-active={logTab === 'all'} onClick={() => setLogTab('all')} className="seg-btn">
              Job Logs
            </button>
            <button data-active={logTab === 'posting'} onClick={() => setLogTab('posting')} className="seg-btn">
              Posting
            </button>
          </div>
        </div>
        {logTab === 'all' ? (
          <LogViewer jobId={id} emptyMessage="No logs yet." />
        ) : (
          <LogViewer
            jobId={id}
            stage="posting"
            emptyMessage="No posting activity yet — press “Schedule / Post” above to start. Posts run on a ~60s scheduler tick, so give it a moment after pressing."
          />
        )}
      </div>
    </div>
  );
}
