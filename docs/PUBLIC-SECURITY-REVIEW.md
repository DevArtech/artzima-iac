# Public repository security review — 23 November 2026

## Result and scope

The repository checks pass. No credentials were detected in the publication set.
This is a source review and offline validation, not a guarantee that the deployed lab
has no vulnerabilities. No servers were started, no infrastructure was applied, no
NAS files were deleted, and nothing was published to GitHub.

Git has no existing commit history to scan. Recheck the final commit and enable GitHub
secret scanning/push protection when the public repository is created. Branch rules
and package visibility must also be verified there; local checks cannot enforce them.

## Changes made

- Restricted the planned ESO Infisical identity. Project-wide Viewer would expose
  administrator secrets, including the B2 administrator key and NAS backup passphrase.
  The custom role reads only runtime secrets under `/k8s/**` and
  `/settings/PUBLIC_DOMAIN` in the configured environment. It grants no writes.
  Added an offline plan assertion for this boundary. It is not applied to the account yet.
- Made publication scanning fail closed when a selected file cannot be copied.
  Symlinks, submodules and missing selected files are rejected rather than skipped.
- Expanded forbidden publication paths to include private-key files, environment files,
  provider variable files, generated credentials and generated recovery output, even
  when someone force-adds an ignored file. Documented example files remain allowed.
- Added six publication-guard regression tests to `make lint`, using temporary Git
  repositories and synthetic material. The commit hook also invokes the full guard.
- Pinned every GitHub Action used by these workflows to an exact commit, resolved
  from its official repository's release tag. Existing read-only PR permissions,
  disabled checkout credential persistence and main-only image publishing remain.
- Suppressed raw Infisical and kubectl error responses in the secret bootstrap helpers,
  because errors can echo submitted credentials. Diagnostics retain operation and error
  type/exit status. Tested three failure paths with a synthetic secret marker.
- Corrected the setup checklist's misleading claim that the B2 administrator key can
  do nothing except create buckets/keys. Its `writeKeys` capability can create more
  powerful keys; keep it outside runtime identities.

The scoped-read design follows the official
[Infisical permission reference](https://infisical.com/docs/internals/permissions/project-permissions).
The account's custom-role entitlement and actual effective permissions are not proved
by an offline provider test.

## Validation

- Full `make lint`: passed, including YAML, Ansible production lint and syntax,
  Python compilation, generated-file freshness, secret manifest and emergency-kit
  consistency, OpenTofu formatting/validation and 18 mocked plan tests, Kubernetes
  rendering/schema checks, Flux builds, Dockerfile/workflow/shell checks.
- Publication guard: passed; six regression tests cover clean examples, detectable
  credentials, ignored private files, force-added private files, symlinks and missing files.
- In-memory exact-value comparison against nine configured credentials: no matches in
  264 publication files at the time of comparison. Values were not printed or saved.
  This checks raw values, not every possible encoding or transformation.
- Three secret-error redaction failure cases: passed.
- No real deployments, container image builds, vulnerability scans of built images or
  live penetration tests were performed.

## Private metadata separation (updated after the review)

Actual IP addresses, MAC addresses, physical device-role assignments, disk targets,
project slug and kit locations now live in ignored private configuration. The public
inventory is a fictional CI fixture with no per-host role assignments. The public
source still describes software components, role definitions and a reference layout.

Live make targets use a separate private runtime; root render/lint use examples only.
A private ConfigMap supplies Flux substitutions without putting the values in Git.
The private site is backed up to Infisical and also needs an offline kit copy. See
[private configuration](PRIVATE-CONFIG.md). Publication checks reject private paths,
RFC1918 addresses, non-example MAC addresses and non-example project slugs.

## Required before deployment

1. Verify custom-role creation is supported. Test a permitted runtime secret read and
   denied reads of `/tofu/B2_ADMIN_APPLICATION_KEY` and
   `/ansible/NAS_RESTIC_PASSWORD` with the ESO identity. Stop on failure; never use
   project Viewer as a fallback.
2. Proxmox currently defaults to `proxmox_insecure = true`. Configure and verify trusted
   API certificates and disable that bypass before using privileged API credentials.
   This requires the actual hosts; it is not fixed by secret scanning.
3. Complete first-run administrator setup before public ingress reaches fresh services.
   Review each public app's authentication and Cloudflare Access policy at cutover.
   The app allowlist alone does not establish that each app is securely configured.
4. Inspect the installer network. Its answer server is temporary, unauthenticated HTTP
   intended for a trusted installation LAN, and contains an optional root password hash.
   Do not expose it through a public tunnel; stop it after installation.
5. Existing tool downloads are version pinned, but most workstation binaries are not
   checksum pinned in `scripts/install-tools.sh`; gitleaks is an exception. Complete
   artifact-integrity hardening before treating the tool bootstrap as verified supply chain.
6. Build the private images, check known dependency/image vulnerabilities and verify GHCR
   package visibility. These checks cannot be completed against images not yet built.
7. Verify disk serials, the independent backup of protected NAS folders, and the exact
   cleanup inventory. Repository lint is not evidence that a destructive target is correct.

Runtime secrets still include powerful Tailscale operator and backup maintenance keys.
The ESO role is an improvement over project Viewer, not per-app or per-folder runtime
isolation. Keep cluster RBAC and access to its bootstrap credential tightly restricted.
