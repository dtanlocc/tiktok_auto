use std::{collections::BTreeMap, path::PathBuf, process::Stdio, sync::Arc, time::Duration};

use base64::{engine::general_purpose, Engine as _};
use ed25519_dalek::{
    pkcs8::{DecodePublicKey, EncodePublicKey},
    Signature, Signer, SigningKey, Verifier, VerifyingKey,
};
use futures_util::StreamExt;
use hmac::{Hmac, Mac};
use keyring::Entry;
use pkcs8::LineEnding;
use rand::{rngs::OsRng, RngCore};
use reqwest::{header, Client, Method, StatusCode};
use semver::Version;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use sha2::{Digest, Sha256};
use tauri::{AppHandle, Emitter, Manager, State};
use tokio::{
    fs,
    io::AsyncWriteExt,
    process::{Child, Command},
    sync::Mutex,
    time::sleep,
};
use tokio_tungstenite::{connect_async, tungstenite::client::IntoClientRequest};
use url::Url;
use uuid::Uuid;

type HmacSha256 = Hmac<Sha256>;

const KEYRING_SERVICE: &str = "com.tiktokauto.desktop";
const KEYRING_DEVICE_KEY: &str = "device-ed25519-v1";
const KEYRING_CAPTCHA_KEY: &str = "customer-captcha-api-key-v1";
const FRIENDS_BACKEND_FILENAME: &str = "TikTokAuto-Backend.exe";
const MAX_HTTP_BODY: usize = 32 * 1024 * 1024;
const MAX_RESPONSE_BODY: usize = 64 * 1024 * 1024;

#[derive(Default)]
struct BackendRuntime {
    child: Option<Child>,
    port: Option<u16>,
    session_secret: Option<Vec<u8>>,
}

