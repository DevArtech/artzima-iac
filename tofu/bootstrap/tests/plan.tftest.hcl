# Offline test (mocked providers, no Tailscale or Infisical calls, no credentials):
#   tofu -chdir=tofu/bootstrap test     (part of `make lint`)
mock_provider "tailscale" {
  mock_resource "tailscale_tailnet_key" {
    defaults = { id = "k123", key = "placeholder-not-a-real-key" }
  }
}

mock_provider "infisical" {
  mock_data "infisical_projects" {
    defaults = { id = "proj-1", org_id = "org-1" }
  }
  mock_resource "infisical_identity" {
    defaults = { id = "ident-1" }
  }
  mock_resource "infisical_identity_universal_auth_client_secret" {
    defaults = { id = "cs-1", client_id = "client-1", client_secret = "placeholder-secret" }
  }
}

variables {
  state_passphrase     = "placeholder-passphrase-for-tests"
  infisical_project_id = "test-project"
}

run "tailnet_keys_for_hosts_and_routers" {
  command = plan

  assert {
    condition = (tailscale_tailnet_key.this["hosts"].tags == toset(["tag:artzima"])
    && tailscale_tailnet_key.this["routers"].tags == toset(["tag:subnet-router"]))
    error_message = "host and router keys carry their own tags"
  }
  assert {
    condition = alltrue([for k in tailscale_tailnet_key.this :
    k.reusable && k.preauthorized && !k.ephemeral && k.recreate_if_invalid == "always" && k.expiry <= 90 * 86400])
    error_message = "reusable, pre-authorized, non-ephemeral, <= 90 days, re-minted when invalid"
  }
  assert {
    condition = (infisical_secret.tailnet_key["hosts"].folder_path == "/ansible" && infisical_secret.tailnet_key["hosts"].name == "TAILSCALE_AUTHKEY"
    && infisical_secret.tailnet_key["routers"].name == "TAILSCALE_ROUTER_AUTHKEY")
    error_message = "the keys go where the Ansible tailscale role reads them"
  }
}

run "eso_identity_is_read_only" {
  command = plan

  assert {
    condition     = infisical_identity.eso.role == "no-access" && infisical_identity.eso.name == "artzima-eso"
    error_message = "the ESO identity has no organization-level access"
  }
  assert {
    condition     = length(infisical_project_identity.eso.roles) == 1 && one(infisical_project_identity.eso.roles).role_slug == infisical_project_role.eso.slug
    error_message = "ESO uses only the runtime role, never project Viewer"
  }
  assert {
    condition     = infisical_secret.eso_client_id.folder_path == "/tofu" && infisical_secret.eso_client_secret.name == "ESO_CLIENT_SECRET"
    error_message = "its credentials go to /tofu/ESO_CLIENT_* (make eso-bootstrap)"
  }
}

run "eso_permissions_exclude_admin_secrets" {
  command = plan
  assert {
    condition = (
      length(infisical_project_role.eso.permissions_v2) == 2 &&
      alltrue([for p in infisical_project_role.eso.permissions_v2 :
        p.subject == "secrets" && !p.inverted &&
        p.action == toset(["describeSecret", "readValue"]) &&
        jsondecode(p.conditions).environment == "prod"
      ]) &&
      jsondecode(infisical_project_role.eso.permissions_v2[0].conditions).secretPath["$glob"] == "/k8s/**" &&
      jsondecode(infisical_project_role.eso.permissions_v2[1].conditions).secretPath == "/settings" &&
      jsondecode(infisical_project_role.eso.permissions_v2[1].conditions).secretName == "PUBLIC_DOMAIN"
    )
    error_message = "ESO must read only runtime /k8s secrets and PUBLIC_DOMAIN, never admin folders."
  }
}

run "key_expiry_validated" {
  command = plan
  variables {
    tailnet_key_expiry_days = 120
  }
  expect_failures = [var.tailnet_key_expiry_days]
}
