# Offline checks use fictional fixtures. Live targets use the private deployment tree.
ifneq ($(wildcard .private-runtime),)
export ARTZIMA_PRIVATE_RUNTIME := 1
endif
OFFLINE_GOALS := help deps hooks check-public render answer-check lint lint-yaml lint-python lint-ansible lint-tofu lint-k8s lint-flux lint-images dr-tools private-prepare private-backup private-restore private-check
REQUESTED_GOALS := $(if $(MAKECMDGOALS),$(MAKECMDGOALS),help)
FORWARD_GOALS := $(filter-out $(OFFLINE_GOALS),$(REQUESTED_GOALS))
LOCAL_GOALS := $(filter $(OFFLINE_GOALS),$(REQUESTED_GOALS))
ARTZIMA_TOOLS ?= $(HOME)/.local/share/artzima-tools
SITE_PY := $(ARTZIMA_TOOLS)/venv/bin/python
.DEFAULT_GOAL := help
EXECUTE_HERE := $(if $(filter 1,$(ARTZIMA_PRIVATE_RUNTIME) $(ARTZIMA_PUBLIC_OFFLINE)),yes,$(if $(FORWARD_GOALS),,yes))
ifeq ($(EXECUTE_HERE),yes)
include mk/targets.mk
else
.PHONY: $(REQUESTED_GOALS) _private-forward
$(REQUESTED_GOALS): _private-forward ;
_private-forward:
ifneq ($(strip $(LOCAL_GOALS)),)
	+$(MAKE) --no-print-directory ARTZIMA_PUBLIC_OFFLINE=1 $(LOCAL_GOALS)
endif
	+$(SITE_PY) scripts/private_site.py run $(FORWARD_GOALS)
endif
ifeq ($(EXECUTE_HERE),yes)
.PHONY: private-prepare private-backup private-restore private-check
private-prepare: ## Prepare the private deployment copy without contacting the servers
	$(SITE_PY) scripts/private_site.py prepare
private-backup: ## Back up private site configuration to Infisical (values hidden)
	$(SITE_PY) scripts/private_site.py backup
private-restore: ## Restore private site configuration on a replacement workstation
	$(SITE_PY) scripts/private_site.py restore
private-check: ## Check that private site files exist (values hidden)
	$(SITE_PY) scripts/private_site.py check
endif
