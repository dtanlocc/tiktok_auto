export interface Account {
  id: string;
  username: string;
  status: string;         
  health_status: string;  
  profile_status: string; 
  current_step: string;
  proxy_id: string | null;
  has_cookies: boolean;
  
  // KIỂU DỮ LIỆU PHÂN LÔ MỚI
  country: string;
  batch_tag: string;
  created_at: string;
}

export interface ProxyModel {
  id: string;
  host: string;
  port: number;
  username: string | null;
  protocol: string;
}

export interface LogMessage {
  time: string;
  username: string;
  message: string;
  level?: 'info' | 'warn' | 'error' | 'success';
}