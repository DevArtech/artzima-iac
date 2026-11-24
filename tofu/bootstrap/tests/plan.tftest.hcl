# Offline test (mocked providers, no Tailscale or Infisical calls, no credentials):
#   tofu -chdir=tofu/bootstrap test     (part of `make lint`)
mock_provider "tailscale" {
  mock_resource "tailscale_tailnet_key" {
    defaults = { id = "k123", key = "placeholder-not-a-real-key" }
  }
}

mock_provider "infisical" {
  mock_data "infisical_projects" {
    defaults = { id = "proj-k8s", org_id = "org-1" }
  }
  mock_resource "infisical_project" {
    defaults = { id = "proj-k8s-new" }
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
  assert {
    condition     = ephemeral.infisical_secret.ts_client_id.workspace_id == "proj-k8s"
    error_message = "the Tailscale OAuth client is read from the cluster's project (/k8s/tailscale)"
  }
}

run "eso_identity_is_read_only" {
  command = plan

  assert {
    condition     = infisical_identity.eso.role == "no-access" && infisical_identity.eso.name == "artzima-eso"
    error_message = "the ESO identity has no organization-level access"
  }
  assert {
    condition     = length(infisical_project_identity.eso.roles) == 1 && one(infisical_project_identity.eso.roles).role_slug == "viewer"
    error_message = "ESO gets exactly the built-in read-only role (viewer)"
  }
  assert {
    condition     = infisical_project_identity.eso.project_id == "proj-k8s" && infisical_project_identity.eso.project_id != "test-project"
    error_message = "ESO joins ONLY the cluster's project, never the main project"
  }
  assert {
    condition     = infisical_secret.eso_client_id.folder_path == "/tofu" && infisical_secret.eso_client_secret.name == "ESO_CLIENT_SECRET"
    error_message = "its credentials go to /tofu/ESO_CLIENT_* (make eso-bootstrap)"
  }
  assert {
    condition     = infisical_secret.eso_client_id.workspace_id == "test-project" && infisical_secret.tailnet_key["hosts"].workspace_id == "test-project"
    error_message = "ESO and Tailscale credentials stay in the MAIN project (the cluster can't read them)"
  }
  assert {
    condition     = length(infisical_project.k8s) == 0 && length(data.infisical_projects.k8s) == 1
    error_message = "by default the cluster's project already exists (created by the operator) and is looked up"
  }
}

run "no_custom_role_anywhere" {
  command = plan
  variables {
    eso_project_role = "artzima-runtime-secrets"
  }
  expect_failures = [var.eso_project_role]
}

run "eso_never_on_the_main_project" {
  command = plan
  override_data {
    target = data.infisical_projects.k8s[0]
    values = { id = "test-project", org_id = "org-1" }
  }
  expect_failures = [infisical_project_identity.eso]
}

run "k8s_project_created_with_delete_protection" {
  command = plan
  variables {
    k8s_project_create = true
  }
  assert {
    condition     = length(infisical_project.k8s) == 1 && infisical_project.k8s[0].has_delete_protection && infisical_project.k8s[0].slug == "example-k8s-project"
    error_message = "a project created by OpenTofu is delete-protected and uses the configured slug"
  }
  assert {
    condition     = length(data.infisical_projects.k8s) == 0 && infisical_project_identity.eso.project_id == "proj-k8s-new"
    error_message = "the created project is the one ESO joins"
  }
}

run "key_expiry_validated" {
  command = plan
  variables {
    tailnet_key_expiry_days = 120
  }
  expect_failures = [var.tailnet_key_expiry_days]
}
