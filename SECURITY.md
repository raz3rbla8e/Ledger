# Security model

Ledger handles bank data, so the design starts from "what could go wrong".

## What Ledger can and can't do
- **It can't move money.** It never has a bank login, and there's no code anywhere that sends payments or changes anything at a bank.
- **It reads email, read-only in practice.** It selects mailboxes read-only, searches only for bank and Interac senders, and fetches with `BODY.PEEK`, so nothing is marked as read, moved or deleted.
- **A Gmail app password technically grants full mailbox access.** Treat it like a password: revoke it at myaccount.google.com/apppasswords if your PC is ever compromised. Changing your Google password also revokes every app password.

## Threats and defences
| Threat | Defence |
|---|---|
| **Fake bank emails** (someone emails you a forged "Tangerine purchase" to plant data or trigger alerts) | Only Gmail's own topmost `Authentication-Results` header is trusted. A bank-domain email must pass DMARC, or DKIM/SPF aligned with the bank's domain, or it's quarantined and logged. Lower headers, which a sender could forge, are ignored. |
| **Secrets on disk** | App passwords, tokens and the dashboard password live in Windows Credential Manager (DPAPI, tied to your Windows login) or the macOS Keychain. Any found in `.env` are moved into the keychain on start. On Linux they fall back to `.env` with chmod 600. Logs redact `password=`, `token=` and `key=` values. |
| **Someone on your network or the internet reaching the dashboard** | It listens on `127.0.0.1` only and refuses to bind anything else without a password. Phone access goes through **Tailscale Serve**: tailnet-only, WireGuard-encrypted, never public. Requests that aren't from localhost need the dashboard password (12+ characters), and 8 wrong attempts lock that IP out for 15 minutes. |
| **A malicious website in your browser attacking `localhost:8765`** | **DNS rebinding:** a `Host` header allowlist (localhost, `*.ts.net`, and configured names) blocks it. **CSRF:** every write needs a custom `X-Ledger-Req` header, which cross-site forms can't send, plus an Origin check. **Response headers:** a strict CSP (`script-src 'self'`, `frame-ancestors 'none'`), `X-Frame-Options: DENY`, `nosniff` and `no-referrer`. |
| **Phone integrations being abused** | Each has its own long random token, compared in constant time. The **widget token** can read only a few numbers. The **Apple Pay token** can only add a purchase, which is validated (amount range, merchant length, date format) and size-capped. |
| **Supply-chain attacks** | No third-party Python packages. Chart.js is vendored, not loaded from a CDN. |
| **Data loss** | Nightly SQLite online backups to `~/Ledger/backups` (14 kept). |
| **Personal data in the repo** | The repo ships a generic config and a fictional demo profile. Your real profile lives only in `~/Ledger/config.toml`, which is git-ignored. |

## Things to know
- **Telegram bot messages are not end-to-end encrypted.** They include amounts and merchant names. Turn Telegram off in `config.toml` if that matters to you; email reports and the dashboard still work.
- **AI providers.** Local AI (Ollama) keeps everything on your PC. If you set `ai.provider = "anthropic"` or `ai.chat = "claude"`, merchant names, email text and money summaries are sent to the Claude API. The same applies to anything Claude Desktop reads through the MCP tools.
- **Malware on your PC.** Ledger can't protect against malware already running as you. Keep Windows updated and use full-disk encryption (BitLocker).

Found a problem? Open an issue without including any personal data.
