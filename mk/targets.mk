# artzima-iac: main entry points. `make help` lists them.
# Tools live in $(ARTZIMA_TOOLS) (installed by `make deps`, no sudo).

ARTZIMA_TOOLS ?= $(HOME)/.local/share/artzima-tools
export ARTZIMA_TOOLS
export PATH := $(ARTZIMA_TOOLS)/bin:$(ARTZIMA_TOOLS)/venv/bin:$(PATH)
export ANSIBLE_COLLECTIONS_PATH := $(ARTZIMA_TOOLS)/collections
export ANSIBLE_CONFIG := $(CURDIR)/ansible/ansible.cfg
export TF_PLUGIN_CACHE_DIR := $(ARTZIMA_TOOLS)/tofu-plugins

PY        := $(ARTZIMA_TOOLS)/venv/bin/python
# Secrets: Infisical (docs/secrets.md). WITH_INF runs a command with the admin
# machine identity and project settings loaded from (never committed; paths
# overridable with ARTZIMA_INFISICAL_ENV / ARTZIMA_INFISICAL_PROJECT / ARTZIMA_LOCAL_ENV):
#   ~/.config/artzima/infisical.env  ~/.config/artzima/infisical-project.env  ./local.env
# plus any --set VAR=/folder/KEY fetched from Infisical into its environment (never to disk).
WITH_INF  := $(PY) $(CURDIR)/scripts/infisical_exec.py
PLAYBOOK  := cd ansible && $(WITH_INF) --optional NAS_SUDO_PASSWORD=/ansible/NAS_SUDO_PASSWORD -- ansible-playbook
# The state passphrase must exist before OpenTofu reads state: from Infisical.
TOFU_ENV  := $(WITH_INF) --set TF_VAR_state_passphrase=/tofu/TOFU_STATE_PASSPHRASE --
POSTINST_WEBHOOK_URL ?=
export POSTINST_WEBHOOK_URL
ANSWER_PORT ?= 8000
LIMIT ?=
ANSIBLE_ARGS ?=
_limit = $(if $(LIMIT),--limit $(LIMIT),)
# Every APPLY target shows what it will do (tofu plan / the playbook's hosts+tasks /
# the Infisical names) and then waits for "yes". AUTO_APPROVE=1 skips the question
# (e.g. `make up AUTO_APPROVE=1`); ANSIBLE_ARGS="--check --diff" is a dry run (no question).
AUTO_APPROVE ?= 0
export AUTO_APPROVE
CONFIRM := $(CURDIR)/scripts/confirm.sh

# ansible_apply <playbook> [<extra args for the real run, e.g. -K>]: list hosts + tasks,
# confirm, run. The listing gets no -K, so the sudo password is asked once, for the run.
define ansible_apply
	$(PLAYBOOK) playbooks/$(1).yml $(_limit) --list-hosts --list-tasks
	@$(CONFIRM) "ansible: run playbooks/$(1).yml$(if $(LIMIT), on $(LIMIT),)" $(ANSIBLE_ARGS)
	$(PLAYBOOK) playbooks/$(1).yml $(_limit) $(2) $(ANSIBLE_ARGS)
endef

# tofu_apply <dir> <planfile>: init, plan to a file, confirm, apply exactly that plan.
define tofu_apply
	$(TOFU_ENV) tofu -chdir=$(1) init -input=false >/dev/null
	$(TOFU_ENV) tofu -chdir=$(1) plan -input=false -out=$(2)
	@$(CONFIRM) "tofu: apply the plan above ($(1))"
	$(TOFU_ENV) tofu -chdir=$(1) apply -input=false $(2)
	@$(PY) scripts/tofu_state_backup.py || echo "WARNING: the OpenTofu state was NOT copied to the NAS; run make tofu-state-backup" >&2
endef

.DEFAULT_GOAL := help
.PHONY: help deps render answers answer-server clean-answers \
        preflight bootstrap up verify gen-secrets ids-init ids-plan ids-apply \
        site common network cluster pbs storage nut nut-check tailscale host-prep \
        proxmox-api lxc-devices ts-routers jellyfin llm firewalla-plan firewalla-apply \
        tofu-init tofu-plan tofu-apply tofu-destroy tofu-state-backup talos-env flux-bootstrap eso-bootstrap \
        cf-init cf-plan cf-apply b2-init b2-plan b2-apply pbs-offsite nas-backup nas-folders media-adopt emergency-kit dr-volsync-restore dr-pdf dr-tools dr-secrets-from-cluster hooks check-public answer-check secrets-check secrets-folders secrets-migrate-k8s \
        lint lint-yaml lint-ansible lint-tofu lint-k8s lint-flux lint-python lint-images

