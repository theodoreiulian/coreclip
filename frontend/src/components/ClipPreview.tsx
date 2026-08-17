import React, { useEffect, useState } from 'react';
import { FilmIcon } from './Icons';
import { Clip } from '../types';

export default function ClipPreview({ clip }: { clip: Clip }) {
  const [version, setVersion] = useState<'captioned' | 'vertical' | 'raw'>('raw');
  const [error, setError] = useState(false);

  // Retry automatically whenever the clip data updates (e.g. a new pipeline
  // stage finishes and the path fields are populated).
  useEffect(() => {
    setError(false);
  }, [clip.id, clip.raw_clip_path, clip.vertical_clip_path, clip.final_clip_path, clip.status]);

  return (
    <div className="flex flex-col gap-2">
      <div className="seg self-start">
        {['raw', 'vertical', 'captioned'].map(v => (
          <button
            key={v}
            data-active={version === v}
            onClick={() => { setVersion(v as any); setError(false); }}
            className="seg-btn"
          >
            {v}
          </button>
        ))}
      </div>
      {error ? (
        <div className="flex aspect-[9/16] w-full flex-col items-center justify-center gap-2 rounded-xl border border-white/10 bg-black/40 text-sm text-[var(--text-faint)]">
          <FilmIcon width={26} height={26} className="opacity-50" />
          No {version} version available
        </div>
      ) : (
        <video
          key={`${clip.id}-${version}`}
          src={`http://localhost:8000/api/clips/${clip.id}/preview/${version}`}
          controls
          className="aspect-[9/16] w-full rounded-xl border border-white/10 bg-black shadow-[0_18px_40px_-20px_rgba(0,0,0,0.9)]"
          controlsList="nodownload"
          onError={() => setError(true)}
        />
      )}
    </div>
  );
}
