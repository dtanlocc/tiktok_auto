# Implementation Plan: Device-Bound Licensed Binary Delivery

## Selected Design And Constraints

We selected the hybrid control-plane boundary with a local-execution profile. A signed bootstrap/desktop executable activates a license, proves possession of a device key, downloads a signed backend executable, and starts it only with a current lease. React contains presentation only. Python application code is compiled for release and no source tree is delivered.

The license server owns activation, expiry, device limits, revocation, feature entitlements, minimum supported version, and artifact manifests. Browser automation, account data, cookies, and video files remain on the customer PC. A bounded offline lease may be supported, but a permanent offline bypass is not.

We do not claim that deleting an expired executable prevents copying or memory inspection. Expiry security comes from a short-lived signed lease checked at startup and privileged-operation boundaries. Deletion is a cleanup and product-policy action only.

## Source Revision And Drift Check

- Evidence collection: `ea0c32f81c0e07683e2e0629ea27efc77c1b8744e82c316bb959dfa81e88b29e`
- Selected revision: `ee06783a2a2f6ce38945a8845610fc72b0ad7e57`
- Relevant application source matched the selected revision at implementation start.
- Repository-wide drift exists in unrelated runtime/test artifacts and the uncommitted hardening documents. Those files must not be overwritten or included in releases.

## Implemented And Verified On 2026-09-04

- Strict Ed25519 device-bound leases, key IDs/rotation, feature/limit claims,
  independent minimum desktop/backend versions, expiry, nonce replay protection,
  automatic renewal, license/device revocation, and control-plane audit records.
  The production lease default is one hour and an online client renews every 15
  minutes. An authoritative HTTP 403/426 response removes the local lease, stops
  the backend, and purges downloaded backend executables; network outages do not
  destroy an otherwise valid offline lease.
- A short-lived HMAC download grant plus an immutable Ed25519 release manifest
  binding component, version, channel, architecture, HTTPS URL, SHA-256, size,
  launcher minimum, and mandatory-update policy. Download grants are single-use.
- A Tauri/Rust launcher that keeps the device key in Windows Credential Manager,
  verifies leases/manifests, downloads and hashes a versioned backend EXE, sends
  one-launch bootstrap state over stdin, and owns HTTP/WebSocket HMAC signing.
  It re-verifies the signed manifest and the complete backend file at every
  launch, repairs a missing/corrupt same-version artifact, rejects downgrade and
  below-policy versions, and checks signed desktop updates before startup.
- The customer supplies their own CAPTCHA provider key. It is stored in Windows
  Credential Manager and passed only through the per-launch stdin bootstrap; no
  shared provider credential is embedded in source or release artifacts.
- FastAPI production mode rejects unsigned local HTTP/WebSocket traffic and
  validates the lease at startup, every route/socket admission, task admission,
  and browser-allocation boundary. Existing active tasks are not killed mid-step.
- Nuitka onefile build with no-docstring/no-assert/isolated flags, explicit
  package data, credential-scrubbed extension staging, and a release allowlist.
  The staging 0.1.2 backend is 43,460,608 bytes with SHA-256
  `3F78302568BBC217D2253C18D774A502957A9620F08C9635D7DC5FE8F28FDB52` and
  passed a real signed-lease/signed-local-request smoke test from the EXE.
- The staging 0.1.2 desktop proof is 15,041,024 bytes with SHA-256
  `B268A0D30656C8FFBEF3C18AC176938D25A6D45C7240296916EDEAF2FABCAE29`.
  It proves the release link/build only and is intentionally not a customer
  installer because it has staging trust values and no Authenticode signature.
- Verification results: 130 backend tests, 12 control-plane tests, 23 focused
  backend security tests, 3 Rust tests, frontend TypeScript/Vite production
  build, release artifact audits, extension credential audit, and compiled EXE
  smoke test all passed. Some sets overlap and are reported separately.
