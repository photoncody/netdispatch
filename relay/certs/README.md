# Custom CA certificates (optional)

Place PEM-encoded CA certificates here (`*.pem` or `*.crt`) **before building
the relay image** if your hub is served over HTTPS with a certificate from a
private CA.

- Every file is compiled into the iPXE binaries as a trusted root
  (`TRUST=...`), replacing iPXE's default trust in public CAs.
- `*.crt` files are also added to the relay's system trust store so the
  heartbeat (`curl`) trusts the hub.

If the hub uses a publicly trusted certificate, leave this directory empty.
Alternatively, set `BOOT_BASE_URL` on the hub and `CENTRAL_SERVER_URL` on the
relay to a plain-HTTP LAN address and skip TLS for boot traffic entirely.
