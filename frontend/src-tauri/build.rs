fn main() {
    println!("cargo:rerun-if-env-changed=TKAUTO_FRIEND_BUILD");
    println!("cargo:rerun-if-env-changed=TKAUTO_FRIEND_BACKEND_SHA256");
    println!("cargo:rerun-if-env-changed=TKAUTO_APP_VERSION");
    println!("cargo:rerun-if-env-changed=TKAUTO_CONTROL_PLANE_URL");
    println!("cargo:rerun-if-env-changed=TKAUTO_LICENSE_PUBLIC_KEYS_JSON");
    println!("cargo:rerun-if-env-changed=TKAUTO_RELEASE_PUBLIC_KEYS_JSON");
    tauri_build::build()
}