- The vendor control plane now includes PostgreSQL production enforcement,
  file-mounted secrets, separate signing keys, trusted-host and request limits,
  activation/device/admin rate limits, device-key-derived IDs, row locking for
  activation limits, license/device administration, release deactivation, and
  one-time artifact authorization. A Caddy/PostgreSQL container deployment and
  operator CLI/runbook are included; Compose syntax validation passed.

The operator's current `backend/database.db` (23,863,296 bytes), WAL, and SHM
files were not deleted or migrated; `PRAGMA quick_check` reports `ok`. WAL/SHM
and cookie exports are untracked for future commits but remain local. Packaged
customer data uses the desktop app-data directory so an EXE update does not
replace the database.

## Production Gates Still Requiring Operator-Owned Material

- Nuitka Community produced the verified EXE. Nuitka Commercial protection has
  not been applied because no commercial license is installed. The production
  pipeline now both requires a Commercial installation and explicitly enables
  its `data-hiding` plugin; merely detecting a Commercial license is not treated
  as sufficient protection.
- A real Authenticode certificate/private-key service, Tauri updater key,
  control-plane domain/TLS deployment, PostgreSQL instance, KMS/HSM key mounts,
  and production public keys were not supplied. The production build script
  fails closed when these values are absent.
- The credential formerly embedded in source still exists in Git history and in
  an ignored local unpacked extension. It is excluded from release artifacts,
  but must be rotated at the provider. History rewrite/force-push requires a
  separately approved coordinated operation.
- A shared CAPTCHA provider key cannot be kept secret if the customer extension
  calls that provider directly. Use customer-owned keys or add a vendor-hosted
  proxy/capability service before shipping a shared credential.
- Independent reverse-engineering, clean-VM installer, clock-rollback, update
  interruption/rollback, and commercial redistribution-rights reviews remain
  required before labeling the package production-certified.
- The Docker Compose model was validated, but the local Docker Desktop Linux
  engine was inaccessible, so the production image has not been built locally.
- The automated security diff scan could not process the repository because a
  nested Git submodule has uncommitted changes. A focused manual review was
  completed without cleaning or modifying that submodule.

## Affected Components

- `backend/app/core/config.py` and `backend/app/main.py`
- `backend/app/interfaces/api/` and privileged use-case entry points
- New backend security/authentication modules
- New vendor-only license/control-plane service
- Tauri desktop/bootstrap source and frontend transport/license screen
- Nuitka release entry point and deterministic build scripts
- CI release signing, artifact publication, update channels, and operator runbooks
- Repository tracking rules and local credential storage

## Ordered Work Packages

### WP1 — Release hygiene and secret boundary

Remove source-owned secrets, untrack credential/runtime artifacts without deleting the operator's local copies, add a strict release allowlist, and make production debug/docs fail closed. Rotate every secret that has appeared in Git history.

### WP2 — Cryptographic license contract

Define a strict Ed25519 signed-lease format with fixed algorithm/type, key ID, license/device identity, explicit features and limits, issued/not-before/expiry times, minimum version, protocol version, and unique token ID. Private keys are supplied only through production secret storage.

### WP3 — Authenticated local process boundary

Create a random per-launch key, transfer it from launcher to backend outside command-line arguments, and require timestamped nonce-based HMAC authentication for all HTTP and WebSocket traffic. React must call a narrow native bridge and never receive the key.

### WP4 — Enforcement and expiry state machine

Centralize entitlement checks at privileged use-case boundaries. Expiry stops admission of new work, lets an already-running operation reach a defined safe boundary, closes the backend, and permits best-effort payload cleanup. Clock rollback, stale lease, replay, revoked device, and below-minimum-version states fail closed.

### WP5 — Vendor control plane

Implement activation, renewal, deactivation/transfer, revocation, device proof, feature/limit policy, artifact manifests, release checks, audit events, rate limiting, and administrator authentication. Store only keyed hashes of activation keys; keep signing keys in KMS/HSM-backed deployment secrets.

### WP6 — Binary-only desktop and backend release

Package React in Tauri, compile the Python worker with Nuitka Commercial, strip debug/source metadata, include only allowlisted browser assets, and produce Windows x64 installer/update artifacts. The installed payload may contain executables, DLLs, browser files, and protected data, but no project Python/TypeScript source or source maps.

