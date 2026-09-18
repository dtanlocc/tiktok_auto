export interface Account {
  id: string;
  email: string;
  username: string;
  status: string;         
  health_status: string;  
  profile_status: string; 
  current_step: string;
  proxy_id: string | null;
  has_cookies: boolean;
  is_paused?: boolean;   // Trang thai tam dung rieng cua account nay (khong luu DB, chi runtime)
  
  // KIỂU DỮ LIỆU PHÂN LÔ MỚI
  country: string;
  batch_tag: string;
  created_at: string;
  note?: string;   // Ghi chú tự do (user tự nhập để theo dõi)

  upload_success_count: number;
  upload_failure_count: number;
  last_upload_status: 'NEVER' | 'SUCCESS' | 'FAILED' | string;
  last_upload_at: string;
  last_upload_error: string;
  video_count: number | null;
  follower_count: number | null;
  following_count: number | null;
  likes_count: number | null;
  tiktok_user_id: string;
  tiktok_sec_uid: string;
  display_name: string;
  bio: string;
  avatar_url: string;
  verified: boolean;
  private_account: boolean;
  website_url: string;
  total_views: number | null;
  total_video_likes: number | null;
  total_comments: number | null;
  total_shares: number | null;
  collected_video_count: number;
  analytics_sync_status: 'NEVER' | 'SYNCING' | 'SUCCESS' | 'PARTIAL' | 'FAILED' | string;
  analytics_sync_source: string;
  analytics_sync_error: string;
  metrics_updated_at: string;
  is_sold: boolean;
}

export interface TikTokVideoMetric {
  video_id: string;
  title: string;
  create_time: number | null;
  view_count: number;
  like_count: number;
  comment_count: number;
  share_count: number;
  favorite_count: number | null;
  repost_count: number | null;
  download_count: number | null;
  cover_url: string;
  share_url: string;
  duration_seconds: number | null;
  max_quality: string;
  detail_source: string;
  region: string;
  shadow_ban: 'YES' | 'NO' | 'UNKNOWN' | string;
  shadow_ban_reason: string;
  index_enabled: boolean | null;
  is_reviewing: boolean;
  is_private: boolean;
  is_taken_down: boolean;
  synced_at: string;
}

export type ProxyCheckStatus = 'OK' | 'WARN' | 'FAIL' | 'UNCHECKED';

export interface Proxy {
  id: string;
  host: string;
  port: number;
  username: string | null;
  protocol: string;
  has_password?: boolean;
  label?: string;
  note?: string;
  enabled?: boolean;
  created_at?: string;
  check_status?: ProxyCheckStatus | string;
  check_error?: string;
  checked_at?: string;
  exit_ip?: string;
  country?: string;
  latency_ms?: number | null;
  tiktok_ok?: boolean | null;
  cdn_ok?: boolean | null;
  account_count?: number;
}

export interface ProxyInput {
  protocol: string;
  host: string;
  port: number;
  username?: string | null;
  password?: string | null;
  label?: string;
  note?: string;
  enabled?: boolean;
}

export interface LogMessage {
  time: string;
  username: string;
  message: string;
  level?: 'info' | 'warn' | 'error' | 'success';
}

export type ProxyModel = Proxy;

export type AppTab = 'accounts' | 'videos' | 'interactions' | 'screens' | 'proxies';
