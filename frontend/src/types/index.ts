export interface Job {
  id: string;
  youtube_url: string;
  youtube_video_id?: string;
  video_title?: string;
  video_duration_seconds?: number;
  status: string;
  failed_stage?: string;
  target_platforms?: string[];
  gemini_model: string;
  source_video_path?: string;
  audio_path?: string;
  transcript_path?: string;
  total_clips_found: number;
  error_message?: string;
  celery_task_id?: string;
  created_at: string;
  updated_at: string;
  clip_count?: number;
}

export interface Clip {
  id: string;
  job_id: string;
  clip_index: number;
  start_time?: number;
  end_time?: number;
  duration?: number;
  virality_score?: number;
  category?: string;
  status: string;
  raw_clip_path?: string;
  vertical_clip_path?: string;
  final_clip_path?: string;
  description?: string;
  hashtags?: string[];
  platform_variants?: Record<string, any>;
  post_results?: Record<string, any>;
  scheduled_post_time?: string;
  error_message?: string;
  created_at: string;
}

export interface JobCreate {
  youtube_url: string;
  target_platforms?: string[];
}

export interface ClipMetadataUpdate {
  description?: string;
  hashtags?: string[];
  platform_variants?: Record<string, any>;
}

export interface PaginatedResponse<T> {
  items: T[];
  total: number;
  page: number;
  per_page: number;
}

export interface ClipListResponse {
  items: Clip[];
  total: number;
  page: number;
  per_page: number;
}

export interface Account {
  id: string;
  platform: string;
  display_name: string;
  external_id?: string;
  avatar_url?: string;
  connected: boolean;
  expires_at?: string;
  created_at: string;
}

export interface ScheduledPost {
  id: string;
  clip_id: string;
  account_id: string;
  platform: string;
  scheduled_time: string;
  status: string;
  result_url?: string;
  error_message?: string;
  posted_at?: string;
}

export interface PostingPlan {
  job_status: string;
  clips: Clip[];
  accounts: Account[];
  scheduled_posts: ScheduledPost[];
}

export interface AppSettings {
  default_gemini_model: string;
  gemini_api_key_set: boolean;
  gemini_api_key_last4?: string;
}

export interface SettingsUpdate {
  gemini_api_key?: string | null;
  default_gemini_model?: string;
}
