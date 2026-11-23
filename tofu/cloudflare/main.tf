# Cloudflare side of the ONE tunnel, as code. Every hostname comes from
# /apps.yaml, the same file that generates the Traefik IngressRoutes
# (k8s/apps/routes), so adding an app is one edit there.
locals {
  apps_file = yamldecode(file("${path.module}/../../apps.yaml"))
  # The domain is private: Infisical /settings/PUBLIC_DOMAIN (infisical.tf).
  domain = nonsensitive(lookup(local.settings, "PUBLIC_DOMAIN", "missing.invalid"))
  apps   = { for a in local.apps_file.apps : a.name => "${a.name}.${local.domain}" }
  # Apps whose clients can't pass a browser login (e.g. the Bitwarden apps for
  # Vaultwarden): apps.yaml `access: never`. Access on them is refused below.
  no_access = [for a in local.apps_file.apps : a.name if try(a.access, "optional") == "never"]

  # The tunnel's single origin: Traefik inside the cluster.
  t              = local.apps_file.traefik
  traefik_origin = "http://${local.t.service}.${local.t.namespace}.svc.cluster.local:${local.t.port}"

  # Access rules (with your email addresses) from the variable (tests) or Infisical.
  access_list = var.access_apps != null ? var.access_apps : [
    for a in jsondecode(nonsensitive(lookup(local.settings, "CLOUDFLARE_ACCESS_APPS", "[]"))) : {
      app              = a.app
      emails           = try(a.emails, [])
      email_domains    = try(a.email_domains, [])
      session_duration = try(a.session_duration, "24h")
    }
  ]
  access = { for a in local.access_list : a.app => a }

  account_id = nonsensitive(lookup(local.settings, "CLOUDFLARE_ACCOUNT_ID", ""))
  zone_id    = nonsensitive(lookup(local.settings, "CLOUDFLARE_ZONE_ID", ""))
}

provider "cloudflare" {
  # Ephemeral (infisical.tf): provider configuration is never written to state.
  api_token = ephemeral.infisical_secret.cloudflare_api_token.value
}

resource "random_id" "tunnel_secret" {
  byte_length = 32

  # Guardrail: destroying this takes every public app offline. A deliberate removal
  # (e.g. retiring an app, rotating the tunnel) = remove this line in a reviewed PR.
  lifecycle {
    prevent_destroy = true
  }
}

resource "cloudflare_zero_trust_tunnel_cloudflared" "artzima" {
  account_id    = local.account_id
  name          = var.tunnel_name
  config_src    = "cloudflare" # remotely managed: cloudflared in k8s only needs the token
  tunnel_secret = random_id.tunnel_secret.b64_std

  # Guardrail: destroying this takes every public app offline. A deliberate removal
  # (e.g. retiring an app, rotating the tunnel) = remove this line in a reviewed PR.
  lifecycle {
    prevent_destroy = true
  }
}

resource "cloudflare_zero_trust_tunnel_cloudflared_config" "artzima" {
  account_id = local.account_id
  tunnel_id  = cloudflare_zero_trust_tunnel_cloudflared.artzima.id
  config = {
    # One rule per app host, all to Traefik (which routes by Host), then 404.
    ingress = concat(
      [for name in sort(keys(local.apps)) : {
        hostname = local.apps[name]
        service  = local.traefik_origin
      }],
      [{ service = "http_status:404" }],
    )
  }
}

resource "cloudflare_dns_record" "app" {
  for_each = local.apps
  zone_id  = local.zone_id
  name     = each.value
  type     = "CNAME"
  content  = "${cloudflare_zero_trust_tunnel_cloudflared.artzima.id}.cfargotunnel.com"
  proxied  = true
  ttl      = 1 # automatic (required for proxied records)
  comment  = "artzima tunnel: managed by tofu/cloudflare from apps.yaml"

  # Guardrail: destroying this takes every public app offline. A deliberate removal
  # (e.g. retiring an app, rotating the tunnel) = remove this line in a reviewed PR.
  lifecycle {
    prevent_destroy = true
  }
}

# ---- optional Cloudflare Access (var.access_apps) ----------------------------
resource "cloudflare_zero_trust_access_policy" "app" {
  for_each   = local.access
  account_id = local.account_id
  name       = "artzima ${each.key}: allow"
  decision   = "allow"
  include = concat(
    [for e in each.value.emails : { email = { email = e } }],
    [for d in each.value.email_domains : { email_domain = { domain = d } }],
  )

  lifecycle {
    precondition {
      condition     = contains(keys(local.apps), each.key)
      error_message = "access_apps entry '${each.key}' is not an app in /apps.yaml."
    }
    precondition {
      condition     = !contains(local.no_access, each.key)
      error_message = "'${each.key}' is `access: never` in /apps.yaml: its native clients can't pass Cloudflare Access. Remove it from access_apps."
    }
  }
}

resource "cloudflare_zero_trust_access_application" "app" {
  for_each         = local.access
  account_id       = local.account_id
  name             = "artzima ${each.key}"
  type             = "self_hosted"
  domain           = local.apps[each.key]
  session_duration = each.value.session_duration
  policies = [{
    id         = cloudflare_zero_trust_access_policy.app[each.key].id
    precedence = 1
  }]
}

data "cloudflare_zero_trust_tunnel_cloudflared_token" "artzima" {
  account_id = local.account_id
  tunnel_id  = cloudflare_zero_trust_tunnel_cloudflared.artzima.id
}
