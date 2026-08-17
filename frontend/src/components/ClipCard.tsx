import React, { useState, memo } from 'react';
import { Clip } from '../types';
import ClipPreview from './ClipPreview';
import MetadataEditor from './MetadataEditor';
import { EditIcon, CheckIcon, XIcon, SparkIcon, ClockIcon } from './Icons';
import { approveClip, rejectClip } from '../api/client';

function scoreBadge(score: number): string {
  if (score >= 8) return 'badge-emerald';
  if (score >= 5) return 'badge-amber';
  return 'badge-rose';
}

function clipStatusBadge(status: string): string {
  if (status === 'approved') return 'badge-emerald';
  if (status === 'failed') return 'badge-rose';
  if (status === 'rejected') return 'badge-slate';
  return 'badge-sky';
}

const ClipCard = memo(function ClipCard({ clip, onUpdate }: { clip: Clip, onUpdate: () => void }) {
  const [editing, setEditing] = useState(false);

  const handleApprove = async () => {
    try {
      await approveClip(clip.id);
      onUpdate();
    } catch (e) { console.error(e); }
  };

  const handleReject = async () => {
    try {
      await rejectClip(clip.id);
      onUpdate();
    } catch (e) { console.error(e); }
  };

  return (
    <div className="glass sheen flex flex-col gap-4 p-4">
      <ClipPreview clip={clip} />

      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h4 className="truncate font-display text-base font-semibold text-white">
            {`Clip ${clip.clip_index}`}
          </h4>
          <p className="mt-0.5 line-clamp-2 text-sm text-[var(--text-dim)]">{clip.description}</p>
        </div>
        <div className="flex flex-none flex-col items-end gap-1">
          <span className={`badge ${scoreBadge(clip.virality_score ?? 0)}`}>
            <SparkIcon width={12} height={12} />
            {clip.virality_score}
          </span>
          <span className="flex items-center gap-1 text-[11px] text-[var(--text-faint)]">
            <ClockIcon width={11} height={11} />
            {clip.duration?.toFixed(1)}s
          </span>
        </div>
      </div>

      <div className="flex flex-wrap gap-1.5">
        {clip.hashtags?.map(h => {
          // Stored tags may or may not already include a leading '#'
          // (Gemini is inconsistent). Normalize to exactly one.
          const tag = h.replace(/^#+/, '');
          return <span key={tag} className="chip">#{tag}</span>;
        })}
      </div>

      <div className="mt-auto flex flex-wrap items-center justify-between gap-2 border-t border-white/[0.06] pt-4">
        <span className={`badge ${clipStatusBadge(clip.status)}`}>
          <span className="dot" />
          {clip.status.replace(/_/g, ' ')}
        </span>
        <div className="flex flex-wrap items-center gap-2">
          <button onClick={() => setEditing(true)} className="btn btn-ghost btn-sm">
            <EditIcon width={14} height={14} /> Edit
          </button>
          {clip.status === 'ready_for_review' && (
            <>
              <button onClick={handleApprove} className="btn btn-success btn-sm">
                <CheckIcon width={14} height={14} /> Approve
              </button>
              <button onClick={handleReject} className="btn btn-danger btn-sm">
                <XIcon width={14} height={14} /> Reject
              </button>
            </>
          )}
        </div>
      </div>

      {editing && <MetadataEditor clip={clip} onClose={() => { setEditing(false); onUpdate(); }} />}
    </div>
  );
});

export default ClipCard;
