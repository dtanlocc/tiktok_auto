# Production control-plane deployment

This directory deploys only the vendor service. Never send `operator/`, Docker
secrets, PostgreSQL data, or signing private keys to a customer.

1. Point the license domain's A/AAAA records at the server and allow inbound
   TCP 80/443 plus UDP 443. PostgreSQL is not published.
2. Copy `.env.example` to `.env` and replace every example value.
3. Generate `operator/` on the server with
   `python ../../scripts/initialize_operator_environment.py operator --database-host database --database-user <user> --database-name <db> --lease-key-id <id> --release-key-id <id>`.
4. Back up `operator/keys`, `operator/secrets`, and the public-key bundle into
   separate encrypted operator storage. Loss of private signing keys prevents
   release continuity; disclosure requires key rotation.
5. Start with `docker compose up -d --build` and verify
   `https://<domain>/health` from another network.
6. Copy only Authenticode-signed backend artifacts into
   `operator/artifacts/`, then register them through
   `scripts/control_plane_admin.py`.

The application has an in-process abuse limiter. Keep an external WAF/rate
limit in front of Caddy for multi-replica or Internet-scale deployment.