struct SecurityState {
    control_http: Client,
    local_http: Client,
    backend: Arc<Mutex<BackendRuntime>>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct SecurityStatus {
    configured: bool,
    activated: bool,
    backend_installed: bool,
    backend_running: bool,
    captcha_configured: bool,
    device_id: String,
    lease_expires_at: Option<i64>,
    message: String,
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct LeaseClaims {
    protocol_version: u8,
    license_id: String,
    device_id: String,
    plan: String,
    features: Vec<String>,
    max_accounts: u64,
    max_tabs: u64,
    channel: String,
    minimum_version: String,
    minimum_backend_version: String,
    issued_at: i64,
    not_before: i64,
    expires_at: i64,
    jti: String,
}

#[derive(Debug, Deserialize, Serialize, Clone)]
#[serde(deny_unknown_fields)]
struct ReleaseManifest {
    protocol_version: u8,
    artifact_id: String,
    component: String,
    version: String,
    channel: String,
    target: String,
    download_url: String,
    sha256: String,
    size_bytes: u64,
    published_at: i64,
    minimum_launcher_version: String,
    mandatory: bool,
}

#[derive(Debug, Deserialize)]
struct LeaseResponse {
    lease: String,
    expires_at: i64,
    renew_after: i64,
}

#[derive(Debug, Deserialize)]
struct ReleaseCheckResponse {
    update_available: bool,
    signed_manifest: Option<String>,
    download_grant: Option<String>,
    grant_expires_at: Option<i64>,
}

#[derive(Debug, Serialize, Deserialize)]
struct BackendPointer {
    version: String,
    filename: String,
    sha256: String,
    size_bytes: u64,
    signed_manifest: String,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct UpdateResult {
    updated: bool,
    version: Option<String>,
    mandatory: bool,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct BackendRequest {
    method: String,
    path: String,
    query: Option<String>,
    content_type: Option<String>,
    body_base64: Option<String>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct BackendResponse {
    status: u16,
    content_type: Option<String>,
    body_base64: String,
}

fn required_build_value(value: Option<&'static str>, name: &str) -> Result<&'static str, String> {
    value.filter(|item| !item.trim().is_empty()).ok_or_else(|| {
        format!("Bản desktop chưa được cấu hình {name}; bản phát hành phải được build qua release pipeline.")
    })
}

fn control_plane_url() -> Result<&'static str, String> {
    let value = required_build_value(option_env!("TKAUTO_CONTROL_PLANE_URL"), "control-plane URL")?;
    let parsed = Url::parse(value)
        .map_err(|_| "Control-plane URL nhúng trong ứng dụng không hợp lệ.".to_string())?;
    if parsed.scheme() != "https"
        || parsed.host_str().is_none()
        || !parsed.username().is_empty()
        || parsed.password().is_some()
        || parsed.query().is_some()
        || parsed.fragment().is_some()
    {
        return Err(
            "Control-plane URL phải là HTTPS và không chứa credential/query/fragment.".to_string(),
        );
    }
    Ok(value)
}

fn app_version() -> &'static str {
    option_env!("TKAUTO_APP_VERSION").unwrap_or(env!("CARGO_PKG_VERSION"))
}

fn friends_build() -> bool {
    option_env!("TKAUTO_FRIEND_BUILD") == Some("1")
}

fn friends_backend_path() -> Result<PathBuf, String> {
    let executable = std::env::current_exe()
        .map_err(|_| "Không xác định được thư mục bản Friends.".to_string())?;
    let parent = executable
        .parent()
        .ok_or_else(|| "Thư mục bản Friends không hợp lệ.".to_string())?;
    Ok(parent.join(FRIENDS_BACKEND_FILENAME))
}

fn license_public_keys() -> Result<&'static str, String> {
    required_build_value(
        option_env!("TKAUTO_LICENSE_PUBLIC_KEYS_JSON"),
        "license public keys",
    )
}

fn release_public_keys() -> Result<&'static str, String> {
    required_build_value(
        option_env!("TKAUTO_RELEASE_PUBLIC_KEYS_JSON"),
        "release public keys",
    )
}

fn app_data_dir(app: &AppHandle) -> Result<PathBuf, String> {
    app.path()
        .app_local_data_dir()
        .map_err(|_| "Không xác định được thư mục dữ liệu ứng dụng.".to_string())
}

fn lease_path(app: &AppHandle) -> Result<PathBuf, String> {
    Ok(app_data_dir(app)?.join("license").join("current.lease"))
}

fn backend_pointer_path(app: &AppHandle) -> Result<PathBuf, String> {
    Ok(app_data_dir(app)?
        .join("artifacts")
        .join("backend-current.json"))
}

fn b64url(bytes: &[u8]) -> String {
    general_purpose::URL_SAFE_NO_PAD.encode(bytes)
}

fn device_key() -> Result<SigningKey, String> {
    let entry = Entry::new(KEYRING_SERVICE, KEYRING_DEVICE_KEY)
        .map_err(|_| "Không mở được kho khóa Windows.".to_string())?;
    match entry.get_password() {
        Ok(encoded) => {
            let raw = general_purpose::STANDARD.decode(encoded).map_err(|_| {
                "Khóa thiết bị trong Windows Credential Manager bị hỏng.".to_string()
            })?;
            let bytes: [u8; 32] = raw
                .try_into()
                .map_err(|_| "Khóa thiết bị có độ dài không hợp lệ.".to_string())?;
            Ok(SigningKey::from_bytes(&bytes))
        }
        Err(keyring::Error::NoEntry) => {
            let key = SigningKey::generate(&mut OsRng);
            entry
                .set_password(&general_purpose::STANDARD.encode(key.to_bytes()))
                .map_err(|_| "Không lưu được khóa thiết bị bằng bảo vệ Windows.".to_string())?;
            Ok(key)
        }
        Err(_) => Err("Không đọc được khóa thiết bị từ Windows Credential Manager.".to_string()),
    }
}

fn captcha_api_key() -> Result<Option<String>, String> {
    let entry = Entry::new(KEYRING_SERVICE, KEYRING_CAPTCHA_KEY)
        .map_err(|_| "Không mở được kho khóa CAPTCHA trong Windows.".to_string())?;
    match entry.get_password() {
        Ok(value) => {
            let value = value.trim().to_string();
            if !(8..=512).contains(&value.len())
                || value.chars().any(|character| character.is_control())
            {
                return Err(
                    "Khóa CAPTCHA trong Windows Credential Manager không hợp lệ.".to_string(),
                );
            }
            Ok(Some(value))
        }
        Err(keyring::Error::NoEntry) => Ok(None),
        Err(_) => Err("Không đọc được khóa CAPTCHA từ Windows Credential Manager.".to_string()),
    }
}

#[tauri::command]
fn set_captcha_api_key(api_key: String) -> Result<(), String> {
    let value = api_key.trim();
    if !(8..=512).contains(&value.len()) || value.chars().any(|character| character.is_control()) {
        return Err(
            "Khóa CAPTCHA phải dài 8-512 ký tự và không chứa ký tự điều khiển.".to_string(),
        );
    }
    Entry::new(KEYRING_SERVICE, KEYRING_CAPTCHA_KEY)
        .map_err(|_| "Không mở được kho khóa CAPTCHA trong Windows.".to_string())?
        .set_password(value)
        .map_err(|_| "Không lưu được khóa CAPTCHA bằng bảo vệ Windows.".to_string())
}

#[tauri::command]
fn clear_captcha_api_key() -> Result<(), String> {
    let entry = Entry::new(KEYRING_SERVICE, KEYRING_CAPTCHA_KEY)
        .map_err(|_| "Không mở được kho khóa CAPTCHA trong Windows.".to_string())?;
    match entry.delete_credential() {
        Ok(()) | Err(keyring::Error::NoEntry) => Ok(()),
        Err(_) => Err("Không xóa được khóa CAPTCHA trong Windows Credential Manager.".to_string()),
    }
}

fn device_id(key: &SigningKey) -> String {
    let digest = Sha256::digest(key.verifying_key().as_bytes());
    format!("device_{}", hex::encode(&digest[..20]))
}

fn device_proof(key: &SigningKey, action: &str, app_version: &str) -> (i64, String, String) {
    let issued_at = chrono_now();
    let mut nonce_bytes = [0_u8; 18];
    OsRng.fill_bytes(&mut nonce_bytes);
    let nonce = b64url(&nonce_bytes);
    let canonical = format!(
        "TKAUTO-{action}-v1\n{}\n{issued_at}\n{nonce}\n{app_version}",
        device_id(key)
    );
    let signature = key.sign(canonical.as_bytes());
    (issued_at, nonce, b64url(&signature.to_bytes()))
}

fn chrono_now() -> i64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs() as i64
}

fn verify_compact_token(
    token: &str,
    expected_type: &str,
    keys_json: &str,
) -> Result<Value, String> {
    if token.is_empty() || token.len() > 64 * 1024 {
        return Err("Token ký số rỗng hoặc quá lớn.".to_string());
    }
    let parts: Vec<&str> = token.split('.').collect();
    if parts.len() != 3 {
        return Err("Token ký số không đúng định dạng.".to_string());
    }
    let header_bytes = general_purpose::URL_SAFE_NO_PAD
        .decode(parts[0])
        .map_err(|_| "Header token không hợp lệ.".to_string())?;
    let header_value: Value = serde_json::from_slice(&header_bytes)
        .map_err(|_| "Header token không phải JSON hợp lệ.".to_string())?;
    let header = header_value
        .as_object()
        .ok_or_else(|| "Header token không phải object.".to_string())?;
    let expected_fields = ["alg", "kid", "typ", "v"];
    if header.len() != expected_fields.len()
        || expected_fields
            .iter()
            .any(|field| !header.contains_key(*field))
        || header.get("alg").and_then(Value::as_str) != Some("EdDSA")
        || header.get("typ").and_then(Value::as_str) != Some(expected_type)
        || header.get("v").and_then(Value::as_u64) != Some(1)
    {
        return Err("Header hoặc thuật toán ký số không được chấp nhận.".to_string());
    }
    let key_id = header
        .get("kid")
        .and_then(Value::as_str)
        .ok_or_else(|| "Token thiếu key ID.".to_string())?;
    let keys: BTreeMap<String, String> = serde_json::from_str(keys_json)
        .map_err(|_| "Bộ public key nhúng trong ứng dụng không hợp lệ.".to_string())?;
    let pem = keys
        .get(key_id)
        .ok_or_else(|| "Token dùng key ID không được tin cậy.".to_string())?;
    let verifying_key = VerifyingKey::from_public_key_pem(pem)
        .map_err(|_| "Public key nhúng trong ứng dụng không hợp lệ.".to_string())?;
    let signature_bytes = general_purpose::URL_SAFE_NO_PAD
        .decode(parts[2])
        .map_err(|_| "Chữ ký token không hợp lệ.".to_string())?;
    let signature = Signature::from_slice(&signature_bytes)
        .map_err(|_| "Chữ ký token sai độ dài.".to_string())?;
    verifying_key
        .verify(format!("{}.{}", parts[0], parts[1]).as_bytes(), &signature)
        .map_err(|_| "Chữ ký token không hợp lệ.".to_string())?;
    let body = general_purpose::URL_SAFE_NO_PAD
        .decode(parts[1])
        .map_err(|_| "Payload token không hợp lệ.".to_string())?;
    serde_json::from_slice(&body).map_err(|_| "Payload token không phải JSON hợp lệ.".to_string())
}

fn validate_lease(token: &str, key: &SigningKey) -> Result<LeaseClaims, String> {
    let claims = trusted_lease_claims(token, key)?;
    let now = chrono_now();
    if now + 30 < claims.not_before || now - 30 >= claims.expires_at {
        return Err("Lease is outside its server-issued validity window.".to_string());
    }
    Ok(claims)
}

fn trusted_lease_claims(token: &str, key: &SigningKey) -> Result<LeaseClaims, String> {
    let claims: LeaseClaims = serde_json::from_value(verify_compact_token(
        token,
        "TKAUTO-LEASE",
        license_public_keys()?,
    )?)
    .map_err(|_| "Lease có trường dữ liệu không hợp lệ.".to_string())?;
    if claims.protocol_version != 1
        || claims.device_id != device_id(key)
        || !claims.features.iter().any(|feature| feature == "app.start")
    {
        return Err("Lease không còn hợp lệ cho thiết bị này.".to_string());
    }
    let current = Version::parse(app_version())
        .map_err(|_| "Phiên bản launcher không hợp lệ.".to_string())?;
    let minimum = Version::parse(&claims.minimum_version)
        .map_err(|_| "Minimum version trong lease không hợp lệ.".to_string())?;
    Version::parse(&claims.minimum_backend_version)
        .map_err(|_| "Minimum backend version trong lease không hợp lệ.".to_string())?;
    if current < minimum {
        return Err("Ứng dụng quá cũ và bắt buộc phải cập nhật.".to_string());
    }
    Ok(claims)
}

async fn read_lease(app: &AppHandle, key: &SigningKey) -> Result<(String, LeaseClaims), String> {
    let token = fs::read_to_string(lease_path(app)?)
        .await
        .map_err(|_| "Thiết bị chưa được kích hoạt hoặc lease không tồn tại.".to_string())?;
    let token = token.trim().to_string();
    let claims = validate_lease(&token, key)?;
    Ok((token, claims))
}

async fn replace_file(path: PathBuf, contents: &[u8]) -> Result<(), String> {
    let parent = path
        .parent()
        .ok_or_else(|| "Đường dẫn dữ liệu không hợp lệ.".to_string())?;
    fs::create_dir_all(parent)
        .await
        .map_err(|_| "Không tạo được thư mục dữ liệu bảo mật.".to_string())?;
    let temporary = parent.join(format!(".{}.tmp", Uuid::new_v4()));
    let mut options = fs::OpenOptions::new();
    options.create_new(true).write(true);
    let mut file = options
        .open(&temporary)
        .await
        .map_err(|_| "Không tạo được file tạm an toàn.".to_string())?;
    file.write_all(contents)
        .await
        .map_err(|_| "Không ghi được dữ liệu an toàn.".to_string())?;
    file.flush()
        .await
        .map_err(|_| "Không flush được dữ liệu an toàn.".to_string())?;
    drop(file);
    if fs::metadata(&path).await.is_ok() {
        fs::remove_file(&path)
            .await
            .map_err(|_| "Không thay thế được file dữ liệu hiện tại.".to_string())?;
    }
    fs::rename(&temporary, &path)
        .await
        .map_err(|_| "Không hoàn tất được thao tác ghi nguyên tử.".to_string())
}

async fn purge_backend_cache(app: &AppHandle) {
    let Ok(root) = app_data_dir(app).map(|path| path.join("artifacts")) else {
        return;
    };
    let Ok(mut entries) = fs::read_dir(&root).await else {
        return;
    };
    while let Ok(Some(entry)) = entries.next_entry().await {
        let name = entry.file_name().to_string_lossy().to_ascii_lowercase();
        if name.ends_with(".exe") || name == "backend-current.json" {
            let _ = fs::remove_file(entry.path()).await;
        }
    }
}

async fn stop_backend_runtime(backend: &Arc<Mutex<BackendRuntime>>) {
    let child = {
        let mut runtime = backend.lock().await;
        runtime.port = None;
        runtime.session_secret = None;
        runtime.child.take()
    };
    if let Some(mut child) = child {
        let _ = child.kill().await;
    }
}

fn is_authoritative_entitlement_denial(status: StatusCode) -> bool {
    matches!(status, StatusCode::FORBIDDEN | StatusCode::UPGRADE_REQUIRED)
}

async fn invalidate_local_entitlement(app: &AppHandle, backend: &Arc<Mutex<BackendRuntime>>) {
    stop_backend_runtime(backend).await;
    purge_backend_cache(app).await;
    if let Ok(path) = lease_path(app) {
        let _ = fs::remove_file(path).await;
    }
}

fn spawn_entitlement_monitor(app: AppHandle, backend: Arc<Mutex<BackendRuntime>>) {
    tauri::async_runtime::spawn(async move {
        loop {
            sleep(Duration::from_secs(60)).await;
            let lease_valid = match device_key() {
                Ok(key) => read_lease(&app, &key).await.is_ok(),
                Err(_) => false,
            };
            if !lease_valid {
                stop_backend_runtime(&backend).await;
                purge_backend_cache(&app).await;
            }
        }
    });
}

#[tauri::command]
async fn security_status(
    app: AppHandle,
    state: State<'_, SecurityState>,
) -> Result<SecurityStatus, String> {
    if friends_build() {
        let backend_installed = trusted_friends_backend().await.is_ok();
        let backend_running = {
            let mut runtime = state.backend.lock().await;
            let running = match runtime.child.as_mut() {
                Some(child) => child.try_wait().is_ok_and(|status| status.is_none()),
                None => false,
            };
            if !running {
                runtime.child = None;
                runtime.port = None;
                runtime.session_secret = None;
            }
            running
        };
        return Ok(SecurityStatus {
            configured: true,
            activated: true,
            backend_installed,
            backend_running,
            captcha_configured: captcha_api_key()?.is_some(),
            device_id: "friends-build".to_string(),
            lease_expires_at: None,
            message: "Bản Friends không yêu cầu license key.".to_string(),
        });
    }
    let configured = control_plane_url().is_ok()
        && license_public_keys().is_ok()
        && release_public_keys().is_ok();
    let key = device_key()?;
    let id = device_id(&key);
    let lease_result = read_lease(&app, &key).await;
    if lease_result.is_err() {
        stop_backend_runtime(&state.backend).await;
        purge_backend_cache(&app).await;
    }
    let backend_installed = match lease_result.as_ref() {
        Ok((_, claims)) => trusted_installed_backend(&app, claims).await.is_ok(),
        Err(_) => false,
    };
    let backend_running = {
        let mut runtime = state.backend.lock().await;
        let running = match runtime.child.as_mut() {
            Some(child) => child.try_wait().is_ok_and(|status| status.is_none()),
            None => false,
        };
        if !running {
            runtime.child = None;
            runtime.port = None;
            runtime.session_secret = None;
        }
        running
    };
    let captcha_configured = captcha_api_key()?.is_some();
    let expires = lease_result
        .as_ref()
        .ok()
        .map(|(_, claims)| claims.expires_at);
    Ok(SecurityStatus {
        configured,
        activated: lease_result.is_ok(),
        backend_installed,
        backend_running,
        captcha_configured,
        device_id: id,
        lease_expires_at: expires,
        message: lease_result
            .err()
            .unwrap_or_else(|| "Thiết bị đã có lease hợp lệ.".to_string()),
    })
}

#[tauri::command]
async fn activate_license(
    app: AppHandle,
    state: State<'_, SecurityState>,
    license_key: String,
) -> Result<SecurityStatus, String> {
    if friends_build() {
        return security_status(app, state).await;
    }
    if license_key.len() < 20 || license_key.len() > 128 {
        return Err("License key không đúng định dạng.".to_string());
    }
    let key = device_key()?;
    let version = app_version();
    let (issued_at, nonce, signature) = device_proof(&key, "ACTIVATE", version);
    let public_key = key
        .verifying_key()
        .to_public_key_pem(LineEnding::LF)
        .map_err(|_| "Không xuất được device public key.".to_string())?;
    let request = serde_json::json!({
        "device_id": device_id(&key),
        "issued_at": issued_at,
        "nonce": nonce,
        "signature": signature,
        "app_version": version,
        "license_key": license_key,
        "device_public_key_pem": public_key,
    });
    let response = state
        .control_http
        .post(format!(
            "{}/v1/activate",
            control_plane_url()?.trim_end_matches('/')
        ))
        .json(&request)
        .send()
        .await
        .map_err(|_| "Không kết nối được máy chủ license.".to_string())?;
    if !response.status().is_success() {
        return Err(format!(
            "Máy chủ từ chối kích hoạt (HTTP {}).",
            response.status().as_u16()
        ));
    }
    let response: LeaseResponse = response
        .json()
        .await
        .map_err(|_| "Phản hồi kích hoạt không hợp lệ.".to_string())?;
    let claims = validate_lease(&response.lease, &key)?;
    if response.expires_at != claims.expires_at || response.renew_after >= response.expires_at {
        return Err("Thời hạn lease trong phản hồi không nhất quán.".to_string());
    }
    replace_file(lease_path(&app)?, response.lease.as_bytes()).await?;
    security_status(app, state).await
}

#[tauri::command]
async fn renew_license(
    app: AppHandle,
    state: State<'_, SecurityState>,
) -> Result<SecurityStatus, String> {
    if friends_build() {
        return security_status(app, state).await;
    }
    let key = device_key()?;
    let token = fs::read_to_string(lease_path(&app)?)
        .await
        .map_err(|_| "No previous lease exists; enter the license key once.".to_string())?;
    let current_claims = trusted_lease_claims(token.trim(), &key)?;
    let version = app_version();
    let (issued_at, nonce, signature) = device_proof(&key, "RENEW", version);
    let request = serde_json::json!({
        "device_id": device_id(&key),
        "issued_at": issued_at,
        "nonce": nonce,
        "signature": signature,
        "app_version": version,
        "license_id": current_claims.license_id,
    });
    let response = state
        .control_http
        .post(format!(
            "{}/v1/lease/renew",
            control_plane_url()?.trim_end_matches('/')
        ))
        .json(&request)
        .send()
        .await
        .map_err(|_| "Could not connect to the license server for renewal.".to_string())?;
    let response_status = response.status();
    if !response_status.is_success() {
        if is_authoritative_entitlement_denial(response_status) {
            invalidate_local_entitlement(&app, &state.backend).await;
        }
        return Err(format!(
            "The server denied lease renewal (HTTP {}).",
            response_status.as_u16()
        ));
    }
    let renewed: LeaseResponse = response
        .json()
        .await
        .map_err(|_| "The lease renewal response is invalid.".to_string())?;
    let claims = validate_lease(&renewed.lease, &key)?;
    if claims.license_id != current_claims.license_id
        || renewed.expires_at != claims.expires_at
        || renewed.renew_after >= renewed.expires_at
    {
        return Err("The renewed lease is inconsistent with this activation.".to_string());
    }
    replace_file(lease_path(&app)?, renewed.lease.as_bytes()).await?;
    security_status(app, state).await
}

async fn load_backend_pointer(app: &AppHandle) -> Result<BackendPointer, String> {
    let raw = fs::read(backend_pointer_path(app)?)
        .await
        .map_err(|_| "Backend binary chưa được cài.".to_string())?;
    serde_json::from_slice(&raw).map_err(|_| "Metadata backend local bị hỏng.".to_string())
}

fn validate_backend_manifest(
    signed_manifest: &str,
    claims: &LeaseClaims,
) -> Result<ReleaseManifest, String> {
    let manifest: ReleaseManifest = serde_json::from_value(verify_compact_token(
        signed_manifest,
        "TKAUTO-RELEASE",
        release_public_keys()?,
    )?)
    .map_err(|_| "Manifest backend có trường dữ liệu không hợp lệ.".to_string())?;
    let manifest_version = Version::parse(&manifest.version)
        .map_err(|_| "Phiên bản backend trong manifest không hợp lệ.".to_string())?;
    let minimum_backend_version = Version::parse(&claims.minimum_backend_version)
        .map_err(|_| "Minimum backend version trong lease không hợp lệ.".to_string())?;
    let launcher_version = Version::parse(app_version())
        .map_err(|_| "Phiên bản launcher không hợp lệ.".to_string())?;
    let minimum_launcher = Version::parse(&manifest.minimum_launcher_version)
        .map_err(|_| "Minimum launcher version trong manifest không hợp lệ.".to_string())?;
    let hash_is_lower_hex = manifest.sha256.len() == 64
        && manifest
            .sha256
            .bytes()
            .all(|value| value.is_ascii_digit() || (b'a'..=b'f').contains(&value));
    if manifest.protocol_version != 1
        || manifest.component != "backend"
        || manifest.target != "windows-x86_64"
        || manifest.channel != claims.channel
        || manifest_version == Version::new(0, 0, 0)
        || manifest_version < minimum_backend_version
        || !hash_is_lower_hex
        || manifest.size_bytes == 0
        || manifest.size_bytes > 10 * 1024 * 1024 * 1024
        || launcher_version < minimum_launcher
    {
        return Err(
            "Manifest backend không khớp license, nền tảng hoặc chính sách phiên bản.".to_string(),
        );
    }
    let url = Url::parse(&manifest.download_url)
        .map_err(|_| "URL tải trong manifest không hợp lệ.".to_string())?;
    if url.scheme() != "https"
        || url.host_str().is_none()
        || !url.username().is_empty()
        || url.password().is_some()
        || url.fragment().is_some()
    {
        return Err("URL tải trong manifest không an toàn.".to_string());
    }
    Ok(manifest)
}

fn validate_backend_pointer(
    pointer: &BackendPointer,
    claims: &LeaseClaims,
) -> Result<ReleaseManifest, String> {
    let manifest = validate_backend_manifest(&pointer.signed_manifest, claims)?;
    validate_backend_pointer_fields(pointer, &manifest)?;
    Ok(manifest)
}

fn validate_backend_pointer_fields(
    pointer: &BackendPointer,
    manifest: &ReleaseManifest,
) -> Result<(), String> {
    let expected_filename = format!("backend-{}.exe", manifest.version);
    if pointer.version != manifest.version
        || pointer.filename != expected_filename
        || pointer.sha256 != manifest.sha256
        || pointer.size_bytes != manifest.size_bytes
    {
        return Err("Metadata backend local không khớp manifest đã ký.".to_string());
    }
    Ok(())
}

async fn hash_file(path: &PathBuf) -> Result<(String, u64), String> {
    let mut file = fs::File::open(path)
        .await
        .map_err(|_| "Không mở được backend binary.".to_string())?;
    let mut hasher = Sha256::new();
    let mut total = 0_u64;
    let mut buffer = vec![0_u8; 1024 * 1024];
    loop {
        let read = tokio::io::AsyncReadExt::read(&mut file, &mut buffer)
            .await
            .map_err(|_| "Không đọc được backend binary.".to_string())?;
        if read == 0 {
            break;
        }
        total += read as u64;
        hasher.update(&buffer[..read]);
    }
    Ok((hex::encode(hasher.finalize()), total))
}

async fn trusted_installed_backend(
    app: &AppHandle,
    claims: &LeaseClaims,
) -> Result<(BackendPointer, ReleaseManifest), String> {
    let pointer = load_backend_pointer(app).await?;
    let manifest = validate_backend_pointer(&pointer, claims)?;
    let executable = app_data_dir(app)?.join("artifacts").join(&pointer.filename);
    let (actual_hash, actual_size) = hash_file(&executable).await?;
    if actual_hash != manifest.sha256 || actual_size != manifest.size_bytes {
        return Err("Backend local đã bị thay đổi; cần tải lại bản chính thức.".to_string());
    }
    Ok((pointer, manifest))
}

async fn trusted_friends_backend() -> Result<PathBuf, String> {
    let expected_hash = required_build_value(
        option_env!("TKAUTO_FRIEND_BACKEND_SHA256"),
        "Friends backend SHA-256",
    )?
    .to_ascii_lowercase();
    if expected_hash.len() != 64
        || !expected_hash
            .bytes()
            .all(|value| value.is_ascii_digit() || (b'a'..=b'f').contains(&value))
    {
        return Err("SHA-256 nhúng cho backend Friends không hợp lệ.".to_string());
    }
    let path = friends_backend_path()?;
    let (actual_hash, size) = hash_file(&path).await?;
    if size == 0 || actual_hash != expected_hash {
        return Err("Backend Friends bị thiếu hoặc đã bị thay đổi.".to_string());
    }
    Ok(path)
}

#[tauri::command]
async fn check_backend_update(
    app: AppHandle,
    state: State<'_, SecurityState>,
) -> Result<UpdateResult, String> {
    if friends_build() {
        trusted_friends_backend().await?;
        return Ok(UpdateResult {
            updated: false,
            version: Some(app_version().to_string()),
            mandatory: false,
        });
    }
    let key = device_key()?;
    let (_, claims) = read_lease(&app, &key).await?;
    let current_version = match trusted_installed_backend(&app, &claims).await {
        Ok((_, manifest)) => manifest.version,
        Err(_) => "0.0.0".to_string(),
    };
    let target = "windows-x86_64";
    let action = format!("RELEASE:backend:{current_version}:{target}");
    let launcher_version = app_version();
    let (issued_at, nonce, signature) = device_proof(&key, &action, launcher_version);
    let request = serde_json::json!({
        "device_id": device_id(&key),
        "issued_at": issued_at,
        "nonce": nonce,
        "signature": signature,
        "app_version": launcher_version,
        "license_id": claims.license_id,
        "component": "backend",
        "current_version": current_version,
        "target": target,
    });
    let response = state
        .control_http
        .post(format!(
            "{}/v1/releases/check",
            control_plane_url()?.trim_end_matches('/')
        ))
        .json(&request)
        .send()
        .await
        .map_err(|_| "Không kiểm tra được bản backend mới.".to_string())?;
    let response_status = response.status();
    if !response_status.is_success() {
        if is_authoritative_entitlement_denial(response_status) {
            invalidate_local_entitlement(&app, &state.backend).await;
        }
        return Err(format!(
            "Máy chủ từ chối kiểm tra update (HTTP {}).",
            response_status.as_u16()
        ));
    }
    let response: ReleaseCheckResponse = response
        .json()
        .await
        .map_err(|_| "Phản hồi update không hợp lệ.".to_string())?;
    if !response.update_available {
        return Ok(UpdateResult {
            updated: false,
            version: None,
            mandatory: false,
        });
    }
    let token = response
        .signed_manifest
        .ok_or_else(|| "Update thiếu manifest ký số.".to_string())?;
    let grant = response
        .download_grant
        .ok_or_else(|| "Update thiếu vé tải.".to_string())?;
    if response.grant_expires_at.unwrap_or_default() <= chrono_now() {
        return Err("Vé tải update đã hết hạn.".to_string());
    }
    let manifest = validate_backend_manifest(&token, &claims)?;
    let incoming_version = Version::parse(&manifest.version)
        .map_err(|_| "Phiên bản backend trong manifest không hợp lệ.".to_string())?;
    let installed_version = Version::parse(&current_version)
        .map_err(|_| "Phiên bản backend đang cài không hợp lệ.".to_string())?;
    if incoming_version <= installed_version {
        return Err("Máy chủ trả về backend không mới hơn phiên bản đang cài.".to_string());
    }
    let url = Url::parse(&manifest.download_url)
        .map_err(|_| "URL tải trong manifest không hợp lệ.".to_string())?;
    let download = state
        .control_http
        .get(url)
        .bearer_auth(grant)
        .send()
        .await
        .map_err(|_| "Không tải được backend binary.".to_string())?;
    if !download.status().is_success() {
        return Err(format!(
            "Máy chủ từ chối tải backend (HTTP {}).",
            download.status().as_u16()
        ));
    }
    if download
        .content_length()
        .is_some_and(|length| length != manifest.size_bytes)
    {
        return Err("Kích thước backend không khớp manifest.".to_string());
    }
    let artifacts = app_data_dir(&app)?.join("artifacts");
    fs::create_dir_all(&artifacts)
        .await
        .map_err(|_| "Không tạo được thư mục artifact.".to_string())?;
    let final_name = format!("backend-{}.exe", manifest.version);
    let final_path = artifacts.join(&final_name);
    let temporary = artifacts.join(format!(".download-{}.tmp", Uuid::new_v4()));
    let mut file = fs::OpenOptions::new()
        .create_new(true)
        .write(true)
        .open(&temporary)
        .await
        .map_err(|_| "Không tạo được file tải tạm.".to_string())?;
    let mut stream = download.bytes_stream();
    let mut hasher = Sha256::new();
    let mut total = 0_u64;
    while let Some(chunk) = stream.next().await {
        let chunk = chunk.map_err(|_| "Luồng tải backend bị gián đoạn.".to_string())?;
        total = total.saturating_add(chunk.len() as u64);
        if total > manifest.size_bytes || total > 10 * 1024 * 1024 * 1024 {
            let _ = fs::remove_file(&temporary).await;
            return Err("Backend tải về vượt kích thước đã ký.".to_string());
        }
        hasher.update(&chunk);
        file.write_all(&chunk)
            .await
            .map_err(|_| "Không ghi được backend tải về.".to_string())?;
    }
    file.flush()
        .await
        .map_err(|_| "Không flush được backend tải về.".to_string())?;
    drop(file);
    let actual_hash = hex::encode(hasher.finalize());
    if total != manifest.size_bytes || actual_hash != manifest.sha256 {
        let _ = fs::remove_file(&temporary).await;
        return Err("Backend tải về không khớp SHA-256 trong manifest ký số.".to_string());
    }
    if fs::metadata(&final_path).await.is_ok() {
        let (existing_hash, existing_size) = hash_file(&final_path).await?;
        if existing_hash == manifest.sha256 && existing_size == manifest.size_bytes {
            let _ = fs::remove_file(&temporary).await;
        } else {
            fs::remove_file(&final_path)
                .await
                .map_err(|_| "Không thể loại bỏ backend local đã bị thay đổi.".to_string())?;
            fs::rename(&temporary, &final_path)
                .await
                .map_err(|_| "Không thể phục hồi backend chính thức.".to_string())?;
        }
    } else {
        fs::rename(&temporary, &final_path)
            .await
            .map_err(|_| "Không kích hoạt được backend mới.".to_string())?;
    }
    let pointer = BackendPointer {
        version: manifest.version.clone(),
        filename: final_name,
        sha256: manifest.sha256.clone(),
        size_bytes: manifest.size_bytes,
        signed_manifest: token,
    };
    let pointer_json =
        serde_json::to_vec(&pointer).map_err(|_| "Không tạo được metadata backend.".to_string())?;
    replace_file(backend_pointer_path(&app)?, &pointer_json).await?;
    Ok(UpdateResult {
        updated: true,
        version: Some(manifest.version),
        mandatory: manifest.mandatory,
    })
}

fn local_headers(
    secret: &[u8],
    method: &str,
    path: &str,
    query: &str,
    body: &[u8],
) -> Result<Vec<(&'static str, String)>, String> {
    let timestamp = chrono_now();
    let mut nonce_bytes = [0_u8; 18];
    OsRng.fill_bytes(&mut nonce_bytes);
    let nonce = b64url(&nonce_bytes);
    let (content_hash, signature) =
        local_signature(secret, timestamp, &nonce, method, path, query, body)?;
    Ok(vec![
        ("X-TK-Auto-Timestamp", timestamp.to_string()),
        ("X-TK-Auto-Nonce", nonce),
        ("X-TK-Auto-Content-SHA256", content_hash),
        ("X-TK-Auto-Signature", signature),
    ])
}

fn local_signature(
    secret: &[u8],
    timestamp: i64,
    nonce: &str,
    method: &str,
    path: &str,
    query: &str,
    body: &[u8],
) -> Result<(String, String), String> {
    let target = if query.is_empty() {
        path.to_string()
    } else {
        format!("{path}?{query}")
    };
    let content_hash = hex::encode(Sha256::digest(body));
    let canonical = format!(
        "TKAUTO-LOCAL-v1\n{timestamp}\n{nonce}\n{}\n{}\n{content_hash}",
        method.to_ascii_uppercase(),
        b64url(target.as_bytes()),
    );
    let mut mac = HmacSha256::new_from_slice(secret)
        .map_err(|_| "Session HMAC key không hợp lệ.".to_string())?;
    mac.update(canonical.as_bytes());
    let signature = b64url(&mac.finalize().into_bytes());
    Ok((content_hash, signature))
}

#[tauri::command]
async fn launch_backend(app: AppHandle, state: State<'_, SecurityState>) -> Result<(), String> {
    let captcha_key = captcha_api_key()?.ok_or_else(|| {
        "Hãy cấu hình khóa CAPTCHA của khách hàng trước khi chạy backend.".to_string()
    })?;
    let (executable, licensed_context) = if friends_build() {
        (trusted_friends_backend().await?, None)
    } else {
        let key = device_key()?;
        let (_, claims) = read_lease(&app, &key).await?;
        let (pointer, manifest) = trusted_installed_backend(&app, &claims).await?;
        (
            app_data_dir(&app)?
                .join("artifacts")
                .join(&pointer.filename),
            Some((key, manifest.version)),
        )
    };
    let mut runtime = state.backend.lock().await;
    if let Some(child) = runtime.child.as_mut() {
        if child
            .try_wait()
            .map_err(|_| "Không kiểm tra được backend process.".to_string())?
            .is_none()
        {
            return Ok(());
        }
    }
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
        .await
        .map_err(|_| "Không cấp được cổng local an toàn.".to_string())?;
    let port = listener
        .local_addr()
        .map_err(|_| "Không đọc được cổng local.".to_string())?
        .port();
    drop(listener);
    let mut secret = [0_u8; 32];
    OsRng.fill_bytes(&mut secret);
    let secret_encoded = b64url(&secret);
    let mut command = Command::new(&executable);
    command
        .stdin(Stdio::piped())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .kill_on_drop(true);
    #[cfg(windows)]
    command.creation_flags(windows_sys::Win32::System::Threading::CREATE_NO_WINDOW);
    let mut child = command
        .spawn()
        .map_err(|_| "Không khởi chạy được backend binary.".to_string())?;
    let database_url = format!(
        "sqlite:///{}",
        app_data_dir(&app)?
            .join("database.db")
            .to_string_lossy()
            .replace('\\', "/")
    );
    let bootstrap = if let Some((key, backend_version)) = licensed_context {
        serde_json::json!({
            "device_id": device_id(&key),
            "lease_path": lease_path(&app)?.to_string_lossy(),
            "license_public_keys_json": license_public_keys()?,
            "local_session_secret": secret_encoded,
            "app_version": backend_version,
            "database_url": database_url,
            "omocaptcha_key": captcha_key,
            "port": port,
        })
    } else {
        serde_json::json!({
            "local_session_secret": secret_encoded,
            "app_version": app_version(),
            "database_url": database_url,
            "omocaptcha_key": captcha_key,
            "port": port,
        })
    };
    let mut stdin = child
        .stdin
        .take()
        .ok_or_else(|| "Backend không mở secure bootstrap pipe.".to_string())?;
    stdin
        .write_all(
            serde_json::to_string(&bootstrap)
                .map_err(|_| "Không mã hóa được bootstrap.".to_string())?
                .as_bytes(),
        )
        .await
        .map_err(|_| "Không gửi được secure bootstrap.".to_string())?;
    stdin
        .write_all(b"\n")
        .await
        .map_err(|_| "Không hoàn tất được secure bootstrap.".to_string())?;
    stdin
        .shutdown()
        .await
        .map_err(|_| "Không đóng được secure bootstrap pipe.".to_string())?;
    runtime.port = Some(port);
    runtime.session_secret = Some(secret.to_vec());
    runtime.child = Some(child);
    drop(runtime);
    if let Err(error) = wait_for_backend(&state.local_http, &state.backend).await {
        let mut failed_runtime = state.backend.lock().await;
        if let Some(mut failed_child) = failed_runtime.child.take() {
            let _ = failed_child.kill().await;
        }
        failed_runtime.port = None;
        failed_runtime.session_secret = None;
        return Err(error);
    }
    spawn_websocket_bridge(
        app.clone(),
        state.inner().backend.clone(),
        "/ws",
        "backend-ws-message",
    );
    spawn_websocket_bridge(
        app,
        state.inner().backend.clone(),
        "/ws/screens",
        "backend-screen-message",
    );
    Ok(())
}

async fn wait_for_backend(
    http: &Client,
    backend: &Arc<Mutex<BackendRuntime>>,
) -> Result<(), String> {
    for _ in 0..60 {
        let (port, secret) = {
            let runtime = backend.lock().await;
            (runtime.port, runtime.session_secret.clone())
        };
        let (Some(port), Some(secret)) = (port, secret) else {
            break;
        };
        let headers = local_headers(&secret, "GET", "/", "", &[])?;
        let mut request = http.get(format!("http://127.0.0.1:{port}/"));
        for (name, value) in headers {
            request = request.header(name, value);
        }
        if request
            .send()
            .await
            .is_ok_and(|response| response.status().is_success())
        {
            return Ok(());
        }
        sleep(Duration::from_millis(250)).await;
    }
    Err("Backend không sẵn sàng sau secure launch.".to_string())
}

fn spawn_websocket_bridge(
    app: AppHandle,
    backend: Arc<Mutex<BackendRuntime>>,
    path: &'static str,
    event_name: &'static str,
) {
    tauri::async_runtime::spawn(async move {
        loop {
            let (port, secret) = {
                let state = backend.lock().await;
                (state.port, state.session_secret.clone())
            };
            let (Some(port), Some(secret)) = (port, secret) else {
                return;
            };
            let headers = match local_headers(&secret, "WS", path, "", &[]) {
                Ok(value) => value,
                Err(_) => return,
            };
            let Ok(mut request) = format!("ws://127.0.0.1:{port}{path}").into_client_request()
            else {
                return;
            };
            for (name, value) in headers {
                if let (Ok(name), Ok(value)) = (
                    header::HeaderName::from_bytes(name.as_bytes()),
                    header::HeaderValue::from_str(&value),
                ) {
                    request.headers_mut().insert(name, value);
                }
            }
            if let Ok((stream, _)) = connect_async(request).await {
                let (_, mut incoming) = stream.split();
                while let Some(Ok(message)) = incoming.next().await {
                    if let Ok(text) = message.to_text() {
                        let _ = app.emit(event_name, text);
                    }
                }
            }
            sleep(Duration::from_secs(2)).await;
        }
    });
}

#[tauri::command]
async fn backend_request(
    state: State<'_, SecurityState>,
    request: BackendRequest,
) -> Result<BackendResponse, String> {
    if !request.path.starts_with('/')
        || request.path.contains("..")
        || request.path.contains('?')
        || request.path.contains('#')
    {
        return Err("API path không hợp lệ.".to_string());
    }
    let query = request.query.unwrap_or_default();
    if query.contains('#') || query.contains('\r') || query.contains('\n') {
        return Err("Query không hợp lệ.".to_string());
    }
    let method = Method::from_bytes(request.method.to_ascii_uppercase().as_bytes())
        .map_err(|_| "HTTP method không hợp lệ.".to_string())?;
    if !matches!(
        method,
        Method::GET | Method::POST | Method::PUT | Method::PATCH | Method::DELETE
    ) {
        return Err("HTTP method không được phép.".to_string());
    }
    let body = request
        .body_base64
        .map(|value| {
            general_purpose::STANDARD
                .decode(value)
                .map_err(|_| "Request body base64 không hợp lệ.".to_string())
        })
        .transpose()?
        .unwrap_or_default();
    if body.len() > MAX_HTTP_BODY {
        return Err("Request body vượt giới hạn local API.".to_string());
    }
    let (port, secret) = {
        let runtime = state.backend.lock().await;
        (runtime.port, runtime.session_secret.clone())
    };
    let (Some(port), Some(secret)) = (port, secret) else {
        return Err("Backend chưa chạy.".to_string());
    };
    let url = if query.is_empty() {
        format!("http://127.0.0.1:{port}{}", request.path)
    } else {
        format!("http://127.0.0.1:{port}{}?{query}", request.path)
    };
    let mut outgoing = state
        .local_http
        .request(method.clone(), url)
        .body(body.clone());
    for (name, value) in local_headers(&secret, method.as_str(), &request.path, &query, &body)? {
        outgoing = outgoing.header(name, value);
    }
    if let Some(content_type) = request.content_type {
        if content_type.len() > 200 || content_type.contains('\r') || content_type.contains('\n') {
            return Err("Content-Type không hợp lệ.".to_string());
        }
        outgoing = outgoing.header(header::CONTENT_TYPE, content_type);
    }
    let response = outgoing
        .send()
        .await
        .map_err(|_| "Local backend request thất bại.".to_string())?;
    let status = response.status().as_u16();
    let content_type = response
        .headers()
        .get(header::CONTENT_TYPE)
        .and_then(|value| value.to_str().ok())
        .map(str::to_string);
    let bytes = response
        .bytes()
        .await
        .map_err(|_| "Không đọc được local backend response.".to_string())?;
    if bytes.len() > MAX_RESPONSE_BODY {
        return Err("Local backend response quá lớn.".to_string());
    }
    Ok(BackendResponse {
        status,
        content_type,
        body_base64: general_purpose::STANDARD.encode(bytes),
    })
}

pub fn run() {
    let control_http = Client::builder()
        .https_only(true)
        .connect_timeout(Duration::from_secs(15))
        .timeout(Duration::from_secs(15 * 60))
        .redirect(reqwest::redirect::Policy::none())
        .build()
        .expect("secure control-plane HTTP client configuration");
    let local_http = Client::builder()
        .https_only(false)
        .connect_timeout(Duration::from_secs(5))
        .timeout(Duration::from_secs(15 * 60))
        .redirect(reqwest::redirect::Policy::none())
        .build()
        .expect("local HTTP client configuration");
    let builder = tauri::Builder::default();
    let builder = if friends_build() {
        builder
    } else {
        builder.plugin(tauri_plugin_updater::Builder::new().build())
    };
    builder
        .plugin(tauri_plugin_process::init())
        .manage(SecurityState {
            control_http,
            local_http,
            backend: Arc::new(Mutex::new(BackendRuntime::default())),
        })
        .setup(|app| {
            if !friends_build() {
                let handle = app.handle().clone();
                let backend = app.state::<SecurityState>().backend.clone();
                spawn_entitlement_monitor(handle, backend);
            }
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            security_status,
            activate_license,
            renew_license,
            set_captcha_api_key,
            clear_captcha_api_key,
            check_backend_update,
            launch_backend,
            backend_request,
        ])
        .run(tauri::generate_context!())
        .expect("error while running TikTok Auto desktop");
}

#[cfg(test)]
mod tests {
    use super::{
        is_authoritative_entitlement_denial, local_signature, validate_backend_pointer_fields,
        BackendPointer, ReleaseManifest,
    };
    use reqwest::StatusCode;

