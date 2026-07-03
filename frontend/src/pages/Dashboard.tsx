import React, { useState, useMemo, memo, useCallback } from 'react';
import { Link } from 'react-router-dom';
import { useJobs } from '../hooks/useJobs';
import { cancelJob } from '../api/client';
import { Job } from '../types';
import {
  DotsIcon, TrashIcon, FilmIcon, GridIcon, ClockIcon,
  CheckIcon, SparkIcon,
} from '../components/Icons';

// Map a raw job status onto a soft badge style + label.
function statusBadge(status: string): { cls: string; label: string } {
  if (status === 'completed') return { cls: 'badge-emerald', label: 'completed' };
  if (status === 'failed') return { cls: 'badge-rose', label: 'failed' };
  if (status === 'ready_for_review') return { cls: 'badge-sky', label: 'ready for review' };
  if (status === 'posting') return { cls: 'badge-amber', label: 'posting' };
  return { cls: 'badge-blue', label: status.replace(/_/g, ' ') };
}

function Stat({ icon, label, value, accent }: {
  icon: React.ReactNode; label: string; value: number | string; accent: string;
}) {
  return (
    <div className="glass sheen relative flex items-center gap-4 p-4">
      <span className={`grid h-11 w-11 place-items-center rounded-xl ${accent}`}>
        {icon}
      </span>
      <div>
        <div className="font-display text-2xl font-bold leading-none tabular-nums">{value}</div>
        <div className="mt-1 text-xs font-medium text-[var(--text-dim)]">{label}</div>
      </div>
    </div>
  );
}

const JobCard = memo(function JobCard({
  job,
  openMenu,
  onToggleMenu,
  onDelete
}: {
  job: Job;
  openMenu: string | null;
  onToggleMenu: (e: React.MouseEvent, jobId: string) => void;
  onDelete: (e: React.MouseEvent, jobId: string) => void;
}) {
  const sb = statusBadge(job.status);
  return (
    <Link
      to={`/jobs/${job.id}`}
      className="glass card-hover sheen group relative flex flex-col p-5"
    >
      {/* 3-dot menu */}
      <button
        onClick={(e) => onToggleMenu(e, job.id)}
        className="absolute right-3 top-3 grid h-7 w-7 place-items-center rounded-lg text-[var(--text-faint)] transition hover:bg-white/10 hover:text-white"
        aria-label="Job actions"
      >
        <DotsIcon width={16} height={16} />
      </button>

      {openMenu === job.id && (
        <div
          className="glass-2 absolute right-3 top-11 z-20 min-w-[170px] overflow-hidden p-1"
          onClick={e => e.stopPropagation()}
        >
          <button
            onClick={(e) => onDelete(e, job.id)}
            className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-sm text-rose-300 transition hover:bg-rose-500/15"
          >
            <TrashIcon width={15} height={15} />
            Remove from queue
          </button>
        </div>
      )}

      <span className="mb-4 grid h-10 w-10 place-items-center rounded-xl bg-white/[0.04] text-cyan-300 ring-1 ring-inset ring-white/10">
        <FilmIcon width={18} height={18} />
      </span>

      <h3 className="mb-3 line-clamp-2 pr-6 font-display text-base font-semibold leading-snug text-white">
        {job.video_title || job.youtube_url}
      </h3>

      <div className="mt-auto flex items-center justify-between pt-3">
        <span className={`badge ${sb.cls}`}>
          <span className="dot" />
          {sb.label}
        </span>
        <span className="flex items-center gap-1.5 text-xs text-[var(--text-dim)]">
          <FilmIcon width={13} height={13} className="text-[var(--text-faint)]" />
          {job.clip_count ?? 0} clips
        </span>
      </div>

      <div className="mt-3 flex items-center gap-1.5 border-t border-white/[0.06] pt-3 text-[11px] text-[var(--text-faint)]">
        <ClockIcon width={12} height={12} />
        {new Date(job.created_at).toLocaleString()}
      </div>
    </Link>
  );
});

export default function Dashboard() {
  const { jobs, loading, refetch } = useJobs();
  const [openMenu, setOpenMenu] = useState<string | null>(null);

  const handleDelete = useCallback(async (e: React.MouseEvent, jobId: string) => {
    e.preventDefault();
    e.stopPropagation();
    try {
      await cancelJob(jobId);
      setOpenMenu(null);
      refetch();
    } catch (err) {
      console.error(err);
    }
  }, [refetch]);

  const toggleMenu = useCallback((e: React.MouseEvent, jobId: string) => {
    e.preventDefault();
    e.stopPropagation();
    setOpenMenu(prev => prev === jobId ? null : jobId);
  }, []);

  // Derived, presentation-only KPIs from the already-loaded jobs.
  const stats = useMemo(() => {
    const total = jobs.length;
    const active = jobs.filter(j => !['completed', 'failed', 'ready_for_review'].includes(j.status)).length;
    const review = jobs.filter(j => j.status === 'ready_for_review').length;
    const clips = jobs.reduce((n, j) => n + (j.clip_count ?? 0), 0);
    return { total, active, review, clips };
  }, [jobs]);

  return (
    <div className="fade-in" onClick={() => setOpenMenu(null)}>
      {/* Header */}
      <div className="mb-8">
        <h2 className="font-display text-3xl font-bold tracking-tight">Dashboard</h2>
        <p className="mt-1 text-sm text-[var(--text-dim)]">
          Your clipping pipeline at a glance.
        </p>
      </div>

      {/* KPI strip */}
      <div className="mb-8 grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Stat label="Total jobs" value={stats.total} accent="bg-blue-500/15 text-blue-300 ring-1 ring-inset ring-blue-400/25"
          icon={<GridIcon width={20} height={20} />} />
        <Stat label="In pipeline" value={stats.active} accent="bg-sky-500/15 text-sky-300 ring-1 ring-inset ring-sky-400/25"
          icon={<ClockIcon width={20} height={20} />} />
        <Stat label="Ready to review" value={stats.review} accent="bg-cyan-500/15 text-cyan-300 ring-1 ring-inset ring-cyan-400/25"
          icon={<CheckIcon width={20} height={20} />} />
        <Stat label="Clips generated" value={stats.clips} accent="bg-emerald-500/15 text-emerald-300 ring-1 ring-inset ring-emerald-400/25"
          icon={<FilmIcon width={20} height={20} />} />
      </div>

      {/* Jobs */}
      {loading ? (
        <div className="flex items-center gap-3 text-[var(--text-dim)]">
          <span className="spinner" /> Loading jobs…
        </div>
      ) : jobs.length === 0 ? (
        <div className="glass sheen flex flex-col items-center justify-center gap-3 px-6 py-20 text-center">
          <span className="grid h-14 w-14 place-items-center rounded-2xl bg-blue-500/15 text-blue-300 ring-1 ring-inset ring-blue-400/25">
            <SparkIcon width={26} height={26} />
          </span>
          <h3 className="font-display text-lg font-semibold">No jobs yet</h3>
          <p className="max-w-sm text-sm text-[var(--text-dim)]">
            Start a new clipping job to find viral moments automatically.
          </p>
          <Link to="/new" className="btn btn-primary mt-1">Create your first job</Link>
        </div>
      ) : (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {jobs.map(job => (
            <JobCard
              key={job.id}
              job={job}
              openMenu={openMenu}
              onToggleMenu={toggleMenu}
              onDelete={handleDelete}
            />
          ))}
        </div>
      )}
    </div>
  );
}
