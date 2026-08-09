import { useState, useEffect, useRef } from 'react';

const DEFAULT_WS_URL = 'ws://localhost:8000';

function resolveWsUrl(): string {
  if (import.meta.env.VITE_WS_URL) {
    return import.meta.env.VITE_WS_URL;
  }
  const apiUrl = import.meta.env.VITE_API_URL;
  if (apiUrl) {
    // Replace http/https with ws/wss.
    return apiUrl.replace(/^http/, 'ws').replace(/\/api$/, '');
  }
  return DEFAULT_WS_URL;
}

export function useWebSocket(jobId: string | undefined) {
  const [lastMessage, setLastMessage] = useState<any>(null);
  const [connectionStatus, setConnectionStatus] = useState<'connecting' | 'connected' | 'disconnected'>('disconnected');
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimeout = useRef<any>(null);
  const stableTimeout = useRef<any>(null);
  const backoff = useRef(1000);
  const wsUrl = useRef(resolveWsUrl()).current;

  useEffect(() => {
    if (!jobId) return;
    let unmounted = false;

    function connect() {
      if (unmounted) return;
      setConnectionStatus('connecting');
      const ws = new WebSocket(`${wsUrl}/ws/jobs/${jobId}`);
      wsRef.current = ws;

      ws.onopen = () => {
        if (unmounted) return;
        setConnectionStatus('connected');
        // Only treat the connection as healthy (and reset backoff) once it has
        // stayed open a few seconds. A socket that opens then closes immediately
        // must NOT reset the backoff, or we get a tight reconnect loop / flicker.
        clearTimeout(stableTimeout.current);
        stableTimeout.current = setTimeout(() => { backoff.current = 1000; }, 3000);
      };

      ws.onmessage = (e) => {
        try {
          const data = JSON.parse(e.data);
          setLastMessage(data);
        } catch (err) {}
      };

      ws.onclose = () => {
        if (unmounted) return;
        clearTimeout(stableTimeout.current);
        setConnectionStatus('disconnected');
        reconnectTimeout.current = setTimeout(connect, backoff.current);
        backoff.current = Math.min(backoff.current * 2, 30000);
      };

      ws.onerror = () => {
        // Errors are followed by onclose; let the reconnect logic there handle it.
      };
    }

    connect();

    return () => {
      unmounted = true;
      clearTimeout(reconnectTimeout.current);
      clearTimeout(stableTimeout.current);
      if (wsRef.current) {
        // Suppress the reconnect-on-close handler when unmounting intentionally.
        wsRef.current.onclose = null;
        wsRef.current.close();
      }
    };
  }, [jobId, wsUrl]);

  return { lastMessage, connectionStatus };
}
