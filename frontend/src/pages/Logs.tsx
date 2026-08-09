import React, { useState } from 'react';
import LogViewer from '../components/LogViewer';
import { TerminalIcon } from '../components/Icons';

export default function Logs() {
  const [source, setSource] = useState('api');
  const [jobId, setJobId] = useState('');

  return (
    <div className="fade-in space-y-6">
      <div className="flex items-center gap-4">
        <span className="grid h-12 w-12 place-items-center rounded-2xl bg-blue-500/15 text-blue-300 ring-1 ring-inset ring-blue-400/25">
          <TerminalIcon width={22} height={22} />
        </span>
        <div>
          <h2 className="font-display text-3xl font-bold tracking-tight">Logs</h2>
          <p className="mt-1 text-sm text-[var(--text-dim)]">
            Live tail of the API and per-job pipeline activity.
          </p>
        </div>
      </div>

      <div className="glass sheen p-5">
        <div className="mb-4 flex flex-wrap items-center gap-3">
          <select
            value={source}
            onChange={e => setSource(e.target.value)}
            className="input input-sm w-auto"
          >
            <option value="api">API Logs</option>
            <option value="job">Specific Job Logs</option>
          </select>
          {source === 'job' && (
            <input
              type="text"
              placeholder="Enter Job ID"
              value={jobId}
              onChange={e => setJobId(e.target.value)}
              className="input input-sm w-72"
            />
          )}
        </div>

        <LogViewer jobId={source === 'job' && jobId ? jobId : undefined} />
      </div>
    </div>
  );
}
