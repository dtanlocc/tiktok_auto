import { invoke } from '@tauri-apps/api/core';
import { listen, type UnlistenFn } from '@tauri-apps/api/event';

const LOCAL_BACKEND = 'http://127.0.0.1:9000';

type NativeBackendResponse = {
  status: number;
  contentType?: string | null;
  bodyBase64: string;
};

export const isTauriRuntime = (): boolean => '__TAURI_INTERNALS__' in window;

const bytesToBase64 = (bytes: Uint8Array): string => {
  let binary = '';
  const chunkSize = 0x8000;
  for (let offset = 0; offset < bytes.length; offset += chunkSize) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + chunkSize));
  }
  return btoa(binary);
};

const base64ToBytes = (value: string): Uint8Array => {
  const binary = atob(value);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index);
  }
  return bytes;
};

let installed = false;

export const installSecureTransport = (): void => {
  if (installed || !isTauriRuntime()) return;
  installed = true;
  const browserFetch = window.fetch.bind(window);

  window.fetch = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const original = new Request(input, init);
    const url = new URL(original.url, window.location.href);
    if (url.origin !== LOCAL_BACKEND) {
      return browserFetch(original);
    }
    const body = ['GET', 'HEAD'].includes(original.method)
      ? new Uint8Array()
      : new Uint8Array(await original.arrayBuffer());
    const native = await invoke<NativeBackendResponse>('backend_request', {
      request: {
        method: original.method,
        path: url.pathname,
        query: url.search.slice(1) || null,
        contentType: original.headers.get('content-type'),
        bodyBase64: body.length > 0 ? bytesToBase64(body) : null,
      },
    });
    const headers = new Headers();
    if (native.contentType) headers.set('content-type', native.contentType);
    return new Response(base64ToBytes(native.bodyBase64), {
      status: native.status,
      headers,
    });
  };
};

export const listenBackendMessages = async (
  channel: 'events' | 'screens',
  handler: (message: MessageEvent<string>) => void,
): Promise<UnlistenFn> => {
  const eventName = channel === 'events' ? 'backend-ws-message' : 'backend-screen-message';
  return listen<string>(eventName, (event) => {
    handler(new MessageEvent('message', { data: event.payload }));
  });
};
