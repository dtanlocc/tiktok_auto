import { useAppStore } from '../store/useAppStore';

let socket: WebSocket | null = null;

export const initWebSocket = () => {
  if (socket) return;

  socket = new WebSocket('ws://127.0.0.1:8001/ws');

  socket.onopen = () => {
    console.log('[+] WebSocket Connected to Backend.');
    useAppStore.getState().setWsConnected(true);
  };

  socket.onmessage = (event) => {
    try {
      const message = JSON.parse(event.data);
      console.log('[*] WebSocket Message Received:', message);

      if (message.event === 'ACCOUNT_STATUS_CHANGED') {
        const { id, status } = message.data;
        useAppStore.getState().updateAccountStatus(id, status);
      } else if (message.event === 'ACCOUNT_ADDED') {
        useAppStore.getState().addAccount(message.data);
      }
      else if (message.event === 'ACCOUNT_PROXY_CHANGED') {
        const { id, proxy_id } = message.data;
        useAppStore.getState().updateAccountProxy(id, proxy_id);
      }
    } catch (error) {
      console.error('[-] Lỗi xử lý tin nhắn WebSocket:', error);
    }
  };

  socket.onclose = () => {
    console.log('[-] WebSocket bị ngắt kết nối. Thử lại sau 5 giây...');
    useAppStore.getState().setWsConnected(false);
    socket = null;
    setTimeout(initWebSocket, 5000);
  };
};