### WP7 — Signed download and updater

The launcher accepts only a manifest bound to license, device, channel, version, architecture, SHA-256, size, expiry, and signature. Verify the artifact before atomic installation. Tauri update signatures and Windows Authenticode signatures are independent checks. Defer restart/install while automation is active and retain rollback metadata.

### WP8 — Adversarial release qualification

Attempt API replay, stale-session use, device-state copying, lease forgery, clock rollback, manifest substitution, downgrade, payload modification, binary extraction, frontend modification, expired-license launch, and update rollback. A third-party reverse-engineering review is required before calling the commercial boundary production-ready.

## Compatibility And Migration

Development mode remains explicit and source-based for maintainers. Production mode never falls back to development authentication or unsigned artifacts. Existing local databases migrate in place after backup; credential fields are protected through Windows DPAPI or Credential Manager. Existing customers receive licenses through an operator-controlled migration rather than an embedded universal key.

The first release targets Windows x64. Firefox, extensions, Windows dialogs, Unicode media paths, concurrent browsers, WebSocket streams, scheduled jobs, and graceful shutdown must be tested from the installed binary, not only from the source tree.

## Tactical Protections During Migration

- Remove and rotate the source-owned third-party secret immediately.
- Untrack cookie/database runtime artifacts while preserving local operator copies.
- Keep production security mode disabled until the native bridge supplies authentication; never expose an unauthenticated production listener.
- Do not publish a partly protected installer. Internal development builds remain clearly watermarked and unsupported for customers.
- Keep every license and release signing private key outside the repository from the first implementation commit.

## Tests And Security Validation

- Golden and negative vectors for lease signing/verification, key rotation, time windows, device binding, feature limits, and version policy.
- HTTP and WebSocket authentication vectors for missing/invalid signature, body mutation, path/query mutation, nonce replay, time skew, and previous-launch keys.
- Integration tests proving every privileged route/use case is covered and anonymous health endpoints expose no sensitive state.
- Control-plane tests for key hashing, duplicate activation, max devices, transfer, revocation, audit logs, artifact authorization, and admin authorization.
- Release scans proving the absence of source, maps, Git metadata, credentials, private keys, and customer runtime files.
- Signature/tamper tests for manifests, payloads, installers, update packages, downgrade attempts, and key rotation.

## Performance And Resource Benchmarks

Measure current source build versus packaged build on a clean Windows VM: installer and payload size, cold/warm startup, idle RSS, first API response, browser launch, upload task start, license activation/renewal p50/p95, update download/verify/install, and shutdown cleanup. No threshold is claimed until baseline measurements are collected.

## Rollout And Rollback

Use internal, beta, and stable channels. License enforcement progresses through audit, warning, then enforce. Release rollout is staged and can be stopped server-side. The control plane can temporarily extend lease grace during an incident. Rollback selects the previous still-signed version; clients never accept an unsigned or below-policy downgrade.

## Acceptance Criteria

- Customer packages contain no project source or private key material.
- A copied activation key alone cannot activate another device beyond policy.
- A copied lease cannot run on a different device and cannot authorize work after expiry/grace.
- Requests that bypass the desktop process are rejected, including WebSockets.
- Tampered, downgraded, expired, wrong-device, wrong-channel, or unsigned payloads never execute.
- Expiry blocks new jobs and never kills an active browser/upload at an unsafe point.
- Update notification, deferral, mandatory update, signature verification, rollback, and interrupted downloads pass on clean Windows systems.
- Production startup fails closed when required security configuration is absent.

## Open Decisions

- Whether the current one-hour offline lease should be shortened further for
  higher-value plans or lengthened for unreliable customer networks.
- License unit: device, named operator, concurrent seat, or account capacity.
- Hosting, database, object storage/CDN, domain, and administrator identity provider for the control plane.
- Procurement of Nuitka Commercial and an RSA Authenticode signing service/certificate.
- Commercial redistribution rights for the browser, modified Playwright runtime, and CAPTCHA extensions.