help: ## Show targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-16s\033[0m %s\n",$$1,$$2}'

deps: ## Install tofu/helm/flux/kustomize/kubeconform/gitleaks/... + ansible venv (+infisicalsdk) + collections
	scripts/install-tools.sh

hooks: ## Install the gitleaks pre-commit hook (the repo is PUBLIC)
	scripts/install-git-hooks.sh

check-public: ## Fail on any secret, personal data or SOPS leftover in what git would publish
	scripts/check_public.sh

secrets-check: ## List required Infisical keys that are missing (names only, never values)
	$(PY) scripts/secrets_check.py
secrets-folders: ## Create the Infisical folder layout of secrets.manifest.yaml (folders only, no values)
	$(PY) scripts/secrets_check.py --create-folders
MIGRATE_ARGS ?=
secrets-migrate-k8s: ## ONE-TIME: copy /k8s/** from the main Infisical project to the cluster's project (dry run; MIGRATE_ARGS=--apply, later --delete-old)
	$(PY) scripts/secrets_migrate_k8s.py $(MIGRATE_ARGS)

GEN_ARGS ?=
gen-secrets: ## Create every MISSING generated secret straight in Infisical (names shown, values never). GEN_ARGS=--dry-run
	$(PY) scripts/gen_secrets.py $(GEN_ARGS)

# ================================================================ THE FOUR PHASES
# docs/OPERATOR-SETUP.md walks through them. Each step shows its plan and asks "yes".
PREFLIGHT_ARGS ?=
preflight: ## PHASE: refuse unless Infisical keys, hosts (MAC + disk serial) and the NAS match the repo (read-only)
	$(PY) scripts/preflight.py $(PREFLIGHT_ARGS)

bootstrap: ## PHASE: folders, generated secrets, then every DERIVED credential (Tailscale keys, ESO identity, B2 keys, tunnel)
	@# folders (no values); stops here, listing names, while one of YOUR keys is missing
	$(PY) scripts/secrets_check.py --create-folders --operator-only
	$(MAKE) --no-print-directory gen-secrets
	$(MAKE) --no-print-directory ids-apply
	$(MAKE) --no-print-directory b2-apply
	$(MAKE) --no-print-directory cf-apply
	@echo "bootstrap done. next: USB installs (docs/OPERATOR-SETUP.md), then make preflight && make up"

UP_STEPS := site tofu-apply nas-folders lxc-devices ts-routers jellyfin llm pbs-offsite nas-backup eso-bootstrap flux-bootstrap
up: ## PHASE: hosts -> VMs/LXCs/Talos -> devices -> off-site -> ESO -> Flux (preflight first; each step confirms)
	$(PY) scripts/preflight.py
	@for t in $(UP_STEPS); do echo; echo "=== make $$t"; $(MAKE) --no-print-directory $$t || exit 1; done
	@echo "up done. next: make verify (Flux needs ~15 min to reconcile everything)"

verify: ## PHASE: read-only health report (nodes, Flux, HelmReleases, ExternalSecrets, certs, backups, restore tests, URLs)
	$(KUBE) $(WITH_INF) --optional PUBLIC_DOMAIN=/settings/PUBLIC_DOMAIN -- $(PY) scripts/verify.py

render: ## Regenerate files derived from the inventory and /apps.yaml (cluster-vars, routes, addresses.md)
	$(PY) scripts/render.py

# ---------------------------------------------------------------- 1. install
# Admin SSH keys, mailbox and (optional) root hash come from Infisical, in memory.
ANSWER_ENV := $(WITH_INF) --set ADMIN_SSH_KEYS=/settings/ADMIN_SSH_KEYS --set ADMIN_EMAIL=/settings/ADMIN_EMAIL \
  --optional ROOT_PASSWORD_HASHED=/bare-metal/ROOT_PASSWORD_HASHED --
answers: ## Render + validate every answer file in a temp dir (nothing is written to the repo)
	$(ANSWER_ENV) $(PY) install/render_answers.py
answer-check: ## Same, offline, with placeholder keys (no Infisical needed)
	$(PY) install/render_answers.py --allow-placeholder

answer-server: ## Serve answers (rendered in memory at start) to the Proxmox auto-installer on :$(ANSWER_PORT)
	$(ANSWER_ENV) $(PY) install/answer_server.py --port $(ANSWER_PORT)

clean-answers: ## Delete install webhook reports (install/answers/installed)
	rm -rf install/answers

