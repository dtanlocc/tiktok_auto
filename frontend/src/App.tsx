import React, { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useAppStore } from './store/useAppStore';
import { Account, AppTab } from './types';

// Nhập khẩu các thành phần đã mô-đun hóa
import { NavSidebar } from './components/NavSidebar';
import { ControlPanel } from './components/ControlPanel';
import { Sidebar } from './components/Sidebar';
import { StatsCards } from './components/StatsCards';
import { AccountPerformanceSummary } from './components/AccountPerformanceSummary';
import { AccountsTable } from './components/AccountsTable';
import { ProxiesTable } from './components/ProxiesTable';
import { InteractionPanel } from './components/InteractionPanel';
import { TerminalConsole } from './components/TerminalConsole';
import { ContextMenu } from './components/ContextMenu';
import { FolderTree } from './components/FolderTree';
import { ImportModal } from './components/ImportModal'; // <-- IMPORT MODAL NỔI MỚI THÊM
import { ExportModal } from './components/ExportModal';
import { TaskCompletionPopup, TaskCompletionNotice } from './components/TaskCompletionPopup';
import { BarChart3, Folder, ListTree, X, Zap, Ban, Download, FolderInput, Cookie, Trash2, Copy } from 'lucide-react';

const VideoManager = lazy(() => import('./components/VideoManager').then((module) => ({ default: module.VideoManager })));
const LiveScreens = lazy(() => import('./components/LiveScreens').then((module) => ({ default: module.LiveScreens })));

