# Security Hardening Proposal: Commercial Client Trust Boundary

## Decision

We need to decide where commercial authorization and proprietary behavior will be trusted. Packaging and obfuscation slow static recovery but cannot make a customer-controlled process authoritative. The meaningful choice is how much authority stays local and what availability cost we accept when moving it to a service we control.

## Executive Recommendation

The complete option set is:

- **Option 1: Hardened local client.** Compile and sign the app and verify an offline license locally. It offers the best offline behavior but a patch can bypass the local decision.
- **Option 2: Hybrid online control plane.** Keep browser execution local while a service owns activation, revocation, entitlements, release policy, and selected short-lived capabilities.
- **Option 3: Cloud-authorized execution.** Require server authorization and server-held proprietary policy for every valuable operation. This is strongest, with the largest availability and operations cost.

I recommend Option 2 now. Its token and protocol design should permit selected premium actions to move to Option 3 later without replacing the desktop architecture.

## Evidence

| Evidence | Finding or document | What it establishes |
| --- | --- | --- |
| `E001` | Unauthenticated fixed local API | `backend/app/main.py` enables wildcard CORS and mounts privileged routers without global application authentication. |
| `E002` | UI owns local API addressing | Frontend code calls stable `127.0.0.1:9000` HTTP/WebSocket endpoints directly. |
| `E003` | Source-owned secret/debug defaults | `backend/app/core/config.py` enables debug by default and contains a third-party secret. Its value is deliberately omitted. |
| `E004` | No signed desktop target | The build manifests contain no desktop shell, compiled release, license client, or signed updater. |
| `E005` | No entitlement boundary | Privileged task routes do not authenticate the desktop instance or require feature entitlements. |
| `E006` | Runtime artifacts tracked | Cookie and SQLite WAL/SHM artifacts are tracked, making repository-copy releases unsafe. |

I inspected the cited callers at revision `ee06783`. E001, E002, and E005 most strongly shape the diagnosis: knowledge of the local port and request schema is currently sufficient to exercise privileged routes. E003, E004, and E006 show why placing the present directory inside an installer would not create a commercial security boundary.

## Current Design And Failure Mode

The browser UI knows a stable port, constructs privileged requests, and connects directly to WebSockets. FastAPI trusts any caller that reaches loopback. Python source, configuration, support files, and runtime data share the same deployment model.

A local attacker does not need every source line. They can observe the network contract, replay calls, patch a future `if licensed` branch, or replace a component. Sending a raw license key with every request is worse: it becomes visible in browser memory, logs, and captures, and replaying it becomes sufficient authorization. The structural failure is ambient local authority, not only readable Python.

## Desired Invariants

- Customer artifacts contain no repository, `.py`, unneeded bytecode, source maps, customer data, build secrets, or private signing keys.
- The activation key is used once for activation; it is not a runtime bearer credential.
- Every local HTTP/WebSocket operation requires a random per-launch capability that React never receives in plaintext.
- Every privileged use case checks the final action, feature, device, plan, numeric limit, protocol version, and expiry.
- The service can revoke a device and require a minimum safe version after a bounded grace period.
- Device proof uses a non-exportable TPM key where available, with a clearly lower-assurance DPAPI fallback.
- Updates install only after Tauri updater-signature and Windows publisher-signature verification.
- Credential fields and cached leases are protected at rest and never appear in logs or crash reports.

## Constraints And Non-Goals

Windows x64 and local browser/video execution are compatibility requirements. We assume a short offline grace is acceptable and do not yet have a measured latency or memory budget. Absolute protection from a machine administrator, debugger, kernel component, memory inspection, or behavior observation is a non-goal because client-only software cannot guarantee it.

Malware-like packers should not be the primary control. They can increase antivirus false positives while leaving authorization patchable. Service-held authority, code signing, revocation, a clean build, and explicit trust boundaries are more durable.

## Before Architecture

The [before diagram](../diagrams/commercial-client-trust-boundary-before.mmd) shows the current boundary. Every same-user process can approach the fixed loopback API used by the UI; no launcher-owned secret or remote decision separates the caller from automation.

