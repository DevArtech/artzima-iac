output "tunnel_id" {
  value = cloudflare_zero_trust_tunnel_cloudflared.artzima.id
}

output "hostnames" {
  value = local.apps
}

output "tunnel_origin" {
  value = local.traefik_origin
}

output "access_protected" {
  value = sort(keys(local.access))
}

# No tunnel_token output: OpenTofu writes the token straight into Infisical
# (infisical.tf, infisical_secret.tunnel_token), where ESO picks it up.
