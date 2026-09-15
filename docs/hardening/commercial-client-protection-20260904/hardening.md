# Security Hardening Review: Commercial TikTok Automation Client

## Evidence Basis

I inspected the current React-to-FastAPI boundary and build configuration at revision `ee06783a2a2f6ce38945a8845610fc72b0ad7e57`. The main issue is structural: privileged automation is exposed through a fixed unauthenticated local API, while the deliverable remains a source-based web/Python application. Evidence and hashes are in [context.md](context.md).

## Constraints

We assume Windows x64 first, local browser/video execution, a short offline grace period, and no supplied latency or memory budget. Client-side encryption is not treated as an absolute secrecy boundary; durable authority and high-value secrets must leave the customer machine.

## Opportunity Portfolio

| Opportunity | Evidence | Options | Recommendation | Proposal |
| --- | --- | --- | --- | --- |
| Create a commercial client trust boundary | Fixed unauthenticated API (E001/E002/E005), source-owned secret and release model (E003/E004/E006) | Hardened local; hybrid control plane; cloud-authorized execution | Use hybrid now and preserve a path to cloud authorization for premium actions | [Proposal](proposals/commercial-client-trust-boundary.md) |

## Recommendation Summary

I recommend a Tauri shell, Nuitka-compiled sidecar, authenticated per-launch local channel, remote license/control service, TPM-backed device identity with DPAPI fallback, short-lived signed leases, and signed releases. This makes a copied key insufficient and enables revocation without moving browser/video traffic off the PC.

If proprietary logic must remain secret against a well-funded reverse engineer, it cannot ship: per-job authority and policy must live on the control plane. That stronger option creates a permanent online availability and operations commitment.

## Next Decisions

- Maximum offline grace period.
- Per-device, named-user, concurrent-seat, or account-capacity licensing.
- Premium actions that require a one-use server capability.
- Stable-only or stable-plus-beta release channels.
- Rotation of the source-owned secret and removal of tracked credential/runtime artifacts before packaging.
