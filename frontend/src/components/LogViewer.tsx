import React, { useEffect, useState, useRef } from 'react';
import axios from 'axios';

const ACTIVE_POLL_MS = 3000;
const IDLE_POLL_MS = 10000;
const API_BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000/api';

export default function LogViewer({
  jobId,
  stage,
  emptyMessage = 'No logs yet.',
}: {
  jobId?: string;
  stage?: string;
  emptyMessage?: string;
}) {
  const [logs, setLogs] = useState<any[]>([]);
  const scrollRef = useRef<HTMLDivElement>(null);
  const lastLenRef = useRef(0);
  const idleTicksRef = useRef(0);

  useEffect(() => {
    let cancelled = false;
    let int: any;

    const fetchLogs = async () => {
      try {
        const url = jobId ? `${API_BASE_URL}/logs/jobs/${jobId}` : `${API_BASE_URL}/logs/api`;
        const params: any = { tail: 500 };
        if (stage) params.stage = stage;
        const res = await axios.get(url, { params });
        if (cancelled) return;
        const newLogs = res.data.logs || [];

        if (newLogs.length !== lastLenRef.current) {
          lastLenRef.current = newLogs.length;
          idleTicksRef.current = 0;
        } else {
          idleTicksRef.current += 1;
        }

        setLogs(newLogs);
        if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
      } catch (e) {
        // 404 = no log file yet (job hasn't logged anything); treat as empty.
        if (!cancelled) setLogs([]);
      }
    };

    const schedule = () => {
      // Poll faster while logs are growing; throttle down when stable.
      const intervalMs = idleTicksRef.current >= 2 ? IDLE_POLL_MS : ACTIVE_POLL_MS;
      int = setInterval(() => {
        fetchLogs();
        clearInterval(int);
        if (!cancelled) schedule();
      }, intervalMs);
    };

    fetchLogs();
    schedule();

    return () => {
      cancelled = true;
      clearInterval(int);
    };
  }, [jobId, stage]);

  const color = (level: string) =>
    level === 'ERROR' ? 'text-rose-400' : level === 'WARNING' ? 'text-amber-300' : 'text-emerald-300';

  return (
    <div className="overflow-hidden rounded-xl border border-white/10 bg-[#06060b]/90 shadow-[0_18px_50px_-24px_rgba(0,0,0,0.9)]">
      {/* terminal chrome */}
      <div className="flex items-center gap-2 border-b border-white/[0.06] bg-white/[0.02] px-4 py-2.5">
        <span className="h-2.5 w-2.5 rounded-full bg-rose-500/80" />
        <span className="h-2.5 w-2.5 rounded-full bg-amber-400/80" />
        <span className="h-2.5 w-2.5 rounded-full bg-emerald-500/80" />
        <span className="ml-2 font-mono text-[11px] tracking-wide text-[var(--text-faint)]">
          {stage ? `${stage}.log` : jobId ? 'job.log' : 'api.log'}
        </span>
      </div>
      <div className="h-96 overflow-y-auto p-4 font-mono text-xs leading-relaxed" ref={scrollRef}>
        {logs.length === 0 && <div className="text-[var(--text-faint)]">{emptyMessage}</div>}
        {logs.map((l, i) => (
          <div key={i} className="mb-1 flex flex-wrap items-baseline gap-x-2">
            <span className="text-[var(--text-faint)]">[{l.timestamp}]</span>
            <span className={`font-semibold ${color(l.level)}`}>[{l.level}]</span>
            <span className="text-blue-300">[{l.stage || 'sys'}]</span>
            <span className="text-[var(--text-dim)]">{l.message}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
