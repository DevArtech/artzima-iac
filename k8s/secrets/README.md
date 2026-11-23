# k8s/secrets

No secret values live here, not even encrypted ones. This directory holds:

- `cluster-secret-store.yaml`: the ESO `ClusterSecretStore` **infisical** (Infisical's
  universal auth, using the read-only machine identity `artzima-eso`)
- one `ExternalSecret` per Kubernetes Secret, which names an Infisical **path and key**
- `ghcr-pull.yaml`: a `ClusterExternalSecret`. It writes the GHCR `dockerconfigjson` into every
  namespace labelled `artzima.io/ghcr-pull: "true"`.

The Flux Kustomization `secrets` applies this directory after ESO is up (`wait: true`),
and before the controllers that use the Secrets. The full table of names and
consumers is in [`docs/secrets.md`](../../docs/secrets.md). The only Secret created by
hand is ESO's own credential, `external-secrets/infisical-universal-auth`, which
`make eso-bootstrap` creates (from the identity `make bootstrap` made, Infisical `/tofu/ESO_CLIENT_*`).
`backups/maintenance.yaml` holds the B2 **maintenance** keys (retention only, never given to a
backup job); `monitoring/alert-webhook.yaml` the one alert URL.

Check: `kubectl get externalsecret -A` (every row `SecretSynced`, `Ready=True`).