# ---------------------------------------------------------------- 2. ansible
site: ## Whole bare-metal bring-up (all playbooks in order; lists tasks, asks "yes")
	$(call ansible_apply,site)
common network cluster pbs storage nut tailscale host-prep proxmox-api lxc-devices ts-routers jellyfin llm: ## Run one playbook (e.g. make nut LIMIT=host01; make llm = the recs LLM LXC)
	$(call ansible_apply,$@)
nut-check: ## Only the NUT killpower checks (read-only)
	$(PLAYBOOK) playbooks/nut.yml --limit host01 --tags nut_check $(ANSIBLE_ARGS)

# ---------------------------------------------------------------- 2b. firewalla (firewalla/baseline.yaml)
# Over SSH through the WireGuard VPN; only rules marked artzima-iac:<id> are ever changed.
firewalla-plan: ## Firewalla: rule diff against firewalla/baseline.yaml + PASS/FAIL of its settings (read-only)
	$(PY) scripts/firewalla.py plan
firewalla-apply: ## Firewalla: plan, confirm, then create/replace/delete the MANAGED rules (never the others)
	$(PY) scripts/firewalla.py plan
	@$(CONFIRM) "firewalla: apply the rule changes above (managed rules only)"
	$(PY) scripts/firewalla.py apply

# ---------------------------------------------------------------- 3. tofu
tofu-init: ## tofu init (providers pinned in tofu/versions.tf + lock file)
	$(TOFU_ENV) tofu -chdir=tofu init -input=false
tofu-plan: ## Plan (secrets from Infisical; plan file is encrypted)
	$(TOFU_ENV) tofu -chdir=tofu plan -out=artzima.tfplan
tofu-apply: ## Plan + confirm + apply (VMs, LXCs, Talos bootstrap, kubeconfig)
	$(call tofu_apply,tofu,artzima.tfplan)
tofu-state-backup: ## Copy the encrypted OpenTofu state to the NAS (-> B2 nightly); also runs after every tofu apply
	$(PY) scripts/tofu_state_backup.py
tofu-destroy: ## Destroy everything OpenTofu created (tofu asks again)
	@$(CONFIRM) "DESTROY every VM/LXC of tofu/ (the NAS and backups are untouched)"
	$(TOFU_ENV) tofu -chdir=tofu destroy
talos-env: ## Print exports for talosctl/kubectl
	@echo "export TALOSCONFIG=$(CURDIR)/tofu/out/talosconfig KUBECONFIG=$(CURDIR)/tofu/out/kubeconfig"

# ---------------------------------------------------------------- 3b. cloudflare tunnel (tofu/cloudflare, reads /apps.yaml)
cf-init: ## Init tofu/cloudflare (tunnel, ingress rules, DNS, Access)
	$(TOFU_ENV) tofu -chdir=tofu/cloudflare init -input=false
cf-plan: ## Plan the Cloudflare side from /apps.yaml (secrets + domain from Infisical)
	$(TOFU_ENV) tofu -chdir=tofu/cloudflare plan -out=cloudflare.tfplan
cf-apply: ## Plan + confirm + apply; writes the tunnel token into Infisical /k8s/cloudflared/TUNNEL_TOKEN
	$(call tofu_apply,tofu/cloudflare,cloudflare.tfplan)

# ---------------------------------------------------------------- 3c. backblaze B2 (tofu/backblaze, reads backups.yml)
b2-init: ## Init tofu/backblaze (off-site backup buckets + scoped keys)
	$(TOFU_ENV) tofu -chdir=tofu/backblaze init -input=false
b2-plan: ## Plan B2 buckets/keys (admin key from Infisical /tofu/B2_ADMIN_*)
	$(TOFU_ENV) tofu -chdir=tofu/backblaze plan -out=backblaze.tfplan
b2-apply: ## Plan + confirm + apply; writes each bucket's backup + maintenance key into Infisical
	$(call tofu_apply,tofu/backblaze,backblaze.tfplan)
pve-root-password: ## Set root@pam's password on the PVE hosts and PBS from Infisical /operator/PVE_ROOT_PASSWORD (yours; for the web UIs and console)
	@$(CONFIRM) "set root's password on all PVE hosts and PBS from Infisical"
	$(WITH_INF) --set PVE_ROOT_PASSWORD=/operator/PVE_ROOT_PASSWORD -- $(PY) scripts/pve_root_password.py

pbs-offsite: ## PBS: S3 datastore on the B2 bucket + local sync/prune/GC jobs (after make b2-apply)
	$(call ansible_apply,pbs-offsite)
