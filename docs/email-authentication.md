# Email authentication: SPF, DKIM, DMARC

Decided 6 Nov 2026: run all three, so nobody can send mail as pipridge.
**Cost: none.** They are DNS TXT records at the domain's DNS host (included
with any registrar or Cloudflare's free plan). DKIM signing is done by the
mail provider at no extra charge (Amazon SES, Postmark, Google Workspace,
Microsoft 365). DMARC reports are free to receive; a dashboard is optional
(Postmark's weekly DMARC digest is free).

The records below use `pipridge.com` and Amazon SES as examples. Replace
them with the real domain and provider: each provider shows the exact SPF
include and DKIM records in its console.

## Records

| Name | Type | Value | Purpose |
|---|---|---|---|
| `pipridge.com` | TXT | `v=spf1 include:amazonses.com -all` | Only the provider may send for the domain; everything else fails |
| `<selector>._domainkey.pipridge.com` | CNAME or TXT | from the provider (SES: three CNAMEs) | The provider signs each message (DKIM, 2048-bit) |
| `_dmarc.pipridge.com` | TXT | `v=DMARC1; p=none; rua=mailto:dmarc@pipridge.com; adkim=s; aspf=s; pct=100` | Receivers report and apply the policy |

Notes:

- One SPF record per domain. Add every service that sends as the domain to
  the same record (e.g. `include:amazonses.com include:_spf.google.com`).
  SPF allows at most 10 DNS lookups.
- Send from a subdomain for transactional mail (`mail.pipridge.com`, the
  SES "custom MAIL FROM"), so SPF aligns and the root domain stays clean.
- `MAIL_FROM` in the API's settings must use the authenticated domain:
  `MAIL_FROM="pipridge <no-reply@pipridge.com>"`.
- A domain or subdomain that never sends mail gets `v=spf1 -all` and
  `v=DMARC1; p=reject`, so it cannot be spoofed either.

## Rollout (industry practice, RFC 7489 and the Google/Yahoo sender rules of 2024)

1. Before the first email goes out: SPF, DKIM and DMARC `p=none` (monitor).
   Without them, mail to Gmail and Yahoo lands in spam or is rejected.
2. After 2 to 4 weeks of clean reports (every legitimate source passes):
   `p=quarantine; pct=25`, then `pct=100`.
3. Then `p=reject`. Spoofed mail as pipridge is refused by receivers.
4. Optional, later: MTA-STS and TLS-RPT (TLS for incoming mail), BIMI (the
   logo in inboxes; needs `p=quarantine` or `p=reject`, and a VMC certificate
   which does cost money).

## Check

```
dig +short TXT pipridge.com
dig +short TXT _dmarc.pipridge.com
dig +short CNAME <selector>._domainkey.pipridge.com
```

A test message to a Gmail address shows `spf=pass`, `dkim=pass` and
`dmarc=pass` under "Show original".

## Owner

IT and security role (staff spec). Review the DMARC reports monthly; a new
service that sends as the domain is added to SPF and DKIM before it sends.