    #[test]
    fn local_hmac_matches_python_backend_vector() {
        let secret: Vec<u8> = (0_u8..32).collect();
        let (content_hash, signature) = local_signature(
            &secret,
            1_800_000_000,
            "abcdefghijklmnopqrstuvwx",
            "POST",
            "/api/v1/tasks/test",
            "a=1%20x",
            b"hello",
        )
        .expect("valid HMAC vector");
        assert_eq!(
            content_hash,
            "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
        );
        assert_eq!(signature, "TDWk9OL9z86rMXuPpK7MOKd6RG0ncnl74y_99PRJXtA");
    }

    #[test]
    fn unsigned_backend_pointer_cannot_change_signed_manifest_fields() {
        let manifest = ReleaseManifest {
            protocol_version: 1,
            artifact_id: "artifact_001".to_string(),
            component: "backend".to_string(),
            version: "1.2.3".to_string(),
            channel: "stable".to_string(),
            target: "windows-x86_64".to_string(),
            download_url: "https://license.example.test/v1/artifacts/a/download".to_string(),
            sha256: "a".repeat(64),
            size_bytes: 1234,
            published_at: 1_800_000_000,
            minimum_launcher_version: "1.0.0".to_string(),
            mandatory: false,
        };
        let mut pointer = BackendPointer {
            version: manifest.version.clone(),
            filename: "backend-1.2.3.exe".to_string(),
            sha256: manifest.sha256.clone(),
            size_bytes: manifest.size_bytes,
            signed_manifest: "signed-token".to_string(),
        };
        assert!(validate_backend_pointer_fields(&pointer, &manifest).is_ok());
        pointer.sha256 = "b".repeat(64);
        assert!(validate_backend_pointer_fields(&pointer, &manifest).is_err());
    }

    #[test]
    fn only_authoritative_server_denials_destroy_the_offline_lease() {
        assert!(is_authoritative_entitlement_denial(StatusCode::FORBIDDEN));
        assert!(is_authoritative_entitlement_denial(
            StatusCode::UPGRADE_REQUIRED
        ));
        assert!(!is_authoritative_entitlement_denial(
            StatusCode::INTERNAL_SERVER_ERROR
        ));
        assert!(!is_authoritative_entitlement_denial(
            StatusCode::REQUEST_TIMEOUT
        ));
    }
}