# The NAS user needs sudo. Its password comes from Infisical /ansible/NAS_SUDO_PASSWORD (optional);
# without that entry, run with NAS_ANSIBLE_ARGS=-K to type it.
NAS_ANSIBLE_ARGS ?=
nas-backup: ## Backrest (restic) on the NAS -> B2 (after make b2-apply); -e nas_backup_deploy=false = render only
	$(call ansible_apply,nas-backup,$(NAS_ANSIBLE_ARGS))
nas-folders: ## NAS sub-folders that LXCs mount read-write (Jellyfin transcodes) + their marker files; before lxc-devices
	$(call ansible_apply,nas-folders,$(NAS_ANSIBLE_ARGS))

# ---------------------------------------------------------------- 3d. derived identities (tofu/bootstrap)
ids-init: ## Init tofu/bootstrap (Tailscale auth keys + ESO machine identity)
	$(TOFU_ENV) tofu -chdir=tofu/bootstrap init -input=false
ids-plan: ## Plan the derived identities
	$(TOFU_ENV) tofu -chdir=tofu/bootstrap plan -out=bootstrap.tfplan
ids-apply: ## Plan + confirm + apply; writes /ansible/TAILSCALE_*AUTHKEY and /tofu/ESO_CLIENT_* into Infisical
	$(call tofu_apply,tofu/bootstrap,bootstrap.tfplan)

# ---------------------------------------------------------------- disaster recovery (docs/disaster-recovery.md)
EK_ARGS ?=
emergency-kit: ## Print the EMERGENCY KIT checklist (names + last rotation, never values). EK_ARGS=--infisical adds Infisical dates
	$(PY) scripts/emergency_kit.py $(EK_ARGS)
AS_OF ?=
dr-volsync-restore: ## Print VolSync ReplicationDestinations restoring every backed-up PVC (AS_OF=<RFC3339> = before the disaster)
	@$(PY) scripts/dr_volsync_restore.py $(if $(AS_OF),--as-of $(AS_OF),)
DR_PDF_ARGS ?=
dr-pdf: ## Runbook + emergency-kit checklist -> dist/artzima-disaster-recovery.pdf (footer: date + commit) for the kit
	$(PY) scripts/dr_pdf.py $(DR_PDF_ARGS)
dr-tools: ## Blank Linux/macOS laptop: restic, tofu, talosctl, kubectl, flux, infisical CLI, ansible (sha256-pinned)
	scripts/dr-tools.sh
DR_ARGS ?=
dr-secrets-from-cluster: ## Infisical lost, cluster alive: copy the cluster's values into the NEW Infisical (DR_ARGS=--apply)
	$(KUBE) $(PY) scripts/dr_secrets_from_cluster.py $(DR_ARGS)

# ---------------------------------------------------------------- media: existing rips
MEDIA_ADOPT_ARGS ?=
media-adopt: ## HARDLINK existing rips (media_existing_paths) into the media data layout on the NAS. Dry-run; MEDIA_ADOPT_ARGS=--apply to act
	PY=$(PY) scripts/media-adopt.sh $(MEDIA_ADOPT_ARGS)

# ---------------------------------------------------------------- 4. flux
GITHUB_OWNER ?= devartech
GITHUB_REPO  ?= artzima-iac
KUBE := KUBECONFIG=$(CURDIR)/tofu/out/kubeconfig
eso-bootstrap: runtime-config ## ESO's Infisical credentials (/tofu/ESO_CLIENT_*, made by make bootstrap) -> Secret external-secrets/infisical-universal-auth
	@$(CONFIRM) "kubectl: create/replace Secret external-secrets/infisical-universal-auth"
	$(KUBE) $(WITH_INF) --set ESO_INFISICAL_CLIENT_ID=/tofu/ESO_CLIENT_ID \
	  --set ESO_INFISICAL_CLIENT_SECRET=/tofu/ESO_CLIENT_SECRET -- $(PY) scripts/eso_bootstrap.py

# The GitHub PAT (root credential #5, /k8s/ghcr/GHCR_PULL_TOKEN) is handed to flux in
# memory (GITHUB_TOKEN); flux creates a read-only deploy key and stores nothing else.
flux-bootstrap: runtime-config ## Bootstrap Flux into the cluster (after eso-bootstrap)
	$(KUBE) flux check --pre
	@$(KUBE) kubectl -n external-secrets get secret infisical-universal-auth >/dev/null 2>&1 || \
	  { echo "missing external-secrets/infisical-universal-auth: run make eso-bootstrap first"; exit 1; }
	@$(CONFIRM) "flux: bootstrap github.com/$(GITHUB_OWNER)/$(GITHUB_REPO) (branch main, k8s/clusters/artzima)"
	$(KUBE) $(WITH_INF) --set GITHUB_TOKEN=/k8s/ghcr/GHCR_PULL_TOKEN -- flux bootstrap github \
	  --owner=$(GITHUB_OWNER) --repository=$(GITHUB_REPO) --branch=main --path=k8s/clusters/artzima \
	  --personal --private=false --read-write-key=false

