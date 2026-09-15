# License control plane

This service belongs only on vendor-controlled infrastructure. Never bundle it,
its database, signing private keys, peppers, or administrator token into either
customer executable.

Production requirements:

1. Put the service behind TLS 1.2+ and a reverse proxy/WAF with rate limits on
   activation, renewal, release-check, and download routes.
2. Use a managed PostgreSQL database with encrypted backups. SQLite is rejected
   when `TKAUTO_CONTROL_ENVIRONMENT=production`.
3. Keep the lease and release Ed25519 keys separate in a KMS/HSM-backed secret
   mount. The public halves are compiled into the launcher; private halves never
   enter CI artifacts or Git.
4. Place only audited, Authenticode-signed `.exe` files in the private artifact
   directory. Registration computes SHA-256 and signs immutable metadata.
5. Restrict `/v1/admin/*` at the network/identity-proxy layer in addition to the
   long random bearer token. Rotate the admin token and download-grant secret on
   an incident; rotate signing keys with an overlap period for public keys.
6. Run `uvicorn control_plane.app.main:load_default_app --factory` from the repo
   root (or the equivalent container command). Do not expose FastAPI docs.

The supported production deployment is under `deploy/control-plane/`. It keeps
PostgreSQL private, terminates TLS with Caddy, mounts administrator secrets and
Ed25519 private keys as files, runs the API read-only with dropped capabilities,
and includes health checks. Generate the one-time operator directory with
`scripts/initialize_operator_environment.py`; never commit or distribute it.

Use `scripts/control_plane_admin.py` to create/revoke licenses and register an
already signed backend artifact. Use `scripts/prepare_production_release.ps1`
for the fail-closed Windows release pipeline.

The raw customer license key is submitted only during activation. The database
stores its peppered HMAC digest. Renewal and release checks require proof from
the per-device Ed25519 key. Artifact downloads additionally require a short-lived
HMAC grant in the `Authorization` header; the grant is not placed in URLs/logs.
Each download grant is single-use; a retry obtains a fresh grant through another
signed release check.
