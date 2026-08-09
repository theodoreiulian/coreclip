import { useState, useEffect, useRef } from 'react';
import { getJobs, getJob, getClips } from '../api/client';
import { Job, Clip } from '../types';

const POLL_INTERVAL_MS = 10000;

export function useJobs(params?: any) {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [loading, setLoading] = useState(true);
  const visibleRef = useRef(true);

  const fetchJobs = async () => {
    try {
      const res = await getJobs(params);
      setJobs(res.data.items);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchJobs();

    const handleVisibility = () => {
      visibleRef.current = document.visibilityState === 'visible';
    };
    document.addEventListener('visibilitychange', handleVisibility);

    const interval = setInterval(() => {
      if (visibleRef.current) {
        fetchJobs();
      }
    }, POLL_INTERVAL_MS);

    return () => {
      clearInterval(interval);
      document.removeEventListener('visibilitychange', handleVisibility);
    };
  }, []);

  return { jobs, loading, refetch: fetchJobs };
}

export function useJob(id: string) {
  const [job, setJob] = useState<Job | null>(null);
  const [clips, setClips] = useState<Clip[]>([]);
  const [loading, setLoading] = useState(true);

  const fetchAll = async () => {
    if (!id) return;
    try {
      const [jRes, cRes] = await Promise.all([getJob(id), getClips(id)]);
      setJob(jRes.data);
      setClips(cRes.data.items);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchAll();
  }, [id]);

  return { job, clips, loading, refetch: fetchAll };
}
