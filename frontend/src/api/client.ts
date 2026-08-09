import axios from 'axios';
import { Job, JobCreate, PaginatedResponse, Clip, ClipListResponse, ClipMetadataUpdate, Account, PostingPlan } from '../types';

const API_BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000/api';

const api = axios.create({
  baseURL: API_BASE_URL,
});

export const createJob = (data: JobCreate) => api.post<Job>('/jobs', data);
export const getJobs = (params?: any) => api.get<PaginatedResponse<Job>>('/jobs', { params });
export const getJob = (id: string) => api.get<Job>(`/jobs/${id}`);
export const getClips = (jobId: string, params?: any) =>
  api.get<ClipListResponse>(`/jobs/${jobId}/clips`, { params });
export const updateClipMetadata = (clipId: string, data: ClipMetadataUpdate) => api.patch<Clip>(`/clips/${clipId}/metadata`, data);
export const approveClip = (clipId: string) => api.post<Clip>(`/clips/${clipId}/approve`);
export const rejectClip = (clipId: string) => api.post<Clip>(`/clips/${clipId}/reject`);
export const approveAllClips = (jobId: string) => api.post(`/jobs/${jobId}/approve-all`);
export const rejectAllClips = (jobId: string) => api.post(`/jobs/${jobId}/reject-all`);
export const retryJob = (jobId: string) => api.post(`/jobs/${jobId}/retry`);
// Metadata is generated lazily (not during the pipeline); omit clip_ids for all clips.
export const generateMetadata = (jobId: string, clip_ids?: string[]) =>
  api.post(`/jobs/${jobId}/generate-metadata`, clip_ids ? { clip_ids } : {});
export const cancelJob = (jobId: string) => api.delete<Job>(`/jobs/${jobId}`);

export const getAccounts = () => api.get<Account[]>('/accounts');
export const connectAccount = (platform: string, display_name: string) =>
  api.post<Account>('/accounts/connect', { platform, display_name });
export const disconnectAccount = (id: string) => api.delete(`/accounts/${id}`);

// TikTok via browser login — backend spawns a Chromium window; poll for completion.
export const getTiktokConfigStatus = () =>
  api.get<{ configured: boolean; label: string }>('/accounts/tiktok/config-status');
export const startTiktokLogin = () =>
  api.post<{ state: string; session_path: string }>('/accounts/tiktok/login');
export const checkTiktokLoginStatus = (state: string) =>
  api.get<{ handle?: string; error?: string }>(`/accounts/tiktok/login-status?state=${state}`);
// TikTok by reusing the existing login from the user's real Google Chrome —
// avoids the login form entirely (which TikTok blocks in an automated browser).
export const connectTiktokExisting = () =>
  api.post<Account>('/accounts/tiktok/connect-existing');

export const getPostingPlan = (jobId: string) => api.get<PostingPlan>(`/jobs/${jobId}/posting-plan`);
export const schedulePosts = (
  jobId: string,
  account_ids: string[],
  clips: { clip_id: string; scheduled_time: string }[],
) => api.post(`/jobs/${jobId}/schedule`, { account_ids, clips });

export default api;