## Options

### Option 1: Hardened Local Client

We package React in Tauri, compile the FastAPI worker with Nuitka, verify a server-signed offline license with an embedded public key, bind its stored form to Windows, and sign the installer. Tauri supports embedding an API server as a [sidecar](https://v2.tauri.app/develop/sidecar/). Nuitka Commercial adds protection for constants, data, and tracebacks beyond ordinary compilation ([official description](https://ssh.nuitka.net/doc/commercial.html)).

The advantage is offline reliability. A license-server outage does not stop work. The limitation is fundamental: the verification code, entitlement result, and useful execution path all remain on one machine. Compilation raises reverse-engineering cost but a determined attacker can patch the post-verification branch or call below it. This option should win only when long offline use outweighs revocation and crack resistance.

See [Option 1 architecture](../diagrams/commercial-client-trust-boundary-local-compiled-after.mmd).

| Change | Before | After | Security consequence | Cost |
| --- | --- | --- | --- | --- |
| Distribution | Source-based tree | Signed Tauri package and compiled sidecar | Static source extraction becomes materially harder | Tauri/Nuitka build toolchain |
| Local API | Fixed unauthenticated port | Random port and per-run capability | Blind loopback replay is rejected | Rust bridge and middleware |
| License | None | Locally verified signed license | Copying a plain key is insufficient | Local verifier remains patchable |
| Secrets | Source/runtime files | DPAPI-protected state and release allowlist | Reduces accidental and at-rest exposure | Migration/recovery logic |
| Updates | Manual | Signed update feed | Blocks unsigned substitutions | Signing-key custody |

We can roll back the commercial build without changing developer execution by retaining an explicitly separate development launcher. Packaging must not silently fall back to unauthenticated production mode.

### Option 2: Hybrid Online Control Plane

This preserves local browser automation but moves durable authority to a service we operate. Activation sends the human-entered key once over TLS. The service stores a verifier/hash, binds the activation to an installation public key, and returns a short-lived signed lease containing license ID, device ID, plan, explicit features, limits, protocol version, minimum app version, issue time, and expiry. The client never has the signing private key.

For stronger device binding, installation creates a TPM-backed device key where available. Microsoft documents that TPM private keys can remain non-exportable and usable only inside the device ([TPM fundamentals](https://learn.microsoft.com/en-us/windows/security/hardware-security/tpm/tpm-fundamentals)). A DPAPI-protected software key is the compatibility fallback; Microsoft recommends Credential Manager or DPAPI for persisted Windows secrets ([guidance](https://learn.microsoft.com/en-us/windows/win32/secbp/handling-passwords)).

The Tauri/Rust launcher starts the sidecar on a random port and transfers a one-run secret through an inherited pipe, not a command-line argument. React calls narrow Tauri commands; Rust authenticates calls to FastAPI, so JavaScript never owns the bearer value. FastAPI authenticates HTTP and WebSocket upgrades, then each sensitive use case checks the signed entitlement.

For premium operations, the service can issue a one-use capability bound to device, action, request digest, nonce, expiry, and app version. A copied lease cannot then authorize arbitrary jobs. Residual risk remains: if every useful input and operation is local, an attacker can patch both checks. The hybrid design is stronger when current policy or a required per-job artifact is service-issued, although data interpreted locally can still be captured at runtime.

Updates need two signatures. Tauri's updater requires a cryptographic signature and does not allow verification to be disabled ([Tauri updater](https://v2.tauri.app/plugin/updater/)). Separately, every EXE, DLL, and MSI/NSIS installer is Authenticode-signed and timestamped with an RSA publisher certificate, following [Microsoft signing guidance](https://learn.microsoft.com/en-us/windows/apps/develop/smart-app-control/code-signing-for-smart-app-control). Private keys live in managed CI/HSM custody, never the repository.

The update service returns version, release notes, channel, minimum compatible version, URL, hash, signature, and mandatory/optional policy. Mandatory updates are reserved for revoked or protocol-incompatible builds and never interrupt an active upload.

See [Option 2 architecture](../diagrams/commercial-client-trust-boundary-hybrid-control-plane-after.mmd).

| Change | Before | After | Security consequence | Cost |
| --- | --- | --- | --- | --- |
| Authority | Any same-host caller | Rust session boundary plus signed lease | Removes ambient authority and enables revocation | Control-service availability |
| API credential | None/raw-key proposal | Per-run local capability and short-lived remote token | Raw key is not replayable runtime authority | Token lifecycle and clock skew |
| Device binding | None | TPM challenge; DPAPI fallback | Copying state to another PC normally fails | Hardware recovery/support |
| Feature gating | UI behavior | Backend use-case policy and optional job capability | Patching React no longer unlocks work | Every privileged path must adopt the gate |
| Release | Mutable tree | Reproducible compiled, doubly signed artifact | Narrows tampering and fake updates | CI signing and rotation |
| Availability | Fully local | Cached lease with bounded grace | Revocation becomes enforceable | Long outages eventually block new work |

We can introduce audit mode, then warning mode, then enforcement. Rollback can disable remote enforcement server-side while retaining compiled packaging, local channel authentication, and signed update verification.

### Option 3: Cloud-Authorized Execution

This treats the desktop as an untrusted executor. Every valuable task goes to the control plane, which checks entitlement/version, owns proprietary policy, and issues a narrow execution plan or capability. The worker still drives Firefox and reads local videos, but cannot originate valid premium work independently.

This gives the strongest practical source protection because code that never ships cannot be extracted. It also gives immediate revocation and abuse controls. The principal cost is reliability: every job gains a network round trip and the service enters the posting critical path. We must own queues, retries, idempotency, regional availability, privacy controls, audit retention, and incident response. A TikTok/proxy failure must remain distinguishable from a control-plane failure.

The client and runtime plans remain observable. The security gain comes from withholding durable policy and job-minting authority, not from encrypting a plan that must be decrypted locally.

See [Option 3 architecture](../diagrams/commercial-client-trust-boundary-cloud-authorized-after.mmd).

| Change | Before | After | Security consequence | Cost |
| --- | --- | --- | --- | --- |
| Job creation | Local caller starts automation | Control plane scopes each job | Patched UI cannot mint valid work | Permanent online dependency |
| Proprietary policy | Delivered locally | Retained on managed service | Non-delivered code cannot be extracted | Server implementation/operations |
| Failure boundary | Local process | Executor plus service/queue | Abuse centrally contained | New outage/retry modes |
| Data flow | PC only | Metadata/request digests cross boundary | Enables anti-replay/audit | Privacy/retention work |
| Migration | Direct API | Versioned job protocol | Controlled evolution | Largest redesign |

A reversible rollout places one premium action behind server authorization and retains the hybrid lease path for everything else. If service reliability is not acceptable, that action can return to lease-only authorization without abandoning the desktop and release controls.

## Comparison

These effects are source-derived or hypothetical, not measured benchmarks.

| Dimension | Option 1: Local | Option 2: Hybrid | Option 3: Cloud-authorized |
| --- | --- | --- | --- |
| Security | Better static resistance; locally patchable | Revocation, device binding, scoped capabilities; executor inspectable | Strongest because durable policy does not ship |
| Performance | No task network hop | Lease off-path; premium token adds one request | Network/service hop for every protected job |
| Memory | Tauri and sidecar process | Similar plus small token caches and service state | Similar client; managed queues/caches |
| Reliability | Best offline behavior | Short outages masked by grace | Control plane is on the critical path |
| Operability | Build/signing and transfer support | Adds license, release, audit, revocation operations | Adds always-on job service, privacy and on-call |
| Migration | Packaging and middleware | Packaging, protocol, service, gating inventory | Largest API/state-machine redesign |

We should benchmark cold start, installer size, idle RSS, first API response, task-start latency, and failure recovery against the current development build. An explicit task-start latency budget is needed before selecting online capabilities.

## Recommendation

I recommend Option 2 because it changes the authority boundary without routing videos or browser traffic through our service. It supports activation, device limits, feature plans, revocation, update notification, mandatory security updates, and meaningful source hardening while retaining bounded offline work.

Option 1 wins only for long air-gapped operation with accepted crack risk. Option 3 wins when proprietary policy disclosure is unacceptable and we are prepared to operate an always-available service. Option 2 should define versioned lease and job-capability schemas now so later migration is incremental.

## Evidence Coverage And Residual Risk

| Evidence | Option 1 | Option 2 | Option 3 | Tactical work still required |
| --- | --- | --- | --- | --- |
| E001 — Local API | Mitigates | Addresses | Addresses | Authenticate every HTTP/WS route |
| E002 — UI API ownership | Mitigates | Addresses ambient access | Addresses plus remote job authority | Replace direct calls with one transport |
| E003 — Secret/debug defaults | Unaffected by compilation alone | Addresses after rotation/config separation | Same | Rotate secret; disable release debug/docs |
| E004 — No release boundary | Addresses | Addresses | Addresses | Clean CI artifact allowlist |
| E005 — No entitlement gate | Mitigates locally | Addresses with lease/capability | Addresses with server jobs | Inventory use cases and fail closed |
| E006 — Runtime artifacts | Unaffected unless build changes | Mitigates via clean build | Same | Untrack/purge and rotate exposed material |

All options retain risk from memory inspection, instrumentation, and native binary patching. Option 2 limits duration and blast radius through expiry, revocation, scoping, and updates. Only Option 3 protects policy code by never delivering it.

## Migration And Rollout

- Create an allowlisted release, rotate exposed secrets, and split developer/production configuration.
- Centralize frontend transport and authenticate every HTTP/WebSocket route.
- Add one backend entitlement policy called inside use cases, not only routers/UI.
- Build activation, TPM/DPAPI device identity, renewal, revocation, transfers, audit, rate limits, and support tooling.
- Add Tauri packaging and Nuitka standalone sidecar; validate Firefox, extensions, Unicode paths, dialogs, and cleanup.
- Add signed stable/beta feeds, staged rollout, minimum version, notes, progress, deferred restart, and rollback metadata.
- Run enforcement in audit and warning modes before fail-closed production.

Active automation must never be terminated by an update. Server policy should support a global grace extension during control-plane incidents.

## Validation Plan

- Reject missing, stale, previous-launch, replayed, malformed, and wrong-origin local credentials for HTTP and WebSockets.
- Test feature, device, expiry, plan-limit, clock rollback, revocation, replay, duplicate activation, and protocol-version cases at the use-case boundary.
- Copy activation state between two PCs and test TPM and DPAPI paths separately.
- Confirm release artifacts contain no source, source maps, tests, Git metadata, secrets, signing keys, or customer data.
- Tamper with sidecar, installer, manifest, URL, hash, version, and signatures; execution/install must fail closed.
- Test update notice, progress, deferred restart, mandatory update, staged rollout, rollback, interruption, proxy, and offline operation.
- Benchmark current versus candidate cold start, RSS, package size, API response, task start, and license latency on a clean VM.
- Perform an independent reverse-engineering and local API abuse assessment; hidden strings alone are not acceptance.

## Implementation Work Packages

The likely packages are release hygiene and secret rotation; centralized transport; Tauri session bridge; backend authentication; entitlement policy; license/control service; device-key storage; Nuitka build; signed updater; Authenticode CI signing; data migration; admin/support portal; and adversarial tests. A file-level implementation plan should be produced after selecting an option and product rules.

## Open Questions

- Maximum offline grace: none, three days, seven days, or special enterprise license?
- Per-device, named-user, concurrent-seat, or account-capacity licensing?
- Which operations require one-use online capabilities?
- Self-service transfers and hardware-replacement limits?
- What happens to active work when a lease expires or update becomes mandatory?
- Which payment/provider and hosting-region constraints apply?
- Do all bundled browser, Playwright, and CAPTCHA components permit commercial redistribution?
