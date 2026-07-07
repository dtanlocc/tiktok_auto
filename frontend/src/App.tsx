import React, { useEffect, useState, useRef } from 'react';
import { useAppStore, Account, Proxy } from './store/useAppStore';
import { initWebSocket } from './services/websocket';
import { 
  Play, 
  Users, 
  CloudLightning, 
  Plus, 
  ShieldAlert, 
  MonitorPlay, 
  FileInput, 
  Terminal as TerminalIcon, 
  Globe, 
  CheckSquare, 
  Square, 
  RefreshCw, 
  Key, 
  Video, 
  Trash2, 
  Wifi, 
  WifiOff, 
  ShieldCheck, 
  Sparkles
} from 'lucide-react';

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

  // 2. Kích hoạt Custom Context Menu khi người dùng click chuột phải lên hàng tài khoản
  const handleRowContextMenu = (e: React.MouseEvent, accountId: string) => {
    e.preventDefault(); // Chặn menu gốc của trình duyệt

    // Nếu dòng được click chưa nằm trong danh sách được tích chọn, tự động chuyển sang tích chọn duy nhất dòng này
    if (!selectedAccountIds.includes(accountId)) {
      setSelectedAccountIds([accountId]);
    }

    // Mở menu tại đúng tọa độ con trỏ chuột của người dùng
    setContextMenu({
      x: e.clientX,
      y: e.clientY,
      visible: true
    });
  };

  // 3. Xử lý tải File .txt hàng loạt tài khoản hoặc proxy lên Server
  const handleFileUpload = async (event: React.ChangeEvent<HTMLInputElement>, type: 'accounts' | 'proxies') => {
    const fileList = event.target.files;
    if (!fileList || fileList.length === 0) return;

    const formData = new FormData();
    // Đóng gói mảng files tải lên dưới cùng 1 trường 'files' gửi lên FastAPI
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

  // 4. Các nút tích chọn thủ công từng hàng và tích chọn tất cả
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

  // 5. Kích hoạt luồng chạy ĐỒNG BỘ ĐỔI AVATAR & BIO HÀNG LOẠT (Gọi API bulk-start)
  // Tìm kiếm và thay thế hàm handleBulkStart cũ bằng 2 hàm xử lý độc lập mới dưới đây:
  
  // HÀM 1: CHUYÊN XỬ LÝ ĐĂNG NHẬP HÀNG LOẠT (COOKIE HOẶC FORM OTP)
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

  // HÀM 2: CHUYÊN XỬ LÝ CẬP NHẬT HỒ SƠ HÀNG LOẠT (AVATAR & BIO)
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

  // 6. Kích hoạt TỰ ĐỘNG PHÂN BỔ PROXY tối ưu tải trọng từ Menu chuột phải
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

  const stats = {
    total: accounts.length,
    running: accounts.filter(a => a.status === 'RUNNING').length,
    proxies: proxies.length,
    loggedIn: accounts.filter(a => a.status === 'LOGGED_IN').length,
  };

  return (
    <div className="min-h-screen bg-[#070b15] p-6 text-slate-100 flex flex-col gap-5 select-none">
      
      {/* HEADER BAR */}
      <div className="flex justify-between items-center border-b border-slate-800 pb-4">
        <div>
          <h1 className="text-2xl font-bold bg-gradient-to-r from-teal-400 to-blue-500 bg-clip-text text-transparent">
            TikTok Professional Multi-Thread Console
          </h1>
          <p className="text-slate-400 text-sm">Bộ quản trị đa luồng tàng hình kết nối chuột phải và phân bổ IP thông minh</p>
        </div>
        
        {/* Tab Selection */}
        <div className="flex bg-slate-900 border border-slate-800 p-1.5 rounded-xl gap-1">
          <button
            onClick={() => setActiveTab('accounts')}
            className={`px-4 py-2 text-xs font-bold rounded-lg transition-all flex items-center gap-1.5 ${activeTab === 'accounts' ? 'bg-teal-500 text-slate-950' : 'text-slate-400 hover:text-slate-100'}`}
          >
            <Users className="w-3.5 h-3.5" /> Quản lý Tài Khoản
          </button>
          <button
            onClick={() => setActiveTab('proxies')}
            className={`px-4 py-2 text-xs font-bold rounded-lg transition-all flex items-center gap-1.5 ${activeTab === 'proxies' ? 'bg-teal-500 text-slate-950' : 'text-slate-400 hover:text-slate-100'}`}
          >
            <Globe className="w-3.5 h-3.5" /> Quản lý Proxies
          </button>
        </div>
      </div>

      {/* CONTROL PANEL TRUNG TÂM (Đồng bộ số luồng và thư mục) */}
      <div className="bg-[#0e1424] p-4 rounded-2xl border border-slate-800 grid grid-cols-1 md:grid-cols-3 gap-4 items-center">
        <div>
          <label className="text-xs text-slate-400 block mb-1 font-semibold">Cấu hình số luồng chạy song song (Threads):</label>
          <input
            type="number"
            min={1}
            max={20}
            value={concurrency}
            onChange={(e) => setConcurrency(parseInt(e.target.value) || 4)}
            className="w-full bg-[#182032] border border-slate-700 rounded-xl p-2.5 text-sm focus:outline-none focus:ring-1 focus:ring-teal-400 font-bold text-teal-400 text-center"
          />
        </div>
        <div className="md:col-span-2">
          <label className="text-xs text-slate-400 block mb-1 font-semibold">Đường dẫn thư mục chứa ảnh đại diện (Avatar Folder):</label>
          <input
            type="text"
            placeholder="Ví dụ: /home/dtanlocc/Downloads/avatars"
            value={avatarFolder}
            onChange={(e) => setAvatarFolder(e.target.value)}
            className="w-full bg-[#182032] border border-slate-700 rounded-xl p-2.5 text-sm focus:outline-none focus:ring-1 focus:ring-teal-400 text-slate-100"
          />
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-4 gap-6 flex-1">
        
        {/* PANEL TRÁI: KHU VỰC CHỌN VÀ IMPORT FILE .TXT HÀNG LOẠT */}
        <div className="bg-[#0e1424] p-5 rounded-2xl border border-slate-800 h-fit flex flex-col gap-4">
          <div className="flex items-center gap-2">
            <FileInput className="text-teal-400 w-5 h-5" />
            <h2 className="font-bold text-slate-200">Nhập dữ liệu (.txt)</h2>
          </div>
          
          {activeTab === 'accounts' ? (
            <div className="space-y-4">
              <p className="text-xs text-slate-400 leading-relaxed">
                Nhấp nút dưới để chọn tải lên hàng loạt tệp tài khoản `.txt` cùng lúc (cho phép giữ Ctrl để chọn nhiều tệp).
              </p>
              <label className="flex flex-col items-center justify-center border-2 border-dashed border-slate-700 rounded-xl p-6 cursor-pointer hover:border-teal-400 transition-colors bg-[#182032]/50">
                <Plus className="text-slate-400 w-6 h-6 mb-2" />
                <span className="text-xs font-semibold text-slate-300">Chọn các file accounts.txt</span>
                <input
                  type="file"
                  accept=".txt"
                  multiple={true} // Cho phép chọn nhiều file cùng lúc
                  disabled={loading}
                  onChange={(e) => handleFileUpload(e, 'accounts')}
                  className="hidden"
                />
              </label>
            </div>
          ) : (
            <div className="space-y-4">
              <p className="text-xs text-slate-400 leading-relaxed">
                Nhấp nút dưới để chọn tải lên hàng loạt tệp Proxy `.txt` cùng lúc.
              </p>
              <label className="flex flex-col items-center justify-center border-2 border-dashed border-slate-700 rounded-xl p-6 cursor-pointer hover:border-teal-400 transition-colors bg-[#182032]/50">
                <Plus className="text-slate-400 w-6 h-6 mb-2" />
                <span className="text-xs font-semibold text-slate-300">Chọn các file proxies.txt</span>
                <input
                  type="file"
                  accept=".txt"
                  multiple={true} // Cho phép chọn nhiều file cùng lúc
                  disabled={loading}
                  onChange={(e) => handleFileUpload(e, 'proxies')}
                  className="hidden"
                />
              </label>
            </div>
          )}
        </div>

        {/* PANEL PHẢI: BẢNG TRÌNH DIỄN DỮ LIỆU */}
        <div className="lg:col-span-3 flex flex-col gap-6">
          
          {/* Card Thống kê */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <div className="bg-[#0e1424] p-4 rounded-xl border border-slate-800 flex items-center justify-between">
              <div><p className="text-xs text-slate-400 font-semibold">Tài khoản</p><p className="text-2xl font-bold mt-1">{stats.total}</p></div>
              <Users className="text-blue-400 w-7 h-7 opacity-75" />
            </div>
            <div className="bg-[#0e1424] p-4 rounded-xl border border-slate-800 flex items-center justify-between">
              <div><p className="text-xs text-slate-400 font-semibold">Đang chạy</p><p className="text-2xl font-bold mt-1 text-amber-400">{stats.running}</p></div>
              <CloudLightning className="text-amber-400 w-7 h-7 opacity-75" />
            </div>
            <div className="bg-[#0e1424] p-4 rounded-xl border border-slate-800 flex items-center justify-between">
              <div><p className="text-xs text-slate-400 font-semibold">Proxy Hệ thống</p><p className="text-2xl font-bold mt-1 text-teal-400">{stats.proxies}</p></div>
              <Globe className="text-teal-400 w-7 h-7 opacity-75" />
            </div>
            <div className="bg-[#0e1424] p-4 rounded-xl border border-slate-800 flex items-center justify-between">
              <div><p className="text-xs text-slate-400 font-semibold">Đã đăng nhập</p><p className="text-2xl font-bold mt-1 text-emerald-400">{stats.loggedIn}</p></div>
              <ShieldAlert className="text-emerald-400 w-7 h-7 opacity-75" />
            </div>
          </div>

          {/* VIEW CHÍNH: TÀI KHOẢN HOẶC PROXY */}
          <div className="bg-[#0e1424] rounded-2xl border border-slate-800 overflow-hidden flex-1 flex flex-col">
            {activeTab === 'accounts' ? (
              <>
                <div className="p-4 border-b border-slate-800 flex justify-between items-center bg-[#141b2e]/50">
                  <div className="flex items-center gap-3">
                    <button
                      onClick={toggleSelectAll}
                      className="text-xs font-bold text-teal-400 hover:text-teal-300 flex items-center gap-1.5"
                    >
                      {selectedAccountIds.length === accounts.length && accounts.length > 0 ? (
                        <CheckSquare className="w-4 h-4" />
                      ) : (
                        <Square className="w-4 h-4" />
                      )}
                      <span>Tích chọn tất cả ({selectedAccountIds.length})</span>
                    </button>
                  </div>
                  
                  {/* Hướng dẫn thao tác Chuột phải trực quan cho người dùng */}
                  <div className="text-xs text-slate-400 italic">
                    💡 Click <span className="text-teal-400 font-bold">Chuột phải</span> lên hàng tài khoản đã chọn để mở Menu nâng cao
                  </div>
                </div>

                <div className="overflow-y-auto max-h-[380px] flex-1">
                  <table className="w-full text-left border-collapse">
                    <thead>
                      <tr className="bg-[#141b2e] text-xs font-semibold text-slate-400 uppercase">
                        <th className="p-4 w-12 text-center">Tích</th>
                        <th className="p-4">Tài khoản</th>
                        <th className="p-4">Liên kết IP Proxy</th>
                        <th className="p-4">Trạng thái</th>
                        <th className="p-4">Tiến trình chạy</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-800 text-xs">
                      {accounts.length === 0 ? (
                        <tr>
                          <td colSpan={5} className="p-8 text-center text-slate-500 font-semibold">
                            Chưa có tài khoản nào. Vui lòng tải các file .txt lên để nhập hàng loạt.
                          </td>
                        </tr>
                      ) : (
                        accounts.map((acc: Account) => (
                          <tr 
                            key={acc.id} 
                            onContextMenu={(e) => handleRowContextMenu(e, acc.id)} // Lắng nghe sự kiện click chuột phải
                            className={`hover:bg-slate-900/40 cursor-context-menu transition-colors ${selectedAccountIds.includes(acc.id) ? 'bg-slate-900/20' : ''}`}
                          >
                            <td className="p-4 text-center">
                              <button onClick={() => toggleSelectAccount(acc.id)} className="text-slate-400 hover:text-teal-400">
                                {selectedAccountIds.includes(acc.id) ? (
                                  <CheckSquare className="w-4 h-4 text-teal-400" />
                                ) : (
                                  <Square className="w-4 h-4" />
                                )}
                              </button>
                            </td>
                            <td className="p-4">
                              <div className="font-bold text-slate-200">{acc.username}</div>
                              <div className="text-[10px] text-slate-500 font-mono mt-0.5">{acc.id}</div>
                            </td>
                            <td className="p-4">
                              <select
                                value={acc.proxy_id || 'none'}
                                onChange={(e) => handleBindProxy(acc.id, e.target.value)}
                                className="bg-[#182032] border border-slate-700 rounded-lg p-1.5 text-xs text-teal-400 font-medium focus:outline-none focus:ring-1 focus:ring-teal-400"
                              >
                                <option value="none">Mạng LAN (Không Proxy)</option>
                                {proxies.map((p: Proxy) => (
                                  <option key={p.id} value={p.id}>
                                    [{p.protocol.toUpperCase()}] {p.host}:{p.port}
                                  </option>
                                ))}
                              </select>
                            </td>
                            <td className="p-4">
                              <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[10px] font-bold border ${
                                acc.status === 'RUNNING' ? 'bg-amber-500/10 text-amber-400 border-amber-500/30' :
                                acc.status === 'QUEUED' ? 'bg-teal-500/10 text-teal-400 border-teal-500/30' :
                                acc.status === 'LOGGED_IN' ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30' :
                                acc.status === 'ERROR' ? 'bg-rose-500/10 text-rose-400 border-rose-500/30' :
                                'bg-slate-500/10 text-slate-400 border-slate-500/30'
                              }`}>
                                {acc.status}
                              </span>
                            </td>
                            <td className="p-4 font-mono font-bold text-slate-300">
                              {acc.status === 'RUNNING' ? (
                                <span className="flex items-center gap-1 text-amber-400">
                                  ⏳ {acc.current_step}
                                </span>
                              ) : acc.status === 'QUEUED' ? (
                                <span className="text-teal-400">⏳ {acc.current_step}</span>
                              ) : (
                                <span>{acc.current_step}</span>
                              )}
                            </td>
                          </tr>
                        ))
                      )}
                    </tbody>
                  </table>
                </div>
              </>
            ) : (
              <>
                <div className="p-4 border-b border-slate-800 bg-[#141b2e]/50">
                  <h2 className="font-bold text-slate-200">Kho lưu trữ IP Proxy</h2>
                </div>
                <div className="overflow-y-auto max-h-[420px] flex-1">
                  <table className="w-full text-left border-collapse">
                    <thead>
                      <tr className="bg-[#141b2e] text-xs font-semibold text-slate-400 uppercase">
                        <th className="p-4">Giao thức</th>
                        <th className="p-4">Địa chỉ IP / Host</th>
                        <th className="p-4">Cổng (Port)</th>
                        <th className="p-4">Tài khoản xác thực</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-800 text-xs text-slate-300">
                      {proxies.length === 0 ? (
                        <tr>
                          <td colSpan={4} className="p-8 text-center text-slate-500 font-semibold">
                            Chưa có proxy nào. Vui lòng tải lên file proxies.txt để nhập hàng loạt.
                          </td>
                        </tr>
                      ) : (
                        proxies.map((p) => (
                          <tr key={p.id} className="hover:bg-slate-900/40 font-mono">
                            <td className="p-4"><span className="bg-teal-500/10 text-teal-400 border border-teal-500/30 px-2 py-0.5 rounded font-bold text-[10px]">{p.protocol.toUpperCase()}</span></td>
                            <td className="p-4 text-slate-100 font-bold">{p.host}</td>
                            <td className="p-4 text-slate-300">{p.port}</td>
                            <td className="p-4 text-slate-500">{p.username ? p.username : 'Không có'}</td>
                          </tr>
                        ))
                      )}
                    </tbody>
                  </table>
                </div>
              </>
            )}
          </div>
        </div>
      </div>

      {/* TERMINAL CONSOLE REALTIME */}
      <div className="bg-[#050811] border border-slate-800 rounded-2xl overflow-hidden flex flex-col h-[180px]">
        <div className="bg-[#0e1424] px-4 py-2 border-b border-slate-800 flex justify-between items-center">
          <div className="flex items-center gap-2 text-xs font-semibold text-slate-300">
            <TerminalIcon className="text-teal-400 w-4 h-4" />
            <span>Nhật ký luồng hệ thống (Live Terminal Console)</span>
          </div>
          <button onClick={() => setLogs([])} className="text-[10px] text-slate-500 hover:text-slate-300 font-bold">Xóa nhật ký</button>
        </div>
        <div className="p-4 font-mono text-xs overflow-y-auto flex-1 space-y-1 text-slate-400 bg-black/40">
          {logs.length === 0 ? <div className="text-slate-600 italic">Chờ khởi động tác vụ để ghi nhận log...</div> : logs.map((log, index) => (
            <div key={index} className="flex gap-2">
              <span className="text-slate-600">[{log.time}]</span>
              <span className="text-teal-400">[{log.username}]</span>
              <span className="text-slate-200">{log.message}</span>
            </div>
          ))}
          <div ref={terminalEndRef} />
        </div>
      </div>

      {/* ==================== CUSTOM CONTEXT MENU (MENU CHUỘT PHẢI) ==================== */}
      {contextMenu && contextMenu.visible && activeTab === 'accounts' && (
        <div 
          style={{ top: contextMenu.y, left: contextMenu.x }}
          className="fixed bg-[#040814]/90 border border-cyan-500/20 shadow-[0_10px_40px_rgba(0,0,0,0.95)] rounded-sm p-1.5 z-50 min-w-[240px] text-xs text-slate-300 font-sans backdrop-blur-md border-b-2 border-b-cyan-500"
        >
          <div className="px-2 py-1 text-[8px] text-cyan-500/60 font-black uppercase tracking-widest border-b border-slate-900 mb-1.5 select-none flex items-center gap-1.5">
            <Sparkles className="w-2.5 h-2.5 text-cyan-400" />
            <span>Batch Operations // {selectedAccountIds.length} Nodes</span>
          </div>

          {/* CHỨC NĂNG 1: ĐĂNG NHẬP COOKIES HÀNG LOẠT */}
          <button 
            onClick={() => handleBulkLogin('COOKIE')} // <-- Gọi hàm xử lý Đăng nhập Cookies độc lập
            className="w-full text-left px-2 py-1.5 hover:bg-cyan-950/20 hover:text-cyan-400 rounded-sm flex items-center gap-2.5 transition-all group font-mono"
          >
            <RefreshCw className="w-3.5 h-3.5 text-slate-600 group-hover:text-cyan-400 transition-transform duration-300 group-hover:rotate-180" />
            <span className="text-[10px] font-black uppercase tracking-widest">LOGIN_VIA_COOKIES</span>
          </button>

          {/* CHỨC NĂNG 2: ĐĂNG NHẬP FORM OTP HÀNG LOẠT */}
          <button 
            onClick={() => handleBulkLogin('CREDENTIAL')} // <-- Gọi hàm xử lý Đăng nhập Form OTP độc lập
            className="w-full text-left px-2 py-1.5 hover:bg-cyan-950/20 hover:text-cyan-400 rounded-sm flex items-center gap-2.5 transition-all group font-mono"
          >
            <Key className="w-3.5 h-3.5 text-slate-600 group-hover:text-cyan-400" />
            <span className="text-[10px] font-black uppercase tracking-widest">LOGIN_VIA_FORM_OTP</span>
          </button>

          {/* CHỨC NĂNG 3: ĐỔI PROFILE HÀNG LOẠT (Chỉ chạy khi Cookies sống) */}
          <button 
            onClick={handleBulkUpdateProfile} // <-- Gọi hàm xử lý Đổi Avatar & Bio độc lập
            className="w-full text-left px-2 py-1.5 hover:bg-cyan-950/20 hover:text-cyan-400 rounded-sm flex items-center gap-2.5 transition-all group font-mono"
          >
            <Video className="w-3.5 h-3.5 text-slate-600 group-hover:text-cyan-400 animate-bounce" />
            <span className="text-[10px] font-black uppercase tracking-widest">UPDATE_AVATAR_BIO</span>
          </button>

          {/* CHỨC NĂNG 4: TỰ ĐỘNG PHÂN BỔ PROXY HÀNG LOẠT */}
          <button 
            onClick={handleAutoAllocateProxies}
            className="w-full text-left px-2 py-1.5 hover:bg-amber-950/20 hover:text-amber-400 rounded-sm flex items-center gap-2.5 transition-all group font-mono"
          >
            <Globe className="w-3.5 h-3.5 text-slate-600 group-hover:text-amber-400" />
            <span className="text-[10px] font-black uppercase tracking-widest">AUTO_MAP_PROXIES</span>
          </button>
          
          <div className="h-[1px] bg-slate-900 my-1.5 select-none"></div>
          
          <button disabled className="w-full text-left px-2 py-1.5 text-slate-700 flex items-center gap-2.5 cursor-not-allowed font-mono">
            <Trash2 className="w-3.5 h-3.5 text-slate-900" />
            <span className="text-[10px] font-black uppercase tracking-widest">DROP_RECORDS</span>
          </button>
        </div>
      )}

    </div>
  );
}