import { useAppStore } from '../store/useAppStore';

let socket: WebSocket | null = null;

const getWsUrl = () => {
  if (import.meta.env.DEV) {
    return 'ws://127.0.0.1:8001/ws'; // Dev port
  }
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${protocol}//${window.location.host}/ws`; // Production
};

export const initWebSocket = () => {
  if (socket && (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)) {
    return;
  }

  const wsUrl = getWsUrl();
  socket = new WebSocket(wsUrl);

  socket.onopen = () => {
    console.log('[+] WebSocket uplink established successfully.');
    useAppStore.getState().setWsConnected(true);
  };

  socket.onmessage = (event) => {
    try {
      const message = JSON.parse(event.data);
      const store = useAppStore.getState();
      
      if (message.event === 'ACCOUNT_STATUS_CHANGED') {
        const { id, status, current_step } = message.data;
        store.updateAccountStatus(id, status, current_step);
      } 
      else if (message.event === 'ACCOUNT_ADDED') {
        store.addAccount(message.data);
      }
      else if (message.event === 'TASK_STEP_UPDATED') {
        const { id, current_step } = message.data;
        store.updateAccountStep(id, current_step);
      }
      else if (message.event === 'ACCOUNT_PROXY_CHANGED') {
        const { id, proxy_id } = message.data;
        store.updateAccountProxy(id, proxy_id);
      }
      else if (message.event === 'TERMINAL_LOG') {
        const { username, message: logMsg } = message.data;
        store.addLog({
          time: new Date().toLocaleTimeString('en-US', { hour12: false }),
          username,
          message: logMsg
        });
      }
    } catch (err) {
      console.error('[-] Error processing WebSocket frame:', err);
    }
  };

  socket.onclose = () => {
    console.log('[-] WebSocket closed. Auto-reconnecting in 5s...');
    useAppStore.getState().setWsConnected(false);
    socket = null;
    setTimeout(initWebSocket, 5000);
  };
};