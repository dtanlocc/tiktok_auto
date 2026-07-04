import React, { useEffect, useState, useRef } from 'react';
import { useAppStore, Account, Proxy } from './store/useAppStore';
import { initWebSocket } from './services/websocket';
import { Play, Users, CloudLightning, Plus, ShieldAlert, MonitorPlay, FileInput, Terminal as TerminalIcon, Globe, Settings, CheckSquare, Square } from 'lucide-react';

interface LogMessage {
  time: string;
  username: string;
  message: string;
}

export default function App() {
  const { accounts, proxies, wsConnected, setAccounts, setProxies } = useAppStore();
  const [activeTab, setActiveTab] = useState<'accounts' | 'proxies'>('accounts');
  
  // Các cấu hình điều khiển trung tâm (Control Panel)
  const [concurrency, setConcurrency] = useState(settings_concurrency => 4);
  const [avatarFolder, setAvatarFolder] = useState('');
  
  // Tích chọn tài khoản (Checkbox Selection)
  const [selectedAccountIds, setSelectedAccountIds] = useState<string[]>([]);

  const [logs, setLogs] = useState<LogMessage[]>([]);
  const [loading, setLoading] = useState(false);
  const terminalEndRef = useRef<HTMLDivElement>(null);

  // 1. Đồng bộ kết nối và tải dữ liệu ban đầu
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

    const ws = new ErrorWebSocketFake(); // Giả kết nối độc lập hoặc dùng chung
    const activeWs = new WebSocket('ws://127.0.0.1:8000/ws');
    activeWs.onmessage = handleWsEvents;

    // Load dữ liệu
    loadData();

    return () => activeWs.close();
  }, [setAccounts, setProxies]);

  const loadData = () => {
    fetch('http://127.0.0.1:8000/api/v1/accounts/')
      .then((res) => res.json())
      .then((data) => setAccounts(data))
      .catch((err) => console.error(err));

    fetch('http://127.0.0.1:8000/api/v1/proxies/')
      .then((res) => res.json())
      .then((data) => setProxies(data))
      .catch((err) => console.error(err));
  };

  useEffect(() => {
    terminalEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [logs]);

  // 2. Xử lý tải File .txt hàng loạt tài khoản hoặc proxy lên Server
  const handleFileUpload = async (event: React.ChangeEvent<HTMLInputElement>, type: 'accounts' | 'proxies') => {
    const file = event.target.files?.[0];
    if (!file) return;

    const formData = new FormData();
    formData.append('file', file);
    setLoading(true);

    const url = type === 'accounts' 
      ? 'http://127.0.0.1:8000/api/v1/accounts/import-file' 
      : 'http://127.0.0.1:8000/api/v1/proxies/import-file';

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
        alert('Lỗi khi tải file lên');
      }
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  // 3. Tích chọn / Huỷ chọn tài khoản hàng loạt
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

  // 4. Kích hoạt luồng chạy HÀNG LOẠT cho các tài khoản đã tích chọn
  const handleBulkStart = async (method: 'COOKIE' | 'CREDENTIAL') => {
    if (selectedAccountIds.length === 0) {
      alert("Vui lòng chọn ít nhất một tài khoản trên bảng để chạy.");
      return;
    }

    try {
      const response = await fetch('http://127.0.0.1:8000/api/v1/tasks/bulk-start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          account_ids: selectedAccountIds,
          login_method: method,
          avatar_folder: avatarFolder || null,
          concurrency_limit: concurrency
        }),
      });

      if (response.ok) {
        const result = await response.json();
        setSelectedAccountIds([]); // Reset tích chọn sau khi đưa vào hàng đợi
      } else {
        const err = await response.json();
        alert(`Lỗi kích hoạt: ${err.detail}`);
      }
    } catch (err) {
      console.error(err);
    }
  };

  const handleBindProxy = async (accountId: string, proxyId: string) => {
    try {
      const targetProxyId = proxyId === 'none' ? null : proxyId;
      await fetch(`http://127.0.0.1:8000/api/v1/accounts/${accountId}/proxy`, {
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
    <div className="min-h-screen bg-[#070b15] p-6 text-slate-100 flex flex-col gap-5">
      
      {/* HEADER BAR */}
      <div className="flex justify-between items-center border-b border-slate-800 pb-4">
        <div>
          <h1 className="text-2xl font-bold bg-gradient-to-r from-teal-400 to-blue-500 bg-clip-text text-transparent">
            TikTok Professional Multi-Thread Console
          </h1>
          <p className="text-slate-400 text-sm">Bộ quản trị đa luồng tàng hình phân bổ IP & Thư mục ảnh đại diện</p>
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

      {/* CONTROL PANEL TRUNG TÂM (Thanh đặt số luồng và thư mục) */}
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
        
        {/* PANEL TRÁI: TẢI FILE .TXT HOẶC PROXY */}
        <div className="bg-[#0e1424] p-5 rounded-2xl border border-slate-800 h-fit flex flex-col gap-4">
          <div className="flex items-center gap-2">
            <FileInput className="text-teal-400 w-5 h-5" />
            <h2 className="font-bold text-slate-200">Nhập dữ liệu (.txt)</h2>
          </div>
          
          {activeTab === 'accounts' ? (
            <div className="space-y-4">
              <p className="text-xs text-slate-400 leading-relaxed">
                Tải lên tệp tin `.txt` chứa danh sách tài khoản TikTok phân tách bằng dấu đứng `|`.
              </p>
              <label className="flex flex-col items-center justify-center border-2 border-dashed border-slate-700 rounded-xl p-6 cursor-pointer hover:border-teal-400 transition-colors bg-[#182032]/50">
                <Plus className="text-slate-400 w-6 h-6 mb-2" />
                <span className="text-xs font-semibold text-slate-300">Chọn file accounts.txt</span>
                <input
                  type="file"
                  accept=".txt"
                  disabled={loading}
                  onChange={(e) => handleFileUpload(e, 'accounts')}
                  className="hidden"
                />
              </label>
            </div>
          ) : (
            <div className="space-y-4">
              <p className="text-xs text-slate-400 leading-relaxed">
                Tải lên tệp tin `.txt` chứa danh sách Proxy. Hỗ trợ định dạng `protocol://host:port` hoặc định dạng dấu đứng `|`.
              </p>
              <label className="flex flex-col items-center justify-center border-2 border-dashed border-slate-700 rounded-xl p-6 cursor-pointer hover:border-teal-400 transition-colors bg-[#182032]/50">
                <Plus className="text-slate-400 w-6 h-6 mb-2" />
                <span className="text-xs font-semibold text-slate-300">Chọn file proxies.txt</span>
                <input
                  type="file"
                  accept=".txt"
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
                  
                  {/* Các nút bấm kích hoạt hàng loạt cho các tài khoản được tích */}
                  <div className="flex gap-2">
                    <button
                      onClick={() => handleBulkStart('COOKIE')}
                      disabled={selectedAccountIds.length === 0}
                      className="inline-flex items-center gap-1 px-4 py-2 rounded-xl text-xs font-bold bg-emerald-500 hover:bg-emerald-600 disabled:bg-slate-800 disabled:text-slate-600 text-slate-950 transition-all shadow-md"
                    >
                      <Play className="w-3.5 h-3.5" /> Chạy đổi Avatar & Bio hàng loạt
                    </button>
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
                            Chưa có tài khoản nào. Vui lòng tải lên file .txt để nhập hàng loạt.
                          </td>
                        </tr>
                      ) : (
                        accounts.map((acc: Account) => (
                          <tr key={acc.id} className={`hover:bg-slate-900/40 transition-colors ${selectedAccountIds.includes(acc.id) ? 'bg-slate-900/20' : ''}`}>
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
                            {/* Cột Tiến trình chạy ngắn gọn gọn gàng */}
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

      {/* TERMINAL CONSOLE REALTIME CHUYÊN NGHIỆP */}
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
    </div>
  );
}

// Biến giả lập phòng ngừa lỗi khai báo trong React
const settings_concurrency = 4;
class ErrorWebSocketFake {
  onmessage() {}
  close() {}
}