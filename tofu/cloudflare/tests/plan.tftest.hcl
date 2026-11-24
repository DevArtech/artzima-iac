# Offline test (mocked providers, no Cloudflare API calls, no credentials):
#   tofu -chdir=tofu/cloudflare test     (part of `make lint`)
mock_provider "cloudflare" {
  mock_resource "cloudflare_zero_trust_tunnel_cloudflared" {
    defaults = { id = "00000000-0000-4000-8000-000000000000" }
  }
  mock_resource "cloudflare_zero_trust_access_policy" {
    defaults = { id = "11111111-1111-4111-8111-111111111111" }
  }
}
mock_provider "random" {}

# Infisical mocked too: no network, no credentials. /settings returns test values.
mock_provider "infisical" {
  mock_data "infisical_secrets" {
    defaults = {
      secrets = {
        PUBLIC_DOMAIN         = { value = "example.test", comment = "", secret_type = "shared" }
        CLOUDFLARE_ACCOUNT_ID = { value = "test-account", comment = "", secret_type = "shared" }
        CLOUDFLARE_ZONE_ID    = { value = "test-zone", comment = "", secret_type = "shared" }
      }
    }
  }
}

variables {
  state_passphrase         = "placeholder-passphrase-for-tests"
  infisical_project_id     = "test-project"
  infisical_k8s_project_id = "test-k8s-project"
  access_apps              = [] # default (null) would read CLOUDFLARE_ACCESS_APPS from Infisical
}

run "one_rule_and_record_per_app" {
  command = plan # mocks; plan: prevent_destroy blocks the test teardown of an apply

  assert {
    condition     = length(cloudflare_dns_record.app) == length(local.apps)
    error_message = "every apps.yaml entry needs exactly one DNS record"
  }
  assert {
    condition     = length(cloudflare_zero_trust_tunnel_cloudflared_config.artzima.config.ingress) == length(local.apps) + 1
    error_message = "one ingress rule per app + the 404 catch-all"
  }
  assert {
    condition     = cloudflare_zero_trust_tunnel_cloudflared_config.artzima.config.ingress[length(local.apps)].service == "http_status:404"
    error_message = "last ingress rule must be the 404 catch-all"
  }
  assert {
    condition = alltrue([for r in slice(cloudflare_zero_trust_tunnel_cloudflared_config.artzima.config.ingress, 0, length(local.apps)) :
    r.service == "http://traefik.traefik.svc.cluster.local:80"])
    error_message = "every app rule must point at the single Traefik origin"
  }
  assert {
    condition     = alltrue([for r in cloudflare_dns_record.app : r.proxied && r.type == "CNAME" && r.content == "00000000-0000-4000-8000-000000000000.cfargotunnel.com"])
    error_message = "DNS records must be proxied CNAMEs to <tunnel-id>.cfargotunnel.com"
  }
  assert {
    condition     = cloudflare_zero_trust_tunnel_cloudflared.artzima.config_src == "cloudflare"
    error_message = "tunnel must be remotely managed"
  }
  assert {
    condition     = length(cloudflare_zero_trust_access_application.app) == 0
    error_message = "no Access apps by default"
  }
  assert {
    condition     = alltrue([for h in values(local.apps) : endswith(h, ".example.test")])
    error_message = "hostnames use the domain from Infisical /settings/PUBLIC_DOMAIN"
  }
  assert {
    condition     = infisical_secret.tunnel_token.folder_path == "/k8s/cloudflared" && infisical_secret.tunnel_token.name == "TUNNEL_TOKEN"
    error_message = "the tunnel token must land where ESO reads it (k8s/secrets/cloudflared/tunnel-token.yaml)"
  }
  assert {
    condition     = infisical_secret.tunnel_token.workspace_id == "test-k8s-project" && data.infisical_secrets.settings.workspace_id == "test-project"
    error_message = "the tunnel token goes to the cluster's project; /settings is read from the main project"
  }
}

run "access_from_infisical_json" {
  command = plan
  variables {
    access_apps = null
  }
  override_data {
    target = data.infisical_secrets.settings
    values = {
      secrets = {
        PUBLIC_DOMAIN          = { value = "example.test", comment = "", secret_type = "shared" }
        CLOUDFLARE_ACCOUNT_ID  = { value = "test-account", comment = "", secret_type = "shared" }
        CLOUDFLARE_ZONE_ID     = { value = "test-zone", comment = "", secret_type = "shared" }
        CLOUDFLARE_ACCESS_APPS = { value = "[{\"app\":\"grafana\",\"emails\":[\"operator@example.test\"]}]", comment = "", secret_type = "shared" }
      }
    }
  }
  assert {
    condition     = length(cloudflare_zero_trust_access_application.app) == 1 && contains(keys(cloudflare_zero_trust_access_application.app), "grafana")
    error_message = "Access apps are read from Infisical /settings/CLOUDFLARE_ACCESS_APPS when the variable is null"
  }
}

run "access_for_listed_apps" {
  command = plan
  variables {
    access_apps = [
      { app = "grafana", emails = ["operator@example.test"] },
      { app = "wizarr", email_domains = ["example.test"], session_duration = "8h" },
    ]
  }
  assert {
    condition     = length(cloudflare_zero_trust_access_application.app) == 2
    error_message = "one Access application per access_apps entry"
  }
  assert {
    condition     = cloudflare_zero_trust_access_application.app["wizarr"].session_duration == "8h"
    error_message = "per-app session duration"
  }
}

run "access_for_unknown_app_is_rejected" {
  command = plan
  variables {
    access_apps = [{ app = "not-an-app", emails = ["operator@example.test"] }]
  }
  expect_failures = [cloudflare_zero_trust_access_policy.app]
}

run "access_refused_for_access_never_app" {
  command = plan
  variables {
    access_apps = [{ app = "vault", emails = ["operator@example.test"] }]
  }
  expect_failures = [cloudflare_zero_trust_access_policy.app]
}
