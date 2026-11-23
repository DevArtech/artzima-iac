# tofu/cloudflare: the Cloudflare Tunnel as code

A separate root module (its own state, which is encrypted). It manages:

- the remotely-managed tunnel `artzima` (`cloudflare_zero_trust_tunnel_cloudflared`,
  `config_src = "cloudflare"`)
- its ingress config: one rule per `/apps.yaml` host to
  `http://traefik.traefik.svc.cluster.local:80`, plus a `http_status:404` catch-all
- proxied DNS `CNAME <app>.<domain> → <tunnel-id>.cfargotunnel.com`
- optionally, Cloudflare Access policies and applications per app
- **the tunnel token in Infisical** (`/k8s/cloudflared/TUNNEL_TOKEN`, an
  `infisical_secret` with write-only `value_wo`). ESO syncs it into the cluster.

```bash
make cf-init cf-plan cf-apply   # secrets + domain from Infisical (docs/secrets.md)
```

Inputs, all from Infisical (`infisical.tf`):

| Infisical | Used as | In state? |
|---|---|---|
| `/tofu/CLOUDFLARE_API_TOKEN` | provider token (ephemeral) | no |
| `/tofu/TOFU_STATE_PASSPHRASE` | state/plan encryption (`TF_VAR_state_passphrase`, from `make`) | no |
| `/settings/PUBLIC_DOMAIN`, `CLOUDFLARE_ACCOUNT_ID`, `CLOUDFLARE_ZONE_ID` | resource attributes | yes (not secret) |
| `/settings/CLOUDFLARE_ACCESS_APPS` (optional JSON) | Access apps + allowed emails | yes (private) |

The tunnel secret and token stay in the (encrypted) state: the Cloudflare provider has
no write-only or ephemeral form for them (see `infisical.tf`). `tofu test` (mocked
providers, offline) checks the rules, records, Access handling and where the token lands.
