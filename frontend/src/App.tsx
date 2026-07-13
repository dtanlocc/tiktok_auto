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
import { InteractionPanel } from './components/InteractionPanel';
import { TerminalConsole } from './components/TerminalConsole';
import { ContextMenu } from './components/ContextMenu';
import { FolderTree } from './components/FolderTree';
import { ImportModal } from './components/ImportModal'; // <-- IMPORT MODAL NỔI MỚI THÊM
import { Folder, Globe, Server, Layers } from 'lucide-react';

interface LogMessage {
  time: string;
  username: string;
  message: string;
}

export default function App() {
  const { accounts, proxies, setAccounts, setProxies } = useAppStore();
  const [activeTab, setActiveTab] = useState<'accounts' | 'proxies' | 'interactions'>('accounts');
  
  // Bộ điều khiển trung tâm (Control Panel)
  const [concurrency, setConcurrency] = useState<number>(4);
  const [avatarFolder, setAvatarFolder] = useState<string>('');

  // ĐIỀU KHIỂN TOÀN CỤC: Bắt đầu / Tạm dừng / Tiếp tục / Dừng khẩn cấp
  const [isGloballyPaused, setIsGloballyPaused] = useState<boolean>(false);

  // THU GỌN CÂY THƯ MỤC QUỐC GIA - mặc định THU GỌN để bảng tài khoản
  // hiển thị đầy đủ (giống view database) ngay khi mở trang, đỡ chiếm chỗ.
  const [isTreeCollapsed, setIsTreeCollapsed] = useState<boolean>(true);
  
  // Danh sách ID tài khoản được chọn (Checkbox Selection)
  const [selectedAccountIds, setSelectedAccountIds] = useState<string[]>([]);
  const [contextMenu, setContextMenu] = useState<{ x: number; y: number; visible: boolean } | null>(null);
  
  // Bộ lọc dữ liệu đa chiều
  const [statusFilter, setStatusFilter] = useState<string>('ALL');
  const [selectedBatchFilter, setSelectedBatchFilter] = useState<string>('ALL');
  const [selectedCountryFilter, setSelectedCountryFilter] = useState<string>('ALL');

  // TRẠNG THÁI CÂY THƯ MỤC VÀ POPUP IMPORT NỔI
  const [selectedCountry, setSelectedCountry] = useState<string | null>(null);
  const [selectedBatch, setSelectedBatch] = useState<string | null>(null);
  const [expandedCountries, setExpandedCountries] = useState<string[]>([]);
  const [isImportModalOpen, setIsImportModalOpen] = useState<boolean>(false); // <-- TRẠNG THÁI POPUP IMPORT

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
          const { id, status, current_step, health_status, profile_status } = message.data;
          useAppStore.setState((state) => ({
            accounts: state.accounts.map(acc => acc.id === id ? { 
              ...acc, 
              status, 
              current_step,
              // NÂNG CẤP ĐỒNG BỘ: Cập nhật ngay lập tức các trường sức khoẻ vĩnh viễn
              health_status: health_status || acc.health_status,
              profile_status: profile_status || acc.profile_status
            } : acc)
          }));
        } else if (message.event === 'TERMINAL_LOG') {
          const { username, message: logMsg } = message.data;
          const time = new Date().toLocaleTimeString();
          setLogs((prev) => [...prev, { time, username, message: logMsg }]);
        } else if (message.event === 'GLOBAL_STATE_CHANGED') {
          setIsGloballyPaused(!!message.data.is_globally_paused);
        } else if (message.event === 'ACCOUNT_PAUSE_CHANGED') {
          const { id, is_paused } = message.data;
          useAppStore.setState((state) => ({
            accounts: state.accounts.map(acc => acc.id === id ? { ...acc, is_paused } : acc)
          }));
        } else if (message.event === 'QUICK_CHECK_FINISHED') {
          const { completed, total } = message.data;
          setLogs((prev) => [...prev, {
            time: new Date().toLocaleTimeString(),
            username: 'System',
            message: `✅ Đã hoàn tất đợt Check nhanh Sống/Chết: ${completed}/${total} tài khoản.`
          }]);
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

    fetch('http://127.0.0.1:8001/api/v1/tasks/status')
      .then((res) => res.json())
      .then((data) => setIsGloballyPaused(!!data.is_globally_paused))
      .catch((err) => console.error('Lỗi tải trạng thái dispatcher:', err));
  };

  // Kích hoạt Custom Context Menu khi người dùng click chuột phải lên hàng tài khoản
  const handleRowContextMenu = (e: React.MouseEvent, accountId: string) => {
    e.preventDefault();

    if (!selectedAccountIds.includes(accountId)) {
      setSelectedAccountIds([accountId]);
    }

    setContextMenu({
      x: e.clientX,
      y: e.clientY,
      visible: true
    });
  };

  // Hàm gọi API xóa hàng loạt tài khoản đang được chọn
  const handleBulkDelete = async () => {
    if (selectedAccountIds.length === 0) {
      alert("Vui lòng chọn ít nhất một tài khoản.");
      return;
    }
    if (!window.confirm(`Bạn có chắc chắn muốn xóa vĩnh viễn ${selectedAccountIds.length} tài khoản đã chọn khỏi DB?`)) {
      return;
    }

    try {
      setLoading(true);
      const response = await fetch('http://127.0.0.1:8001/api/v1/accounts/bulk-delete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ account_ids: selectedAccountIds }),
      });

      if (response.ok) {
        setSelectedAccountIds([]);
        setContextMenu(null);
      }
    } catch (err) {
      console.error('Lỗi khi xóa tài khoản:', err);
    } finally {
      setLoading(false);
    }
  };

  // Hàm chọn nhanh tài khoản chưa đổi Profile (Chỉ trong phạm vi Lô đang được lọc xem)
  const handleSelectUnupdatedProfiles = () => {
    const unupdatedIds = filteredAccounts
      .filter(acc => acc.profile_status !== 'COMPLETED' && acc.health_status !== 'BANNED')
      .map(acc => acc.id);

    if (unupdatedIds.length === 0) {
      alert("Tất cả tài khoản trong Lô đang chọn đều đã cập nhật Profile.");
      return;
    }

    setSelectedAccountIds(unupdatedIds);
    alert(`Đã tích chọn nhanh ${unupdatedIds.length} tài khoản chưa cập nhật Profile.`);
  };

  // =========================================================================
  // XỬ LÝ LỌC TRANG THÁI THEO CÂY THƯ MỤC CỰC KỲ THÔNG MINH
  // =========================================================================
  const filteredAccounts = accounts.filter(acc => {
    // Khi CHƯA chọn Quốc gia/Lô cụ thể trên cây thư mục (hoặc cây đang bị thu
    // gọn), coi như KHÔNG lọc theo cây -> hiển thị TOÀN BỘ tài khoản trong DB
    // (đúng yêu cầu "view database đầy đủ"). Chỉ khi người dùng chủ động chọn
    // 1 Lô cụ thể trên cây thì mới thu hẹp phạm vi lại.
    const matchTree = (selectedCountry && selectedBatch)
      ? (acc.country === selectedCountry && acc.batch_tag === selectedBatch)
      : true;

    const matchStatus = statusFilter === 'ALL' || acc.status === statusFilter;
    return matchTree && matchStatus;
  });

  // Gom các acc bị banned trong lô đang chọn
  const handleSelectAllBanned = () => {
    const bannedIds = filteredAccounts.filter(acc => acc.health_status === 'BANNED').map(acc => acc.id);
    if (bannedIds.length === 0) {
      alert("Không tìm thấy tài khoản Banned nào trong Lô này.");
      return;
    }
    setSelectedAccountIds(bannedIds);
    alert(`Đã chọn nhanh ${bannedIds.length} acc bị Banned trong Lô.`);
  };

  // Xử lý tải File / Thư mục .txt hàng loạt (Đã tích hợp quét đệ quy thư mục)
  const handleFileUpload = async (
    event: React.ChangeEvent<HTMLInputElement>, 
    type: 'accounts' | 'proxies',
    country?: string,
    batchTag?: string
  ) => {
    const fileList = event.target.files;
    if (!fileList || fileList.length === 0) return;

    const formData = new FormData();
    let txtFileCount = 0;

    // Quét đệ quy lọc tệp tin đuôi .txt chứa tài khoản
    for (let i = 0; i < fileList.length; i++) {
      const file = fileList[i];
      if (file.name.toLowerCase().endsWith('.txt')) {
        formData.append('files', file);
        txtFileCount++;
      }
    }

    if (txtFileCount === 0) {
      alert("Không tìm thấy tệp tin định dạng .txt nào trong thư mục/lựa chọn của bạn.");
      return;
    }

    setLoading(true);

    let url = '';
    if (type === 'accounts') {
      const targetCountry = country || 'US';
      const targetBatch = batchTag ? encodeURIComponent(batchTag) : '';
      url = `http://127.0.0.1:8001/api/v1/accounts/import-file?country=${targetCountry}&batch_tag=${targetBatch}`;
    } else {
      url = 'http://127.0.0.1:8001/api/v1/proxies/import-file';
    }

    try {
      const response = await fetch(url, {
        method: 'POST',
        body: formData,
      });

      if (response.ok) {
        const result = await response.json();
        alert(result.message);
        loadData(); // Tải lại bảng để cập nhật cây thư mục
      } else {
        alert('Lỗi trong quá trình import dữ liệu.');
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
    if (selectedAccountIds.length === filteredAccounts.length) {
      setSelectedAccountIds([]);
    } else {
      setSelectedAccountIds(filteredAccounts.map(a => a.id));
    }
  };

  // Đăng nhập hàng loạt
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
      console.error(err);
    }
  };

  // Cập nhật Profile hàng loạt
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
      console.error(err);
    }
  };

  // Phân bổ Proxy tự động
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
        loadData(); 
        setContextMenu(null); 
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
      loadData();
    } catch (err) {
      console.error(err);
    }
  };

  // =========================================================================
  // ĐIỀU KHIỂN TOÀN CỤC: Bắt đầu / Tạm dừng / Tiếp tục / Dừng khẩn cấp
  // =========================================================================
  const callTaskControlApi = async (endpoint: string) => {
    try {
      const res = await fetch(`http://127.0.0.1:8001/api/v1/tasks/${endpoint}`, { method: 'POST' });
      if (!res.ok) {
        const err = await res.json();
        alert(err.detail || `Lỗi khi gọi ${endpoint}.`);
      }
    } catch (err) {
      console.error(`Lỗi khi gọi ${endpoint}:`, err);
      alert('Không thể kết nối tới backend.');
    }
  };

  const handleGlobalStart = () => callTaskControlApi('start-global');
  const handleGlobalPause = () => callTaskControlApi('pause-global');
  const handleGlobalResume = () => callTaskControlApi('resume-global');
  const handleGlobalStop = () => callTaskControlApi('stop-global');

  // ĐIỀU KHIỂN TỪNG TÀI KHOẢN: Tạm dừng / Tiếp tục riêng lẻ
  const handlePauseAccount = (accountId: string) => callTaskControlApi(`pause-account/${accountId}`);
  const handleResumeAccount = (accountId: string) => callTaskControlApi(`resume-account/${accountId}`);

  // CHECK NHANH SỐNG/CHẾT (độc lập hoàn toàn với hàng đợi Login)
  const handleQuickHealthCheck = async () => {
    if (selectedAccountIds.length === 0) {
      alert("Vui lòng tích chọn ít nhất một tài khoản trên bảng trước khi chạy.");
      return;
    }
    try {
      const response = await fetch('http://127.0.0.1:8001/api/v1/tasks/quick-health-check', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          account_ids: selectedAccountIds,
          concurrency_limit: 5
        }),
      });
      if (response.ok) {
        const result = await response.json();
        alert(result.message);
        setContextMenu(null);
      } else {
        const err = await response.json();
        alert(`Lỗi kích hoạt Check nhanh: ${err.detail}`);
      }
    } catch (err) {
      console.error(err);
    }
  };

  const handleSelectBatch = (country: string, batch: string) => {
    setSelectedCountry(country);
    setSelectedBatch(batch);
    setSelectedAccountIds([]); // Reset tích chọn khi chuyển lô
  };

  const handleToggleCountry = (country: string) => {
    setExpandedCountries((prev) =>
      prev.includes(country) ? prev.filter(c => c !== country) : [...prev, country]
    );
  };

  return (
    <div className="min-h-screen bg-[#070b15] p-6 text-slate-100 flex flex-col gap-5 select-none">
      
      {/* 1. HEADER COMPONENT */}
      <Header activeTab={activeTab} setActiveTab={setActiveTab} />

      {/* 2. CONTROL PANEL COMPONENT (Chứa nút chọn thư mục ảnh cao cấp) */}
      <ControlPanel 
        concurrency={concurrency} 
        setConcurrency={setConcurrency} 
        avatarFolder={avatarFolder} 
        setAvatarFolder={setAvatarFolder}
        isGloballyPaused={isGloballyPaused}
        onGlobalStart={handleGlobalStart}
        onGlobalPause={handleGlobalPause}
        onGlobalResume={handleGlobalResume}
        onGlobalStop={handleGlobalStop}
        accounts={accounts}
        selectedAccountIds={selectedAccountIds}
      />

      <div className="grid grid-cols-1 lg:grid-cols-4 gap-6 flex-1">
        
        {/* ===================================================================
            CÔT TRÁI (1 CỘT): SIDEBAR HOẶC CÂY THƯ MỤC TÙY TAB (SIÊU GỌN GÀNG)
            Ở tab 'accounts', cây thư mục có thể bị THU GỌN HẲN (không chiếm
            chỗ) qua nút bấm. Tab 'interactions' KHÔNG có cột trái, luôn full
            width vì không cần cây thư mục/sidebar.
            =================================================================== */}
        {(() => {
          const showLeftColumn = (activeTab === 'accounts' && !isTreeCollapsed) || activeTab === 'proxies';
          if (!showLeftColumn) return null;
          return (
            <div className="lg:col-span-1">
              {activeTab === 'accounts' ? (
                <FolderTree
                  accounts={accounts}
                  selectedCountry={selectedCountry}
                  selectedBatch={selectedBatch}
                  expandedCountries={expandedCountries}
                  onSelectBatch={handleSelectBatch}
                  onToggleCountry={handleToggleCountry}
                  onOpenImportModal={() => setIsImportModalOpen(true)} // Mở modal nổi nạp tài khoản
                  onCollapse={() => setIsTreeCollapsed(true)}
                />
              ) : (
                <Sidebar activeTab={activeTab} loading={loading} onFileUpload={handleFileUpload} />
              )}
            </div>
          );
        })()}

        {/* ===================================================================
            CỘT PHẢI: KHU VỰC LÀM VIỆC CHÍNH (MAIN WORKSPACE)
            Giãn full 4 cột khi không có cột trái, ngược lại chiếm 3 cột.
            =================================================================== */}
        <div className={
          ((activeTab === 'accounts' && !isTreeCollapsed) || activeTab === 'proxies')
            ? 'lg:col-span-3 flex flex-col gap-6'
            : 'lg:col-span-4 flex flex-col gap-6'
        }>
          
          {/* STATS SUMMARY */}
          <StatsCards accounts={accounts} proxies={proxies} />

          {activeTab === 'accounts' ? (
            <div className="flex flex-col gap-4 min-h-[450px]">
              {/* THANH ĐIỀU KHIỂN & BỘ LỌC TRẠNG THÁI - LUÔN HIỂN THỊ, KHÔNG CÒN
                  BẮT BUỘC CHỌN LÔ MỚI THẤY BẢNG (view database đầy đủ mặc định) */}
              <div className="bg-[#0e1424] p-4 rounded-xl border border-slate-800 flex flex-col gap-3">
                <div className="flex items-center justify-between border-b border-slate-800 pb-2">
                  <div className="text-xs text-slate-300 font-bold flex items-center gap-1.5 uppercase tracking-wide">
                    <Folder className="w-4 h-4 text-teal-400" />
                    {selectedCountry && selectedBatch ? (
                      <>
                        <span>Lô Đang Xem:</span>
                        <span className="text-teal-400">[{selectedCountry}]</span>
                        <span className="text-indigo-400 font-mono font-bold">{selectedBatch}</span>
                      </>
                    ) : (
                      <span className="text-slate-300">Toàn bộ tài khoản (Database đầy đủ)</span>
                    )}
                    <span className="text-[10px] text-slate-500 font-medium">({filteredAccounts.length} accounts)</span>
                  </div>
                  <div className="flex items-center gap-3">
                    {isTreeCollapsed && (
                      <button
                        onClick={() => setIsTreeCollapsed(false)}
                        className="text-[10px] font-bold text-teal-400 hover:text-teal-300 cursor-pointer"
                      >
                        📂 Hiện cây thư mục quốc gia
                      </button>
                    )}
                    {selectedCountry && selectedBatch && (
                      <button 
                        onClick={() => { setSelectedCountry(null); setSelectedBatch(null); }}
                        className="text-[10px] font-black text-rose-500 hover:text-rose-400 cursor-pointer"
                      >
                        ✕ BỎ LỌC LÔ (XEM LẠI TOÀN BỘ)
                      </button>
                    )}
                  </div>
                </div>

                <div className="flex flex-wrap gap-2 items-center justify-between">
                  <div className="flex items-center gap-2">
                    <span className="text-[10px] text-slate-400 font-bold uppercase tracking-wider">Lọc nhanh:</span>
                    <div className="flex gap-1">
                      {['ALL', 'IDLE', 'RUNNING', 'QUEUED', 'SUCCESS', 'ERROR'].map((status) => (
                        <button
                          key={status}
                          onClick={() => setStatusFilter(status)}
                          className={`px-2.5 py-1 text-[9px] font-bold rounded-md border uppercase tracking-wider transition-all ${
                            statusFilter === status 
                              ? 'bg-teal-500/20 text-teal-400 border-teal-500/40' 
                              : 'bg-slate-900 text-slate-400 border-slate-800 hover:text-slate-200'
                          }`}
                        >
                          {status} ({status === 'ALL' ? filteredAccounts.length : filteredAccounts.filter(a => a.status === status).length})
                        </button>
                      ))}
                    </div>
                  </div>

                  <div className="flex flex-wrap gap-1.5">
                    <button
                      onClick={handleSelectUnupdatedProfiles}
                      className="bg-purple-500/10 hover:bg-purple-500/20 border border-purple-500/30 text-purple-400 text-[10px] px-2.5 py-1 rounded-md font-bold transition-all"
                    >
                      ⚡ Chưa đổi Profile
                    </button>
                    <button
                      onClick={handleSelectAllBanned}
                      className="bg-rose-500/10 hover:bg-rose-500/20 border border-rose-500/30 text-rose-400 text-[10px] px-2.5 py-1 rounded-md font-bold transition-all"
                    >
                      🎯 Chọn Banned
                    </button>
                    {selectedAccountIds.length > 0 && (
                      <button
                        onClick={handleBulkDelete}
                        className="bg-rose-600 hover:bg-rose-700 text-slate-100 text-[10px] px-2.5 py-1 rounded-md font-bold transition-all"
                      >
                        🗑️ Xóa ({selectedAccountIds.length})
                      </button>
                    )}
                  </div>
                </div>
              </div>

              {/* BẢNG TÀI KHOẢN - LUÔN HIỂN THỊ (toàn bộ DB hoặc đã lọc theo Lô) */}
              <AccountsTable 
                accounts={filteredAccounts}
                proxies={proxies} 
                selectedAccountIds={selectedAccountIds}
                setSelectedAccountIds={setSelectedAccountIds}
                toggleSelectAll={toggleSelectAll}
                toggleSelectAccount={toggleSelectAccount}
                handleBindProxy={handleBindProxy}
                handleRowContextMenu={handleRowContextMenu}
                onPauseAccount={handlePauseAccount}
                onResumeAccount={handleResumeAccount}
              />
            </div>
          ) : activeTab === 'interactions' ? (
            <InteractionPanel accounts={accounts} selectedAccountIds={selectedAccountIds} />
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
          onBulkDelete={handleBulkDelete}
          onQuickHealthCheck={handleQuickHealthCheck}
        />
      )}

      {/* ===================================================================
          9. MODAL NỔI NẠP TÀI KHOẢN (POPUP DIALOG CHUẨN SAAS)
          =================================================================== */}
      <ImportModal 
        isOpen={isImportModalOpen}
        onClose={() => setIsImportModalOpen(false)}
        loading={loading}
        onFileUpload={handleFileUpload}
      />

    </div>
  );
}