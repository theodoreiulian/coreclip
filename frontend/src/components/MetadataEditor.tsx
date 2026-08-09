import React, { useState } from 'react';
import { Clip, ClipMetadataUpdate } from '../types';
import { updateClipMetadata } from '../api/client';
import { EditIcon, XIcon } from './Icons';

export default function MetadataEditor({ clip, onClose }: { clip: Clip, onClose: () => void }) {
  const [title, setTitle] = useState(clip.title || '');
  const [description, setDescription] = useState(clip.description || '');
  const [hashtags, setHashtags] = useState(clip.hashtags?.join(', ') || '');
  const [saving, setSaving] = useState(false);

  const handleSave = async () => {
    setSaving(true);
    try {
      await updateClipMetadata(clip.id, {
        title,
        description,
        hashtags: hashtags.split(',').map(s => s.trim()).filter(Boolean)
      });
      onClose();
    } catch (e) {
      console.error(e);
      alert("Failed to save");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="glass-2 sheen fade-in w-full max-w-lg p-6"
        onClick={e => e.stopPropagation()}
      >
        <div className="mb-5 flex items-center justify-between">
          <h3 className="flex items-center gap-2 font-display text-xl font-bold">
            <span className="grid h-8 w-8 place-items-center rounded-lg bg-blue-500/15 text-blue-300 ring-1 ring-inset ring-blue-400/25">
              <EditIcon width={16} height={16} />
            </span>
            Edit Metadata
          </h3>
          <button
            onClick={onClose}
            className="grid h-8 w-8 place-items-center rounded-lg text-[var(--text-faint)] transition hover:bg-white/10 hover:text-white"
            aria-label="Close"
          >
            <XIcon width={18} height={18} />
          </button>
        </div>

        <div className="space-y-4">
          <div>
            <label className="mb-1.5 block text-sm font-semibold">Title</label>
            <input value={title} onChange={e => setTitle(e.target.value)} className="input" />
          </div>

          <div>
            <label className="mb-1.5 block text-sm font-semibold">Description</label>
            <textarea
              value={description}
              onChange={e => setDescription(e.target.value)}
              className="input h-28 resize-none"
            />
          </div>

          <div>
            <label className="mb-1.5 block text-sm font-semibold">
              Hashtags <span className="font-normal text-[var(--text-faint)]">· comma separated</span>
            </label>
            <input value={hashtags} onChange={e => setHashtags(e.target.value)} className="input" />
          </div>
        </div>

        <div className="flex justify-end gap-2 pt-6">
          <button onClick={onClose} className="btn btn-ghost">Cancel</button>
          <button onClick={handleSave} disabled={saving} className="btn btn-primary">
            {saving ? <><span className="spinner" /> Saving…</> : 'Save'}
          </button>
        </div>
      </div>
    </div>
  );
}
