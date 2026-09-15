# Evidence Context: Commercial Client Protection

This is a derived architecture review. It does not claim that the proposed controls have been implemented or validated.

## Source identity

- Source root: `D:\\tiktok_auto`
- Target revision: `ee06783a2a2f6ce38945a8845610fc72b0ad7e57`
- Selected source files match that revision.
- Repository-wide drift: present. Unrelated runtime/test-artifact changes were not used as application-source evidence.
- Evidence collection SHA-256: `ea0c32f81c0e07683e2e0629ea27efc77c1b8744e82c316bb959dfa81e88b29e`

## Evidence inventory

| Evidence | Title | Source | What it establishes |
| --- | --- | --- | --- |
| E001 | Unauthenticated fixed local API | `backend/app/main.py` | Wildcard CORS and privileged routers without global desktop-process authentication. |
| E002 | UI owns local API addressing | `frontend/src/services/api.ts`, `frontend/src/services/websocket.ts`, `frontend/src/App.tsx` | Frontend directly calls stable `127.0.0.1:9000` HTTP/WebSocket endpoints. |
| E003 | Source-owned release secret/debug default | `backend/app/core/config.py` | Debug is enabled by default and a third-party service secret is present in source; its value is intentionally omitted. |
| E004 | No signed desktop release target | `frontend/package.json`, `pyproject.toml`, `docker-compose.yml` | No desktop shell, compiled release, license client, or signed updater is declared. |
| E005 | No caller/entitlement check on task routes | `backend/app/interfaces/api/tasks_router.py` | Privileged task routes resolve repositories/dispatcher without application identity or commercial entitlement. |
| E006 | Runtime credential artifacts tracked | Git index | Cookie and SQLite WAL/SHM artifacts are tracked; their contents were not inspected. |

## Selected file hashes

- `backend/app/main.py`: `e3f8b18fda9bb7c0bfffa4e00fb347131ecaa9edea4e0b7d90e21a1b629e20ce`
- `backend/app/core/config.py`: `95383bed66a869a3cc637b68be393faa59e8e67d0baadb271ecab61107e95a47`
- `backend/app/interfaces/api/tasks_router.py`: `a9e46a74c701624d6bfee80710e5d32c3159741efe31346f72f2e1b8d9f32e96`
- `frontend/src/services/api.ts`: `3554b50bde7f55178d484484c3b6e63d846694d81791e2ab37d347d65559ce95`
- `frontend/src/services/websocket.ts`: `57dbd02e88c994a168c18dcfd01b4101f539a7aea52791521f0a70d03a98dd32`
- `frontend/src/App.tsx`: `702494ff8412113baac769b2b92dddeb3331e626cf8f417f12f9c0713a60fe83`
- `frontend/package.json`: `0c0174711fa5f2a59a0beac4b9e050d2045bb0a9806799598f75a7c0f089297f`
- `pyproject.toml`: `7c070c85afadca5ae098230ae7935a84c39303a2b33ddfa3edd79543aa29db99`
- `docker-compose.yml`: `aacbb30057266ccbf4f89d19cb5dc5cadcfcf560835f27d46100df451ba9434c`

## External design references

- Tauri sidecars: <https://v2.tauri.app/develop/sidecar/>
- Tauri signed updater: <https://v2.tauri.app/plugin/updater/>
- Nuitka Commercial: <https://ssh.nuitka.net/doc/commercial.html>
- Microsoft app signing: <https://learn.microsoft.com/en-us/windows/apps/develop/smart-app-control/code-signing-for-smart-app-control>
- Microsoft DPAPI guidance: <https://learn.microsoft.com/en-us/windows/win32/secbp/handling-passwords>
- Microsoft TPM fundamentals: <https://learn.microsoft.com/en-us/windows/security/hardware-security/tpm/tpm-fundamentals>

## Evidence limitations

This was a focused source review, not a penetration test or complete security scan. Reverse-engineering resistance, installer behavior, license latency, offline recovery, and updater rollback have not been measured. Redistribution rights for bundled browser components and extensions remain to be reviewed.
