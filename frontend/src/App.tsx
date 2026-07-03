import React, { useEffect, useState, useRef } from 'react';
import { useAppStore } from './store/useAppStore';
import { initWebSocket } from './services/websocket';
import { Play, Users, CloudLightning, Plus, ShieldAlert, MonitorPlay, FileInput, Terminal as TerminalIcon, Globe, Settings, Link } from 'lucide-react';

interface LogMessage {
  time: string;
  username: string;
  message: string;
}

export default function App() {
  const { accounts, proxies, wsConnected, setAccounts, setProxies, addAccount, addProxy } = useAppStore();
  const [activeTab, setActiveTab] = useState<'accounts' | 'proxies'>('accounts');
  const [rawFileContent, setRawFileContent] = useState('');
  
  // States cho Proxy Form
  const [proxyHost, setProxyHost] = useState('');
  const [proxyPort, setProxyPort] = useState('');
  const [proxyProtocol, setProxyProtocol] = useState('http');
  const [proxyUser, setProxyUser] = useState('');
  const [proxyPass, setProxyPass] = useState('');

  const [logs, setLogs] = useState<LogMessage[]>([]);
  const [loading, setLoading] = useState(false);
  const terminalEndRef = useRef<HTMLDivElement>(null);

  // 1. Đồng bộ kết nối và tải dữ liệu ban đầu
  useEffect(() => {
    initWebSocket();

    // Lắng nghe realtime từ WebSocket
    const handleWsEvents = (event: MessageEvent) => {
      try {
        const message = JSON.parse(event.data);
        if (message.event === 'TASK_STEP_UPDATED') {
          const { id, current_step } = message.data;
          useAppStore.setState((state) => ({
            accounts: state.accounts.map(acc => acc.id === id ? { ...acc, current_step } : acc)
          }));
        } else if (message.event === 'ACCOUNT_PROXY_CHANGED') {
          const { id, proxy_id } = message.data;
          useAppStore.getState().updateAccountProxy(id, proxy_id);
        } else if (message.event === 'TERMINAL_LOG') {
          const { username, message: logMsg } = message.data;
          const time = new Date().toLocaleTimeString();
          setLogs((prev) => [...prev, { time, username, message: logMsg }]);
        }
      } catch (err) {
        console.error(err);
      }
    };

    const ws = new WebSocket('ws://127.0.0.1:8000/ws');
    ws.onmessage = handleWsEvents;

    // Tải Accounts
    fetch('http://127.0.0.1:8000/api/v1/accounts/')
      .then((res) => res.json())
      .then((data) => setAccounts(data))
      .catch((err) => console.error(err));

    // Tải Proxies
    fetch('http://127.0.0.1:8000/api/v1/proxies/')
      .then((res) => res.json())
      .then((data) => setProxies(data))
      .catch((err) => console.error(err));

    return () => ws.close();
  }, [setAccounts, setProxies]);

  useEffect(() => {
    terminalEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [logs]);

  // 2. Thêm Proxy mới
  const handleAddProxy = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!proxyHost || !proxyPort) return;
    setLoading(true);

    try {
      const response = await fetch('http://127.0.0.1:8000/api/v1/proxies/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          host: proxyHost,
          port: parseInt(proxyPort),
          protocol: proxyProtocol,
          username: proxyUser || null,
          password: proxyPass || null
        }),
      });

      if (response.ok) {
        setProxyHost('');
        setProxyPort('');
        setProxyUser('');
        setProxyPass('');
        // Reload Proxies
        const res = await fetch('http://127.0.0.1:8000/api/v1/proxies/');
        const data = await res.json();
        setProxies(data);
        alert('Đã thêm Proxy thành công!');
      }
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  // 3. Import Tài Khoản từ file txt
  const handleImportRaw = async () => {
    if (!rawFileContent.trim()) return;
    setLoading(true);
    try {
      const response = await fetch('http://127.0.0.1:8000/api/v1/accounts/import-raw', {
        method: 'POST',
        headers: { 'Content-Type': 'text/plain' },
        body: rawFileContent,
      });
      if (response.ok) {
        setRawFileContent('');
        alert('Nhập dữ liệu tài khoản thành công!');
        const res = await fetch('http://127.0.0.1:8000/api/v1/accounts/');
        const data = await res.json();
        setAccounts(data);
      }
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  // 4. Kích hoạt đổi Proxy cho Tài khoản (Binding)
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

  const handleStartTask = async (accountId: string, method: string) => {
    try {
      await fetch(
        `http://127.0.0.1:8000/api/v1/tasks/start?account_id=${accountId}&login_method=${method}`,
        { method: 'POST' }
      );
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
    <div className="min-h-screen bg-[#070b15] p-6 text-slate-100 flex flex-col gap-6">
      
      {/* HEADER BAR */}
      <div className="flex justify-between items-center border-b border-slate-800 pb-4">
        <div>
          <h1 className="text-2xl font-bold bg-gradient-to-r from-teal-400 to-blue-500 bg-clip-text text-transparent">
            TikTok Professional Multi-Thread Console
          </h1>
          <p className="text-slate-400 text-sm">Bộ điều hợp đa luồng tàng hình kết nối Proxy động</p>
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

      <div className="grid grid-cols-1 lg:grid-cols-4 gap-6 flex-1">
        
        {/* PANEL TRÁI: FORM ĐẦU VÀO THEO TỪNG TAB */}
        <div className="bg-[#0e1424] p-5 rounded-2xl border border-slate-800 h-fit flex flex-col gap-4">
          {activeTab === 'accounts' ? (
            <>
              <div className="flex items-center gap-2">
                <FileInput className="text-teal-400 w-5 h-5" />
                <h2 className="font-bold text-slate-200">Import File .txt</h2>
              </div>
              <textarea
                rows={10}
                value={rawFileContent}
                onChange={(e) => setRawFileContent(e.target.value)}
                placeholder="Dán dòng text tài khoản..."
                className="w-full bg-[#182032] border border-slate-700 rounded-xl p-3 text-xs font-mono text-slate-300 focus:outline-none focus:ring-1 focus:ring-teal-400"
              />
              <button
                onClick={handleImportRaw}
                disabled={loading}
                className="w-full bg-teal-500 hover:bg-teal-600 font-bold text-slate-950 p-2.5 rounded-xl text-sm transition-all"
              >
                Bắt đầu Import
              </button>
            </>
          ) : (
            <>
              <div className="flex items-center gap-2">
                <Plus className="text-teal-400 w-5 h-5" />
                <h2 className="font-bold text-slate-200">Thêm Proxy</h2>
              </div>
              <form onSubmit={handleAddProxy} className="space-y-3 text-xs">
                <div>
                  <label className="text-slate-400 block mb-1">Host / IP</label>
                  <input
                    type="text"
                    required
                    placeholder="127.0.0.1 hoặc domain proxy"
                    value={proxyHost}
                    onChange={(e) => setProxyHost(e.target.value)}
                    className="w-full bg-[#1e293b] border border-slate-700 rounded-lg p-2 text-slate-100"
                  />
                </div>
                <div>
                  <label className="text-slate-400 block mb-1">Cổng (Port)</label>
                  <input
                    type="number"
                    required
                    placeholder="8080"
                    value={proxyPort}
                    onChange={(e) => setProxyPort(e.target.value)}
                    className="w-full bg-[#1e293b] border border-slate-700 rounded-lg p-2 text-slate-100"
                  />
                </div>
                <div>
                  <label className="text-slate-400 block mb-1">Giao thức (Protocol)</label>
                  <select
                    value={proxyProtocol}
                    onChange={(e) => setProxyProtocol(e.target.value)}
                    className="w-full bg-[#1e293b] border border-slate-700 rounded-lg p-2 text-slate-100"
                  >
                    <option value="http">HTTP</option>
                    <option value="socks5">SOCKS5</option>
                  </select>
                </div>
                <div>
                  <label className="text-slate-400 block mb-1">Username (Không bắt buộc)</label>
                  <input
                    type="text"
                    value={proxyUser}
                    onChange={(e) => setProxyUser(e.target.value)}
                    className="w-full bg-[#1e293b] border border-slate-700 rounded-lg p-2 text-slate-100"
                  />
                </div>
                <div>
                  <label className="text-slate-400 block mb-1">Password (Không bắt buộc)</label>
                  <input
                    type="password"
                    value={proxyPass}
                    onChange={(e) => setProxyPass(e.target.value)}
                    className="w-full bg-[#1e293b] border border-slate-700 rounded-lg p-2 text-slate-100"
                  />
                </div>
                <button
                  type="submit"
                  disabled={loading}
                  className="w-full bg-teal-500 hover:bg-teal-600 font-bold text-slate-950 p-2.5 rounded-xl text-sm transition-all"
                >
                  Lưu Proxy
                </button>
              </form>
            </>
          )}
        </div>

        {/* PANEL PHẢI: BẢNG TRÌNH DIỄN DỮ LIỆU */}
        <div className="lg:col-span-3 flex flex-col gap-6">
          
          {/* Card Thống kê */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <div className="bg-[#0e1424] p-4 rounded-xl border border-slate-800 flex items-center justify-between">
              <div><p className="text-xs text-slate-400">Tài khoản</p><p className="text-2xl font-bold mt-1">{stats.total}</p></div>
              <Users className="text-blue-400 w-7 h-7 opacity-75" />
            </div>
            <div className="bg-[#0e1424] p-4 rounded-xl border border-slate-800 flex items-center justify-between">
              <div><p className="text-xs text-slate-400">Đang hoạt động</p><p className="text-2xl font-bold mt-1 text-amber-400">{stats.running}</p></div>
              <CloudLightning className="text-amber-400 w-7 h-7 opacity-75" />
            </div>
            <div className="bg-[#0e1424] p-4 rounded-xl border border-slate-800 flex items-center justify-between">
              <div><p className="text-xs text-slate-400">Proxy Quản lý</p><p className="text-2xl font-bold mt-1 text-teal-400">{stats.proxies}</p></div>
              <Globe className="text-teal-400 w-7 h-7 opacity-75" />
            </div>
            <div className="bg-[#0e1424] p-4 rounded-xl border border-slate-800 flex items-center justify-between">
              <div><p className="text-xs text-slate-400">Đã đăng nhập</p><p className="text-2xl font-bold mt-1 text-emerald-400">{stats.loggedIn}</p></div>
              <ShieldAlert className="text-emerald-400 w-7 h-7 opacity-75" />
            </div>
          </div>

          {/* VIEW CHÍNH: TÀI KHOẢN HOẶC PROXY */}
          <div className="bg-[#0e1424] rounded-2xl border border-slate-800 overflow-hidden flex-1">
            {activeTab === 'accounts' ? (
              <>
                <div className="p-4 border-b border-slate-800">
                  <h2 className="font-bold text-slate-200">Quản lý điều phối tài khoản</h2>
                </div>
                <div className="overflow-y-auto max-h-[350px]">
                  <table className="w-full text-left border-collapse">
                    <thead>
                      <tr className="bg-[#141b2e] text-xs font-semibold text-slate-400 uppercase">
                        <th className="p-4">Tài khoản</th>
                        <th className="p-4">Liên kết IP Proxy</th>
                        <th className="p-4">Tiến trình hiện tại</th>
                        <th className="p-4 text-right">Hành động</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-800 text-xs">
                      {accounts.map((acc: any) => (
                        <tr key={acc.id} className="hover:bg-slate-900/40 transition-colors">
                          <td className="p-4">
                            <div className="font-bold text-slate-200">{acc.username}</div>
                            <div className="text-[10px] text-slate-500 font-mono mt-0.5">{acc.id}</div>
                          </td>
                          <td className="p-4">
                            {/* Trình chọn gán Proxy trực tiếp (Dropdown Selector) */}
                            <select
                              value={acc.proxy_id || 'none'}
                              onChange={(e) => handleBindProxy(acc.id, e.target.value)}
                              className="bg-[#182032] border border-slate-700 rounded-lg p-1.5 text-xs text-teal-400 font-medium focus:outline-none focus:ring-1 focus:ring-teal-400"
                            >
                              <option value="none">Chạy mạng thật (No Proxy)</option>
                              {proxies.map((p) => (
                                <option key={p.id} value={p.id}>
                                  [{p.protocol.toUpperCase()}] {p.host}:{p.port}
                                </option>
                              ))}
                            </select>
                          </td>
                          <td className="p-4 font-mono">
                            <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[10px] font-bold ${
                              acc.status === 'RUNNING' ? 'text-amber-400 animate-pulse' :
                              acc.status === 'QUEUED' ? 'text-teal-400' :
                              acc.status === 'LOGGED_IN' ? 'text-emerald-400' : 'text-slate-400'
                            }`}>
                              {acc.status === 'RUNNING' ? `⏳ ${acc.current_step}` : (acc.current_step || 'Chưa chạy')}
                            </span>
                          </td>
                          <td className="p-4 text-right">
                            <button
                              onClick={() => handleStartTask(acc.id, 'COOKIE')}
                              disabled={acc.status === 'RUNNING' || acc.status === 'QUEUED'}
                              className="inline-flex items-center gap-1 px-3 py-1.5 rounded-lg text-[10px] font-bold bg-emerald-500 hover:bg-emerald-600 disabled:bg-slate-800 disabled:text-slate-600 text-slate-950 transition-all"
                            >
                              <Play className="w-3 h-3" /> Cookie Login
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </>
            ) : (
              <>
                <div className="p-4 border-b border-slate-800">
                  <h2 className="font-bold text-slate-200">Kho lưu trữ IP Proxy</h2>
                </div>
                <div className="overflow-y-auto max-h-[350px]">
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
                      {proxies.map((p) => (
                        <tr key={p.id} className="hover:bg-slate-900/40 font-mono">
                          <td className="p-4"><span className="bg-teal-500/10 text-teal-400 border border-teal-500/30 px-2 py-0.5 rounded font-bold text-[10px]">{p.protocol.toUpperCase()}</span></td>
                          <td className="p-4 text-slate-100 font-bold">{p.host}</td>
                          <td className="p-4 text-slate-300">{p.port}</td>
                          <td className="p-4 text-slate-500">{p.username ? p.username : 'Không có'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </>
            )}
          </div>
        </div>
      </div>

      {/* TERMINAL CONSOLE */}
      <div className="bg-[#050811] border border-slate-800 rounded-2xl overflow-hidden flex flex-col h-[180px]">
        <div className="bg-[#0e1424] px-4 py-2 border-b border-slate-800 flex justify-between items-center">
          <div className="flex items-center gap-2 text-xs font-semibold text-slate-300">
            <TerminalIcon className="text-teal-400 w-4 h-4" />
            <span>Live Console Monitor</span>
          </div>
          <button onClick={() => setLogs([])} className="text-[10px] text-slate-500 hover:text-slate-300 font-bold">Xóa nhật ký</button>
        </div>
        <div className="p-4 font-mono text-xs overflow-y-auto flex-1 space-y-1 text-slate-400 bg-black/40">
          {logs.length === 0 ? <div className="text-slate-600 italic">Chờ nhận nhật ký thao tác luồng...</div> : logs.map((log, index) => (
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