# ---------------------------------------------------------------- lint (CI)
lint: check-public lint-yaml lint-python lint-ansible lint-tofu lint-k8s lint-flux lint-images ## Everything CI runs
	@echo "lint: all green"

lint-yaml: ## yamllint
	yamllint -s .
lint-python: ## Byte-compile the Python helpers + render check
	$(PY) scripts/test_public_guard.py
	$(PY) scripts/test_private_site.py
	$(PY) scripts/test_infisical_split.py
	$(PY) scripts/test_make_dispatch.py
	$(PY) scripts/test_private_package.py
	$(PY) -m py_compile install/render_answers.py install/answer_server.py scripts/*.py
	$(PY) install/render_answers.py --allow-placeholder >/dev/null
	$(PY) scripts/render.py --check
	$(PY) scripts/secrets_check.py --lint
	$(PY) scripts/emergency_kit.py --lint
	$(PY) scripts/firewalla.py --lint
	$(PY) scripts/check_tool_pins.py # scripts/dr-tools.sh pins == scripts/install-tools.sh
	$(PY) -c 'import json; json.load(open("renovate.json"))' # Renovate config parses
	bash -n install/prepare-iso.sh install/first-boot.sh scripts/*.sh
	@for f in scripts/*.sh install/*.sh; do test -x $$f || { echo "not executable: $$f (chmod +x)"; exit 1; }; done
	$(PY) -m py_compile images/skyjo/artzima_serve.py images/skyjo/check_weights.py k8s/apps/media/wiring/wire.py k8s/apps/media/wiring/seed.py \
	  k8s/apps/media/sabnzbd/clamav-scan.py \
	  k8s/apps/monitoring/grafana-admin/sync.py \
	  k8s/apps/recs/app/recs_core.py k8s/apps/recs/app/recs_server.py k8s/apps/recs/mf-build/build_rmf2.py
	$(PY) -m py_compile k8s/apps/media/bazarr/a4k_accountless.py k8s/apps/media/bazarr/test_a4k_accountless.py
	$(PY) -I k8s/apps/recs/tests/test_recs_core.py # recs: seeds/weights, scoring, fusion, exclusion, fallback
lint-ansible: ## syntax-check every playbook + ansible-lint (production profile)
	cd ansible && for p in playbooks/*.yml; do ansible-playbook --syntax-check $$p >/dev/null || exit 1; echo "syntax ok: $$p"; done
	cd ansible && ansible-lint
lint-tofu: ## tofu fmt -check + validate every root + offline tofu test (no credentials)
	tofu -chdir=tofu fmt -check -recursive
	for d in tofu tofu/cloudflare tofu/backblaze tofu/bootstrap; do \
	  tofu -chdir=$$d init -backend=false -input=false >/dev/null && tofu -chdir=$$d validate || exit 1; done
	tofu -chdir=tofu/cloudflare test # mocked providers: rules/records per apps.yaml entry
	tofu -chdir=tofu/backblaze test # mocked providers: private+encrypted+locked buckets, no key can delete
	tofu -chdir=tofu/bootstrap test # mocked providers: tagged Tailscale keys, read-only ESO identity
lint-k8s: ## kustomize build + kubeconform + helm template of every HelmRelease
	$(PY) scripts/validate_k8s.py
lint-flux: ## flux build --dry-run every Flux Kustomization (offline)
	$(PY) scripts/lint_flux.py
lint-images: ## hadolint (images/*/Dockerfile), actionlint (.github/workflows), shellcheck (scripts)
	hadolint images/*/Dockerfile
	actionlint
	shellcheck -S warning scripts/*.sh install/*.sh
	@echo "images/workflows/scripts lint ok"

.PHONY: runtime-config
runtime-config: ## Apply private cluster substitutions without committing them to Git
	@test "$(ARTZIMA_PRIVATE_RUNTIME)" = "1" && test -f .private-runtime || { echo "Private runtime required"; exit 1; }
	$(KUBE) kubectl apply -f k8s/clusters/artzima/cluster-vars.yaml
