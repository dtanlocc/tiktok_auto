import React, { useEffect, useState, useRef } from 'react';
import { useAppStore } from './store/useAppStore';
import { initWebSocket } from './services/websocket';
import { Account, Proxy } from './store/useAppStore';

// Nhập khẩu các thành phần đã mô-đun hóa
import { Header } from './components/Header';
import { ControlPanel } from './components/ControlPanel';
import { Sidebar } from './components/Sidebar';
import { StatsCards } from './components/StatsCards';
import { AccountsTable } from './components/AccountsTable';
import { ProxiesTable } from './components/ProxiesTable';
import { TerminalConsole } from './components/TerminalConsole';
import { ContextMenu } from './components/ContextMenu';

interface LogMessage {
  time: string;
  username: string;
  message: string;
}

export default function App() {
  const { accounts, proxies, wsConnected, setAccounts, setProxies } = useAppStore();
  const [activeTab, setActiveTab] = useState<'accounts' | 'proxies'>('accounts');
  
  // Bộ điều khiển trung tâm (Control Panel)
  const [concurrency, setConcurrency] = useState<number>(4);
  const [avatarFolder, setAvatarFolder] = useState<string>('');
  
  // Danh sách ID tài khoản được chọn (Checkbox Selection)
  const [selectedAccountIds, setSelectedAccountIds] = useState<string[]>([]);
  
  // Trạng thái menu chuột phải (Context Menu)
  const [contextMenu, setContextMenu] = useState<{ x: number; y: number; visible: boolean } | null>(null);

  const [logs, setLogs] = useState<LogMessage[]>([]);
  const [loading, setLoading] = useState<boolean>(false);
  const terminalEndRef = useRef<HTMLDivElement>(null);

  // 1. Khởi động WebSockets, tải dữ liệu ban đầu và lắng nghe sự kiện đóng menu chuột phải
  useEffect(() => {
    initWebSocket();

    const handleWsEvents = (event: MessageEvent) => {
      try {
        const message = JSON.parse(event.data);
        if (message.event === 'TASK_STEP_UPDATED') {
          const { id, current_step } = message.data;
          useAppStore.setState((state) => ({
            accounts: state.accounts.map(acc => acc.id === id ? { ...acc, current_step } : acc)
          }));
        } else if (message.event === 'ACCOUNT_STATUS_CHANGED') {
          const { id, status, current_step } = message.data;
          useAppStore.setState((state) => ({
            accounts: state.accounts.map(acc => acc.id === id ? { ...acc, status, current_step } : acc)
          }));
        } else if (message.event === 'TERMINAL_LOG') {
          const { username, message: logMsg } = message.data;
          const time = new Date().toLocaleTimeString();
          setLogs((prev) => [...prev, { time, username, message: logMsg }]);
        }
      } catch (err) {
        console.error(err);
      }
    };

    const activeWs = new WebSocket('ws://127.0.0.1:8001/ws');
    activeWs.onmessage = handleWsEvents;

    // Tải dữ liệu ban đầu
    loadData();

    // Đóng Menu chuột phải tự động khi click chuột trái ra ngoài màn hình
    const closeMenu = () => setContextMenu(null);
    document.addEventListener('click', closeMenu);

    return () => {
      activeWs.close();
      document.removeEventListener('click', closeMenu);
    };
  }, [setAccounts, setProxies]);

  // Cuộn Terminal xuống cuối khi nhận log mới
  useEffect(() => {
    terminalEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [logs]);

  // Hàm tải dữ liệu từ API Backend
  const loadData = () => {
    fetch('http://127.0.0.1:8001/api/v1/accounts/')
      .then((res) => res.json())
      .then((data) => setAccounts(data))
      .catch((err) => console.error('Lỗi tải danh sách tài khoản:', err));

    fetch('http://127.0.0.1:8001/api/v1/proxies/')
      .then((res) => res.json())
      .then((data) => setProxies(data))
      .catch((err) => console.error('Lỗi tải danh sách proxy:', err));
  };

  // Kích hoạt Custom Context Menu khi người dùng click chuột phải lên hàng tài khoản
  const handleRowContextMenu = (e: React.MouseEvent, accountId: string) => {
    e.preventDefault(); // Chặn menu gốc của trình duyệt

    if (!selectedAccountIds.includes(accountId)) {
      setSelectedAccountIds([accountId]);
    }

    setContextMenu({
      x: e.clientX,
      y: e.clientY,
      visible: true
    });
  };

  // Xử lý tải File .txt hàng loạt tài khoản hoặc proxy lên Server
  const handleFileUpload = async (event: React.ChangeEvent<HTMLInputElement>, type: 'accounts' | 'proxies') => {
    const fileList = event.target.files;
    if (!fileList || fileList.length === 0) return;

    const formData = new FormData();
    for (let i = 0; i < fileList.length; i++) {
      formData.append('files', fileList[i]);
    }
    setLoading(true);

    const url = type === 'accounts' 
      ? 'http://127.0.0.1:8001/api/v1/accounts/import-file' 
      : 'http://127.0.0.1:8001/api/v1/proxies/import-file';

    try {
      const response = await fetch(url, {
        method: 'POST',
        body: formData,
      });

      if (response.ok) {
        const result = await response.json();
        alert(result.message);
        loadData();
      } else {
        alert('Lỗi trong quá trình import tệp tin.');
      }
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  const toggleSelectAccount = (id: string) => {
    setSelectedAccountIds((prev) => 
      prev.includes(id) ? prev.filter(item => item !== id) : [...prev, id]
    );
  };

  const toggleSelectAll = () => {
    if (selectedAccountIds.length === accounts.length) {
      setSelectedAccountIds([]);
    } else {
      setSelectedAccountIds(accounts.map(a => a.id));
    }
  };

  // CHUYÊN XỬ LÝ ĐĂNG NHẬP HÀNG LOẠT (COOKIE HOẶC FORM OTP)
  const handleBulkLogin = async (method: 'COOKIE' | 'CREDENTIAL') => {
    if (selectedAccountIds.length === 0) {
      alert("Vui lòng tích chọn ít nhất một tài khoản trên bảng trước khi chạy.");
      return;
    }

    try {
      const response = await fetch('http://127.0.0.1:8001/api/v1/tasks/bulk-login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          account_ids: selectedAccountIds,
          login_method: method,
          concurrency_limit: typeof concurrency === 'string' ? 4 : concurrency
        }),
      });

      if (response.ok) {
        setSelectedAccountIds([]); // Reset tích chọn
        setContextMenu(null); // Đóng menu chuột phải
      } else {
        const err = await response.json();
        alert(`Lỗi kích hoạt đăng nhập: ${err.detail}`);
      }
    } catch (err) {
      console.error('Lỗi gọi API bulk-login:', err);
    }
  };

  // CHUYÊN XỬ LÝ CẬP NHẬT HỒ SƠ HÀNG LOẠT (AVATAR & BIO)
  const handleBulkUpdateProfile = async () => {
    if (selectedAccountIds.length === 0) {
      alert("Vui lòng tích chọn ít nhất một tài khoản trên bảng trước khi chạy.");
      return;
    }

    try {
      const response = await fetch('http://127.0.0.1:8001/api/v1/tasks/bulk-update-profile', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          account_ids: selectedAccountIds,
          avatar_folder: avatarFolder || null,
          concurrency_limit: typeof concurrency === 'string' ? 4 : concurrency
        }),
      });

      if (response.ok) {
        setSelectedAccountIds([]);
        setContextMenu(null);
      } else {
        const err = await response.json();
        alert(`Lỗi kích hoạt đổi profile: ${err.detail}`);
      }
    } catch (err) {
      console.error('Lỗi gọi API bulk-update-profile:', err);
    }
  };

  // Kích hoạt TỰ ĐỘNG PHÂN BỔ PROXY từ Menu chuột phải
  const handleAutoAllocateProxies = async () => {
    if (selectedAccountIds.length === 0) return;
    setLoading(true);

    try {
      const response = await fetch('http://127.0.0.1:8001/api/v1/accounts/auto-allocate-proxies', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ account_ids: selectedAccountIds }),
      });

      if (response.ok) {
        const result = await response.json();
        alert(result.message);
        loadData(); // Tải lại bảng để đồng bộ IP Proxy mới gán
        setContextMenu(null); // Đóng menu chuột phải
      } else {
        const err = await response.json();
        alert(`Lỗi phân bổ: ${err.detail}`);
      }
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  // Gán Proxy thủ công qua Dropdown
  const handleBindProxy = async (accountId: string, proxyId: string) => {
    try {
      const targetProxyId = proxyId === 'none' ? null : proxyId;
      await fetch(`http://127.0.0.1:8001/api/v1/accounts/${accountId}/proxy`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ proxy_id: targetProxyId }),
      });
    } catch (err) {
      console.error(err);
    }
  };

  return (
    <div className="min-h-screen bg-[#070b15] p-6 text-slate-100 flex flex-col gap-5 select-none">
      
      {/* 1. HEADER COMPONENT */}
      <Header activeTab={activeTab} setActiveTab={setActiveTab} />

      {/* 2. CONTROL PANEL COMPONENT */}
      <ControlPanel 
        concurrency={concurrency} 
        setConcurrency={setConcurrency} 
        avatarFolder={avatarFolder} 
        setAvatarFolder={setAvatarFolder} 
      />

      <div className="grid grid-cols-1 lg:grid-cols-4 gap-6 flex-1">
        
        {/* 3. SIDEBAR COMPONENT */}
        <Sidebar activeTab={activeTab} loading={loading} onFileUpload={handleFileUpload} />

        {/* WORKSPACE AREA */}
        <div className="lg:col-span-3 flex flex-col gap-6">
          
          {/* 4. STATS CARDS COMPONENT */}
          <StatsCards accounts={accounts} proxies={proxies} />

          {/* 5 & 6. DATA TABLES (TÀI KHOẢN HOẶC PROXY THEO TỰ TAB) */}
          {activeTab === 'accounts' ? (
            <AccountsTable 
              accounts={accounts} 
              proxies={proxies} 
              selectedAccountIds={selectedAccountIds}
              toggleSelectAll={toggleSelectAll}
              toggleSelectAccount={toggleSelectAccount}
              handleBindProxy={handleBindProxy}
              handleRowContextMenu={handleRowContextMenu}
            />
          ) : (
            <ProxiesTable proxies={proxies} />
          )}

        </div>
      </div>

      {/* 7. TERMINAL CONSOLE COMPONENT */}
      <TerminalConsole logs={logs} setLogs={setLogs} terminalEndRef={terminalEndRef} />

      {/* 8. CUSTOM CONTEXT MENU COMPONENT */}
      {contextMenu && contextMenu.visible && activeTab === 'accounts' && (
        <ContextMenu 
          x={contextMenu.x} 
          y={contextMenu.y} 
          selectedCount={selectedAccountIds.length}
          onBulkLogin={handleBulkLogin}
          onBulkUpdateProfile={handleBulkUpdateProfile}
          onAutoAllocateProxies={handleAutoAllocateProxies}
        />
      )}

    </div>
  );
}