export default function App() {
  const accounts = useAppStore((state) => state.accounts);
  const proxies = useAppStore((state) => state.proxies);
  const setAccounts = useAppStore((state) => state.setAccounts);
  const setProxies = useAppStore((state) => state.setProxies);
  const updateAccountFields = useAppStore((state) => state.updateAccountFields);
  const [activeTab, setActiveTab] = useState<AppTab>('accounts');
  
  // Bộ điều khiển trung tâm (Control Panel)
  // concurrency = SỐ LUỒNG TỐI ĐA / 1 PROXY (thay cho "số luồng tổng" trước đây).
  const [concurrency, setConcurrency] = useState<number>(8);
  const [avatarFolder, setAvatarFolder] = useState<string>('');

  // ĐIỀU KHIỂN TOÀN CỤC: Bắt đầu / Tạm dừng / Tiếp tục / Dừng khẩn cấp
  const [isGloballyPaused, setIsGloballyPaused] = useState<boolean>(false);

  // THU GỌN CÂY THƯ MỤC QUỐC GIA - mặc định THU GỌN để bảng tài khoản
  // hiển thị đầy đủ (giống view database) ngay khi mở trang, đỡ chiếm chỗ.
  const [isTreeCollapsed, setIsTreeCollapsed] = useState<boolean>(true);


  // Danh sách ID tài khoản được chọn (Checkbox Selection)
  const [selectedAccountIds, setSelectedAccountIds] = useState<string[]>([]);
  // Danh sách account đang có phiên tay mở (để đổi nút Run one test <-> Đóng phiên).
  const [manualSessionIds, setManualSessionIds] = useState<string[]>([]);
  const [contextMenu, setContextMenu] = useState<{ x: number; y: number; visible: boolean } | null>(null);
  
  // Bộ lọc dữ liệu đa chiều
  const [statusFilter, setStatusFilter] = useState<string>('ALL');

  // TRẠNG THÁI CÂY THƯ MỤC VÀ POPUP IMPORT NỔI
  const [selectedCountry, setSelectedCountry] = useState<string | null>(null);
  const [selectedBatch, setSelectedBatch] = useState<string | null>(null);
  const [expandedCountries, setExpandedCountries] = useState<string[]>([]);
  const [isImportModalOpen, setIsImportModalOpen] = useState<boolean>(false); // <-- TRẠNG THÁI POPUP IMPORT
  const [isExportModalOpen, setIsExportModalOpen] = useState<boolean>(false); // <-- POPUP XUẤT ACC
  const [proxyMode, setProxyMode] = useState<boolean>(true); // true = dùng proxy (auto-map); false = mạng thật

  const [loading, setLoading] = useState<boolean>(false);
  const [taskCompletionNotices, setTaskCompletionNotices] = useState<TaskCompletionNotice[]>([]);
  const taskNoticeSequence = useRef(0);

  const pushTaskCompletionNotice = useCallback((notice: Omit<TaskCompletionNotice, 'id'>) => {
    taskNoticeSequence.current += 1;
    const nextNotice: TaskCompletionNotice = {
      ...notice,
      id: `${Date.now()}-${taskNoticeSequence.current}`,
    };
    setTaskCompletionNotices((current) => [...current, nextNotice].slice(-3));
  }, []);

  const dismissTaskCompletionNotice = useCallback((id: string) => {
    setTaskCompletionNotices((current) => current.filter((notice) => notice.id !== id));
  }, []);

  const loadData = useCallback(() => {
    fetch('http://127.0.0.1:9000/api/v1/accounts/')
      .then((res) => res.json())
      .then((data) => {
        // Email is now the account primary key. Preserve selections made before
        // the one-time UUID -> email migration when the backend reconnects.
        const previous = useAppStore.getState().accounts;
        const oldEmailById = new Map(previous.map((account) => [account.id, account.email]));
        const validIds = new Set((Array.isArray(data) ? data : []).map((account: Account) => account.id));
        setSelectedAccountIds((ids) => ids
          .map((id) => oldEmailById.get(id) || id)
          .filter((id) => validIds.has(id)));
        setAccounts(data);
      })
      .catch((err) => console.error('Lỗi tải danh sách tài khoản:', err));

    fetch('http://127.0.0.1:9000/api/v1/proxies/')
      .then((res) => res.json())
      .then((data) => setProxies(data))
      .catch((err) => console.error('Lỗi tải danh sách proxy:', err));

    fetch('http://127.0.0.1:9000/api/v1/tasks/status')
      .then((res) => res.json())
      .then((data) => {
        setIsGloballyPaused(!!data.is_globally_paused);
        if (typeof data.proxy_max_concurrent === 'number') setConcurrency(data.proxy_max_concurrent);
      })
      .catch((err) => console.error('Lỗi tải trạng thái dispatcher:', err));
  }, [setAccounts, setProxies]);

  // 1. Khởi động WebSockets, tải dữ liệu ban đầu và lắng nghe sự kiện đóng menu chuột phải
  useEffect(() => {
    const handleWsEvents = (event: MessageEvent) => {
      try {
        // Bo qua som cac frame anh man hinh (tan suat cao, kich thuoc lon) - da
        // duoc component LiveScreens xu ly bang WebSocket rieng. Tranh parse JSON
        // nang o day gay giat bang tai khoan.
        if (typeof event.data === 'string' && event.data.indexOf('BROWSER_FRAME') !== -1) return;

        const message = JSON.parse(event.data);
        if (message.event === 'TASK_STEP_UPDATED') {
          const { id, current_step } = message.data;
          useAppStore.setState((state) => ({
            accounts: state.accounts.map(acc => acc.id === id ? { ...acc, current_step } : acc)
          }));
        } else if (message.event === 'ACCOUNT_STATUS_CHANGED') {
          const { id, ...fields } = message.data;
          useAppStore.setState((state) => ({
            accounts: state.accounts.map(acc => acc.id === id ? { ...acc, ...fields } : acc)
          }));
        } else if (message.event === 'TERMINAL_LOG') {
          const { username, message: logMsg } = message.data;
          useAppStore.getState().addLog({ time: new Date().toLocaleTimeString(), username, message: logMsg });
        } else if (message.event === 'GLOBAL_STATE_CHANGED') {
          setIsGloballyPaused(!!message.data.is_globally_paused);
        } else if (message.event === 'ACCOUNT_PAUSE_CHANGED') {
          const { id, is_paused } = message.data;
          useAppStore.setState((state) => ({
            accounts: state.accounts.map(acc => acc.id === id ? { ...acc, is_paused } : acc)
          }));
        } else if (message.event === 'ACCOUNT_UPDATED') {
          // Sửa trường trực tiếp (username/country/batch_tag...) hoặc xóa cookies
          const { id, ...fields } = message.data;
          useAppStore.setState((state) => ({
            accounts: state.accounts.map(acc => acc.id === id ? { ...acc, ...fields } : acc)
          }));
        } else if (message.event === 'ACCOUNT_ADDED') {
          useAppStore.getState().addAccount({ ...message.data, email: message.data.email || '' });
        } else if (message.event === 'ACCOUNT_DELETED') {
          useAppStore.getState().deleteAccount(message.data.id);
        } else if (message.event === 'ACCOUNT_PROXY_CHANGED') {
          useAppStore.getState().updateAccountProxy(message.data.id, message.data.proxy_id);
        } else if (message.event === 'QUICK_CHECK_FINISHED') {
          const completed = Number(message.data?.completed) || 0;
          const total = Number(message.data?.total) || 0;
          const alive = Number(message.data?.alive) || 0;
          const dead = Number(message.data?.dead) || 0;
          const inconclusive = Number(message.data?.inconclusive) || 0;
          useAppStore.getState().addLog({
            time: new Date().toLocaleTimeString(),
            username: 'System',
            message: `Đã hoàn tất đợt Check nhanh Sống/Chết: ${completed}/${total} tài khoản.`
          });
          pushTaskCompletionNotice({
            tone: dead > 0 || inconclusive > 0 ? 'warning' : 'success',
            title: 'Check nhanh đã hoàn tất',
            message: `Đã kiểm tra xong ${completed}/${total} tài khoản. Kết quả từng tài khoản đã được cập nhật trên bảng.`,
            stats: [
              { label: 'Đã kiểm tra', value: completed },
              { label: 'Sống', value: alive },
              { label: 'Die', value: dead },
              { label: 'Chưa kết luận', value: inconclusive },
            ],
          });
        } else if (message.event === 'FAST_ANALYTICS_FINISHED') {
          const completed = Number(message.data?.completed) || 0;
          const total = Number(message.data?.total) || 0;
          const updated = Number(message.data?.updated) || 0;
          const cached = Number(message.data?.cached) || 0;
          const failed = Number(message.data?.failed) || 0;
          const skipped_sold = Number(message.data?.skipped_sold) || 0;
          useAppStore.getState().addLog({
            time: new Date().toLocaleTimeString(),
            username: 'System',
            message: `Đồng bộ nhanh xong ${completed}/${total}: cập nhật ${updated}, cache ${cached}, lỗi ${failed}, bỏ qua ĐÃ BÁN ${skipped_sold}.`,
          });
          pushTaskCompletionNotice({
            tone: failed > 0 ? (failed >= completed && completed > 0 ? 'error' : 'warning') : 'success',
            title: 'Đồng bộ nhanh đã hoàn tất',
            message: `Đã xử lý xong ${completed}/${total} tài khoản. Dữ liệu mới đã hiển thị trên giao diện.`,
            stats: [
              { label: 'Đã xử lý', value: completed },
              { label: 'Cập nhật', value: updated },
              { label: 'Dùng cache', value: cached },
              { label: 'Lỗi', value: failed },
              { label: 'Bỏ qua đã bán', value: skipped_sold },
            ],
          });
        }
      } catch (err) {
        console.error(err);
      }
    };

    let disposed = false;
    let activeWs: WebSocket | null = null;
    let reconnectTimer: number | null = null;
    const connect = () => {
      activeWs = new WebSocket('ws://127.0.0.1:9000/ws');
      activeWs.onopen = () => {
        useAppStore.getState().setWsConnected(true);
        loadData();
      };
      activeWs.onmessage = handleWsEvents;
      activeWs.onclose = () => {
        useAppStore.getState().setWsConnected(false);
        if (!disposed) reconnectTimer = window.setTimeout(connect, 2000);
      };
    };
    connect();

    // Tải dữ liệu ban đầu
    loadData();

    // Đóng Menu chuột phải tự động khi click chuột trái ra ngoài màn hình
    const closeMenu = () => setContextMenu(null);
    document.addEventListener('click', closeMenu);

    return () => {
      disposed = true;
      activeWs?.close();
      if (reconnectTimer !== null) window.clearTimeout(reconnectTimer);
      document.removeEventListener('click', closeMenu);
    };
  }, [loadData, pushTaskCompletionNotice]);

  // Đồng bộ định kỳ danh sách phiên tay đang mở (bắt trường hợp user tự ĐÓNG
  // cửa sổ -> nút tự trở lại "Run one test").
  useEffect(() => {
    refreshManualSessions();
    const t = setInterval(refreshManualSessions, 4000);
    return () => clearInterval(t);
  }, []);

  // Nạp chế độ proxy hiện tại (proxy / mạng thật) để menu chuột phải hiện đúng.
  useEffect(() => {
    fetch('http://127.0.0.1:9000/api/v1/tasks/proxy-mode')
      .then((r) => r.json())
      .then((d) => { if (typeof d?.use_proxy === 'boolean') setProxyMode(d.use_proxy); })
      .catch(() => {});
  }, []);

  // Lưu ngay giá trị "số luồng tối đa / 1 proxy" xuống backend khi user chỉnh.
  const handleSetProxyConcurrency = async (val: number) => {
    setConcurrency(val); // cập nhật UI ngay
    if (!val || val < 1) return;
    try {
      await fetch('http://127.0.0.1:9000/api/v1/tasks/proxy-concurrency', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ limit: val }),
      });
    } catch {
      // im lặng - sẽ được áp dụng lại khi bấm chạy tác vụ
    }
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
      const response = await fetch('http://127.0.0.1:9000/api/v1/accounts/bulk-delete', {
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
  const treeFilteredAccounts = useMemo(() => accounts.filter(acc => {
    // Khi CHƯA chọn Quốc gia/Lô cụ thể trên cây thư mục (hoặc cây đang bị thu
    // gọn), coi như KHÔNG lọc theo cây -> hiển thị TOÀN BỘ tài khoản trong DB
    // (đúng yêu cầu "view database đầy đủ"). Chỉ khi người dùng chủ động chọn
    // 1 Lô cụ thể trên cây thì mới thu hẹp phạm vi lại.
    const matchTree = (selectedCountry && selectedBatch)
      ? (acc.country === selectedCountry && acc.batch_tag === selectedBatch)
      : true;

    return matchTree;
  }), [accounts, selectedBatch, selectedCountry]);
  const filteredAccounts = useMemo(
    () => statusFilter === 'ALL' ? treeFilteredAccounts : treeFilteredAccounts.filter((account) => account.status === statusFilter),
    [statusFilter, treeFilteredAccounts],
  );

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
      url = `http://127.0.0.1:9000/api/v1/accounts/import-file?country=${targetCountry}&batch_tag=${targetBatch}`;
    } else {
      url = 'http://127.0.0.1:9000/api/v1/proxies/import-file';
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
    const filteredIds = filteredAccounts.map((account) => account.id);
    const filteredIdSet = new Set(filteredIds);
    const allFilteredSelected = filteredIds.length > 0 && filteredIds.every((id) => selectedAccountIds.includes(id));
    if (allFilteredSelected) {
      setSelectedAccountIds(selectedAccountIds.filter((id) => !filteredIdSet.has(id)));
    } else {
      setSelectedAccountIds(Array.from(new Set([...selectedAccountIds, ...filteredIds])));
    }
  };

  // Đăng nhập hàng loạt
  const handleBulkLogin = async (method: 'COOKIE' | 'CREDENTIAL') => {
    if (selectedAccountIds.length === 0) {
      alert("Vui lòng tích chọn ít nhất một tài khoản trên bảng trước khi chạy.");
      return;
    }

    try {
      const response = await fetch('http://127.0.0.1:9000/api/v1/tasks/bulk-login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          account_ids: selectedAccountIds,
          login_method: method,
          proxy_concurrency: typeof concurrency === 'string' ? 2 : concurrency
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
      const response = await fetch('http://127.0.0.1:9000/api/v1/tasks/bulk-update-profile', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          account_ids: selectedAccountIds,
          avatar_folder: avatarFolder || null,
          proxy_concurrency: typeof concurrency === 'string' ? 2 : concurrency
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

  // Copy acc ra clipboard (định dạng giống Xuất acc) NHƯNG KHÔNG xóa khỏi app.
  // Ưu tiên các acc đã chọn; nếu chưa chọn -> copy cả lô đang hiển thị.
  const handleCopyAccounts = async () => {
    const ids = selectedAccountIds.length > 0 ? selectedAccountIds : filteredAccounts.map((a) => a.id);
    if (ids.length === 0) { alert('Không có tài khoản nào để copy.'); return; }
    try {
      const res = await fetch('http://127.0.0.1:9000/api/v1/accounts/copy', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ account_ids: ids }),
      });
      const data = await res.json();
      if (!res.ok) { alert(data.detail || 'Lỗi copy tài khoản.'); return; }
      try {
        await navigator.clipboard.writeText(data.content);
      } catch {
        // Fallback nếu clipboard API bị chặn: dùng textarea tạm.
        const ta = document.createElement('textarea');
        ta.value = data.content; document.body.appendChild(ta); ta.select();
        document.execCommand('copy'); document.body.removeChild(ta);
      }
      alert(`Đã copy ${data.copied_count} tài khoản vào clipboard (acc VẪN CÒN trong app).`);
    } catch {
      alert('Không kết nối được backend để copy.');
    }
  };

  // Đổi chế độ proxy (runtime): true = auto-map proxy như cũ; false = mạng thật (VPN)
  const handleSetProxyMode = async (useProxy: boolean) => {
    try {
      const res = await fetch('http://127.0.0.1:9000/api/v1/tasks/proxy-mode', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ use_proxy: useProxy }),
      });
      const data = await res.json();
      if (res.ok) {
        setProxyMode(data.use_proxy);
        alert(data.message || (useProxy ? 'Đã bật proxy.' : 'Đã chuyển sang mạng thật.'));
      } else {
        alert(data.detail || 'Lỗi đổi chế độ proxy.');
      }
    } catch {
      alert('Không kết nối được backend.');
    }
  };

  // Phân bổ Proxy tự động
  const handleAutoAllocateProxies = async () => {
    if (selectedAccountIds.length === 0) return;
    setLoading(true);

    try {
      const response = await fetch('http://127.0.0.1:9000/api/v1/accounts/auto-allocate-proxies', {
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

  // SỬA TRƯỜNG TRỰC TIẾP TRÊN UI (inline edit) - cập nhật store NGAY (không reload)
  // rồi gọi API PATCH lưu xuống DB. Nếu API lỗi -> khôi phục giá trị cũ.
  const handleUpdateAccount = async (accountId: string, fields: Partial<Account>) => {
    const before = accounts.find((a) => a.id === accountId);
    updateAccountFields(accountId, fields); // optimistic - đổi ngay trên UI
    try {
      const res = await fetch(`http://127.0.0.1:9000/api/v1/accounts/${accountId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(fields),
      });
      if (!res.ok) {
        const err = await res.json();
        alert(err.detail || 'Lỗi cập nhật.');
        if (before) updateAccountFields(accountId, before); // rollback UI
      } else {
        const saved = await res.json();
        updateAccountFields(accountId, saved);
        if (saved.id && saved.id !== accountId) {
          setSelectedAccountIds((ids) => ids.map((id) => id === accountId ? saved.id : id));
        }
      }
    } catch {
      alert('Không kết nối được backend.');
      if (before) updateAccountFields(accountId, before);
    }
  };

  // XÓA COOKIES các account đang chọn (giữ account, chỉ xóa cookies)
  const handleClearCookies = async () => {
    if (selectedAccountIds.length === 0) {
      alert('Vui lòng chọn ít nhất một tài khoản.');
      return;
    }
    if (!window.confirm(`Xóa cookies của ${selectedAccountIds.length} tài khoản đã chọn? (Account vẫn giữ, chỉ xóa cookies)`)) return;
    try {
      const res = await fetch('http://127.0.0.1:9000/api/v1/accounts/clear-cookies', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ account_ids: selectedAccountIds }),
      });
      const data = await res.json();
      if (res.ok) alert(data.message);
      else alert(data.detail || 'Lỗi xóa cookies.');
    } catch {
      alert('Không kết nối được backend.');
    }
  };

  // =========================================================================
  // CHẾ ĐỘ PHIÊN TAY: mở trình duyệt HIỆN, tự login rồi GIỮ mở để thao tác tay.
  // Tách riêng hoàn toàn với luồng mở trình duyệt ẩn (dispatcher).
  // =========================================================================
  const refreshManualSessions = async () => {
    try {
      const res = await fetch('http://127.0.0.1:9000/api/v1/tasks/debug-login/active');
      const data = await res.json();
      setManualSessionIds(Array.isArray(data.active_ids) ? data.active_ids : []);
    } catch {
      // im lặng - backend có thể chưa chạy
    }
  };

  const handleManualSessionStart = async (accountId: string) => {
    // Cập nhật lạc quan để nút đổi ngay sang "Đóng phiên".
    setManualSessionIds((prev) => (prev.includes(accountId) ? prev : [...prev, accountId]));
    try {
      const res = await fetch('http://127.0.0.1:9000/api/v1/tasks/debug-login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ account_id: accountId }),
      });
      const data = await res.json();
      if (!res.ok) {
        alert(data.detail || 'Không mở được phiên tay.');
        setManualSessionIds((prev) => prev.filter((id) => id !== accountId));
      }
    } catch {
      alert('Không kết nối được backend.');
      setManualSessionIds((prev) => prev.filter((id) => id !== accountId));
    }
  };

  const handleManualSessionStop = async (accountId: string) => {
    try {
      await fetch('http://127.0.0.1:9000/api/v1/tasks/debug-login/stop', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ account_id: accountId }),
      });
    } catch {
      // bỏ qua
    } finally {
      setManualSessionIds((prev) => prev.filter((id) => id !== accountId));
    }
  };

  // =========================================================================
  // CHUYỂN CỤM: đổi batch_tag (Lô) hàng loạt cho các account đã chọn để gom nhóm
  // theo dõi. Cho phép gõ tên cụm MỚI hoặc chọn 1 cụm CÓ SẴN.
  // =========================================================================
  const handleMoveToGroup = async () => {
    if (selectedAccountIds.length === 0) {
      alert('Vui lòng chọn ít nhất một tài khoản.');
      return;
    }
    // Gợi ý danh sách cụm (Lô) hiện có để người dùng biết mà gõ lại cho khớp.
    const existing = Array.from(new Set(accounts.map((a) => a.batch_tag).filter(Boolean))).sort();
    const hint = existing.length ? `\n\nCác cụm hiện có:\n- ${existing.join('\n- ')}` : '';
    const target = window.prompt(
      `Chuyển ${selectedAccountIds.length} tài khoản sang cụm nào?\n(Gõ tên cụm MỚI hoặc 1 cụm có sẵn)${hint}`,
      ''
    );
    if (target === null) return; // user bấm Cancel
    const name = target.trim();
    if (!name) {
      alert('Tên cụm không được để trống.');
      return;
    }
    // Cập nhật lạc quan: di chuyển ngay trên UI (bảng + cây Lô).
    const ids = [...selectedAccountIds];
    ids.forEach((id) => useAppStore.getState().updateAccountFields(id, { batch_tag: name }));
    try {
      const res = await fetch('http://127.0.0.1:9000/api/v1/accounts/move-to-group', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ account_ids: ids, batch_tag: name }),
      });
      const data = await res.json();
      if (res.ok) alert(data.message);
      else {
        alert(data.detail || 'Lỗi chuyển cụm.');
        loadData(); // rollback bằng cách nạp lại đúng trạng thái từ server
      }
    } catch {
      alert('Không kết nối được backend.');
      loadData();
    }
  };

  // Gán Proxy thủ công qua Dropdown
  const handleBindProxy = async (accountId: string, proxyId: string) => {
    try {
      const targetProxyId = proxyId === 'none' ? null : proxyId;
      await fetch(`http://127.0.0.1:9000/api/v1/accounts/${accountId}/proxy`, {
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
      const res = await fetch(`http://127.0.0.1:9000/api/v1/tasks/${endpoint}`, { method: 'POST' });
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
      const response = await fetch('http://127.0.0.1:9000/api/v1/tasks/quick-health-check', {
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

  const handleSyncAnalytics = async (accountIds: string[] = selectedAccountIds) => {
    const operationalIds = accountIds.filter((id) => !accounts.find((account) => account.id === id)?.is_sold);
    if (!operationalIds.length) throw new Error('Không có account đang hoạt động để đồng bộ. Nhóm ĐÃ BÁN luôn bị bỏ qua.');
    const response = await fetch('http://127.0.0.1:9000/api/v1/tasks/sync-analytics', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ account_ids: operationalIds, concurrency_limit: Math.min(16, Math.max(8, concurrency * 2)) }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || 'Không thể bắt đầu đồng bộ hiệu suất.');
    useAppStore.getState().addLog({ time: new Date().toLocaleTimeString(), username: 'System', message: result.message });
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

  const selectedAnalyticsCount = selectedAccountIds.filter(
    (id) => !accounts.find((account) => account.id === id)?.is_sold,
  ).length;

  return (
    <div className="min-h-screen bg-canvas text-fg flex">

      {/* SIDEBAR TRÁI CỐ ĐỊNH: điều hướng + trạng thái hệ thống */}
      <NavSidebar activeTab={activeTab} setActiveTab={setActiveTab} isGloballyPaused={isGloballyPaused} />

      {/* KHU LÀM VIỆC CHÍNH (cuộn độc lập với sidebar) */}
      <main className="flex-1 min-w-0 h-screen overflow-y-auto flex flex-col gap-3 px-4 py-4 md:px-5">

      {/* CONTROL PANEL COMPONENT (Chứa nút chọn thư mục ảnh cao cấp) */}
      {activeTab !== 'videos' && <ControlPanel
        proxyMode={proxyMode}
        concurrency={concurrency}
        setConcurrency={handleSetProxyConcurrency}
        avatarFolder={avatarFolder}
        setAvatarFolder={setAvatarFolder}
        isGloballyPaused={isGloballyPaused}
        onGlobalStart={handleGlobalStart}
        onGlobalPause={handleGlobalPause}
        onGlobalResume={handleGlobalResume}
        onGlobalStop={handleGlobalStop}
        selectedAccountIds={selectedAccountIds}
      />}

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
                <Sidebar activeTab="proxies" loading={loading} onFileUpload={handleFileUpload} />
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
          {activeTab !== 'videos' && activeTab !== 'screens' && <StatsCards accounts={accounts} proxies={proxies} />}

          {activeTab === 'accounts' ? (
            <div className="flex flex-col gap-4 min-h-[450px]">
              <AccountPerformanceSummary accounts={filteredAccounts} />
              {/* THANH ĐIỀU KHIỂN & BỘ LỌC TRẠNG THÁI - LUÔN HIỂN THỊ, KHÔNG CÒN
                  BẮT BUỘC CHỌN LÔ MỚI THẤY BẢNG (view database đầy đủ mặc định) */}
              <div className="card p-4 flex flex-col gap-3">
                <div className="flex items-center justify-between border-b border-line-soft pb-2.5 gap-3 flex-wrap">
                  <div className="text-xs text-fg font-semibold flex items-center gap-2">
                    <Folder className="w-4 h-4 text-brand" />
                    {selectedCountry && selectedBatch ? (
                      <>
                        <span className="text-fg-muted">Lô đang xem:</span>
                        <span className="badge bg-brand/10 text-brand border border-brand/25">{selectedCountry}</span>
                        <span className="text-fg font-mono font-bold">{selectedBatch}</span>
                      </>
                    ) : (
                      <span className="text-fg-muted">Toàn bộ tài khoản</span>
                    )}
                    <span className="badge bg-white/5 text-fg-subtle">{filteredAccounts.length} acc</span>
                  </div>
                  <div className="flex items-center gap-2">
                    {isTreeCollapsed && (
                      <button onClick={() => setIsTreeCollapsed(false)} className="btn btn-sm btn-ghost">
                        <ListTree className="w-3.5 h-3.5" /> Cây thư mục
                      </button>
                    )}
                    {selectedCountry && selectedBatch && (
                      <button onClick={() => { setSelectedCountry(null); setSelectedBatch(null); }} className="btn btn-sm btn-danger">
                        <X className="w-3.5 h-3.5" /> Bỏ lọc lô
                      </button>
                    )}
                  </div>
                </div>

                <div className="flex flex-wrap gap-3 items-center justify-between">
                  <div className="flex items-center gap-2">
                    <span className="text-[10px] text-fg-subtle font-bold uppercase tracking-wider">Lọc nhanh</span>
                    <div className="flex gap-1 flex-wrap">
                      {['ALL', 'IDLE', 'RUNNING', 'QUEUED', 'SUCCESS', 'ERROR'].map((status) => {
                        const count = status === 'ALL' ? treeFilteredAccounts.length : treeFilteredAccounts.filter(a => a.status === status).length;
                        const active = statusFilter === status;
                        return (
                          <button
                            key={status}
                            onClick={() => setStatusFilter(status)}
                            className={`px-2.5 py-1 text-[10px] font-bold rounded-md border uppercase tracking-wide transition-colors duration-150 cursor-pointer ${
                              active ? 'bg-brand/15 text-brand border-brand/35' : 'bg-surface-2 text-fg-muted border-line-soft hover:text-fg'
                            }`}
                          >
                            {status} <span className="tabular-nums opacity-70">{count}</span>
                          </button>
                        );
                      })}
                    </div>
                  </div>

                  <div className="flex flex-wrap gap-1.5">
                    <button onClick={handleSelectUnupdatedProfiles} className="btn btn-sm bg-violet-500/10 text-violet-300 border border-violet-500/25 hover:bg-violet-500/20">
                      <Zap className="w-3.5 h-3.5" /> Chưa đổi Profile
                    </button>
                    <button onClick={handleSelectAllBanned} className="btn btn-sm bg-rose-500/10 text-rose-400 border border-rose-500/25 hover:bg-rose-500/20">
                      <Ban className="w-3.5 h-3.5" /> Chọn Banned
                    </button>
                    <button onClick={() => setIsExportModalOpen(true)} className="btn btn-sm bg-brand/10 text-brand border border-brand/25 hover:bg-brand/20">
                      <Download className="w-3.5 h-3.5" /> Xuất acc
                    </button>
                    <button onClick={handleCopyAccounts} className="btn btn-sm bg-violet-500/10 text-violet-300 border border-violet-500/25 hover:bg-violet-500/20"
                      title="Copy acc (đã chọn, hoặc cả lô đang hiện) ra clipboard — KHÔNG xóa khỏi app">
                      <Copy className="w-3.5 h-3.5" /> Copy acc{selectedAccountIds.length > 0 ? ` (${selectedAccountIds.length})` : ''}
                    </button>
                    {selectedAccountIds.length > 0 && (
                      <button onClick={() => { void handleSyncAnalytics().catch((reason) => alert(reason instanceof Error ? reason.message : 'Không thể đồng bộ hiệu suất TikTok.')); }} disabled={!selectedAnalyticsCount} className="btn btn-sm bg-sky-500/10 text-sky-300 border border-sky-500/25 hover:bg-sky-500/20 disabled:cursor-not-allowed disabled:opacity-40" title="Profile dùng HTTP công khai; chi tiết video dùng một browser ẩn dùng chung khi cần; không OAuth, tự bỏ qua ĐÃ BÁN">
                        <BarChart3 className="w-3.5 h-3.5" /> Đồng bộ nhanh ({selectedAnalyticsCount})
                      </button>
                    )}
                    {selectedAccountIds.length > 0 && (
                      <button onClick={handleMoveToGroup} className="btn btn-sm bg-indigo-500/10 text-indigo-300 border border-indigo-500/25 hover:bg-indigo-500/20"
                        title="Chuyển các tài khoản đã chọn sang 1 cụm (Lô) mới hoặc có sẵn để theo dõi">
                        <FolderInput className="w-3.5 h-3.5" /> Chuyển cụm ({selectedAccountIds.length})
                      </button>
                    )}
                    {selectedAccountIds.length > 0 && (
                      <button onClick={handleClearCookies} className="btn btn-sm bg-amber-500/10 text-amber-400 border border-amber-500/25 hover:bg-amber-500/20">
                        <Cookie className="w-3.5 h-3.5" /> Xóa cookies ({selectedAccountIds.length})
                      </button>
                    )}
                    {selectedAccountIds.length > 0 && (
                      <button onClick={handleBulkDelete} className="btn btn-sm btn-danger">
                        <Trash2 className="w-3.5 h-3.5" /> Xóa ({selectedAccountIds.length})
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
                onUpdateAccount={handleUpdateAccount}
                onRunOneTest={handleManualSessionStart}
                onStopRunOneTest={handleManualSessionStop}
                manualSessionIds={manualSessionIds}
                onSyncAnalytics={handleSyncAnalytics}
              />
            </div>
          ) : activeTab === 'videos' ? (
            <Suspense fallback={<div className="card min-h-96 animate-pulse bg-surface" aria-label="Đang tải quản lý video" />}>
              <VideoManager accounts={accounts} selectedAccountIds={selectedAccountIds} onSelectedAccountIdsChange={setSelectedAccountIds} concurrency={concurrency} />
            </Suspense>
          ) : activeTab === 'interactions' ? (
            <InteractionPanel accounts={accounts} selectedAccountIds={selectedAccountIds} />
          ) : activeTab === 'screens' ? (
            <Suspense fallback={<div className="card min-h-96 animate-pulse bg-surface" aria-label="Đang tải màn hình trực tiếp" />}>
              <LiveScreens accounts={accounts} />
            </Suspense>
          ) : (
            <ProxiesTable proxies={proxies} />
          )}

        </div>
      </div>

      {/* 7. TERMINAL CONSOLE COMPONENT */}
      {activeTab === 'screens' && <TerminalConsole />}

      </main>

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
          proxyMode={proxyMode}
          onSetProxyMode={handleSetProxyMode}
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

      {/* MODAL XUẤT ACC RA FILE TXT */}
      <ExportModal
        isOpen={isExportModalOpen}
        onClose={() => setIsExportModalOpen(false)}
        selectedAccountIds={selectedAccountIds}
        displayedAccountIds={filteredAccounts.map((a) => a.id)}
        onExported={loadData}
      />

      <TaskCompletionPopup
        notices={taskCompletionNotices}
        onDismiss={dismissTaskCompletionNotice}
      />

    </div>
  );
}
