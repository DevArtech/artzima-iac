# Private deployment configuration

The public checkout contains fictional CI fixtures. It does not contain the operator's
actual internal IP addresses, hardware MAC addresses, disk serials, physical role
assignments or Infisical project slug. Public source still shows the components and
role definitions needed to understand and validate the software.

## Where the actual configuration lives

- `.private/site/ansible/hosts.yml`: actual device identities, role assignments and disk targets.
- `.private/site/ansible/group_vars/all/{network,lab,infisical,media,backups}.yml`:
  actual addressing, project settings, NAS paths and deployment settings.
- `.private/site/ansible/roles/nut/defaults/main.yml`: actual UPS device settings.
- `.private/site/emergency-kit.yaml`: private kit locations and rotation records.
- `.private/runtime/`: generated deployment copy, state, kubeconfig, installer reports,
  actual address table and printable operator runbook. Never publish this directory.

The private root and runtime are mode 0700; private configuration files are mode 0600.
The publication guard rejects private paths even if force-added to Git.

## Normal operation

Keep using `make preflight`, `make bootstrap`, `make up`, `make answers` and
`make verify` from the public checkout. The Makefile forwards live targets into the
private runtime. No extra manual inventory-copy step is required. Missing private
configuration stops execution; it never falls back to the example inventory.

`make render` and all lint targets use public examples only. They cannot write actual
metadata into the tracked tree. The example cluster substitutions live under
`examples/cluster-vars.yaml`; they are not deployed by Flux.

The actual `cluster-vars` ConfigMap is generated privately and applied by
`make runtime-config` before ESO/Flux bootstrap. Flux references this ConfigMap but
does not contain or overwrite its contents. Public application manifests retain
substitution variables instead of private addresses.

## Change the deployment

Edit the authoritative files under `.private/site/`, then run:

```bash
make private-backup
make private-prepare
```

The private runtime is a generated copy. Do not edit inventory there: preparation
overwrites it from the authoritative private site. Preparation preserves generated
OpenTofu state and other private outputs. If private settings change a generated
application manifest beyond its public parameterization, preparation stops and asks
for the public template to be parameterized first. It does not silently deploy a
different public manifest.

For direct CLI operations, prepare first and use `.private/runtime` as the working
directory. Its marker selects private mode for make targets. Keep custom tool paths
absolute. Use the actual address table in `.private/runtime/docs/addresses.md`.
Never use the public example addresses as installation targets.

## Backup and catastrophic recovery

`make private-backup` stores the private site bundle in Infisical at
`/settings/PRIVATE_SITE_CONFIG`. It contains metadata, not root credential values.
The cluster's scoped runtime identity cannot read this setting.

On a replacement workstation, clone the public repository, install the tools and
restore the local Infisical identity/project configuration from the emergency kit.
Then run:

```bash
make private-restore
make private-prepare
```

Restore validates the file allowlist and refuses to replace an existing private site.
Keep an encrypted offline copy of `.private/site/` with the recovery kit as well.
Infisical alone is not enough for a recovery where Infisical itself is unavailable.

Print with `make dr-pdf`. The actual runbook is written to
`.private/runtime/dist/artzima-disaster-recovery.pdf`. Never commit the PDF.
Private kit locations belong in the private emergency-kit file, not the public template.

## Publication gates

`make check-public` checks the selected Git publication files for credentials, forbidden
private paths, RFC1918 addresses, non-example hardware MACs and non-example project slugs.
Example addresses use documentation or benchmarking networks; MAC fixtures use a clearly
synthetic prefix. These checks reduce accidental disclosure; they do not prove that
arbitrary prose or unknown encodings contain no private information.

No commit history existed when this separation was made. If private data reaches Git
history later, deleting the current file alone will not remove that history.
