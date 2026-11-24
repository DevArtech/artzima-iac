#!/usr/bin/env python3
"""Offline-ish validation of the Flux tree (no cluster needed).

1. `kustomize build` every Flux Kustomization path (+ the cluster dir itself).
2. Apply Flux-style ${var} substitution from cluster-vars.yaml.
3. For every HelmRelease: `helm template --include-crds` the pinned chart with
   its values (validates values.schema.json where charts ship one).
4. JSON schemas generated from every CRD those pinned charts install (ESO's
   ExternalSecret/ClusterSecretStore, Traefik, cert-manager, ...), so custom
   resources are checked against the exact versions we deploy; the datreeio
   CRDs-catalog is only the fallback.
5. kubeconform (strict) every Flux path and every rendered chart.
Skip 3-4 with --no-helm.

Needs kustomize, kubeconform and helm on PATH (make deps puts them in the
tools dir). Charts are cached under $ARTZIMA_TOOLS/helm-cache.
"""

from __future__ import annotations

import os
import json
import pathlib
import re
import subprocess
import sys
import tempfile

import yaml

REPO = pathlib.Path(__file__).resolve().parent.parent
K8S = REPO / "k8s"
TOOLS = pathlib.Path(os.environ.get("ARTZIMA_TOOLS", pathlib.Path.home() / ".local/share/artzima-tools"))
SCHEMAS = [
    "default",
    "https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json",
]
KUSTOMIZATIONS = [
    "clusters/artzima",
    "infrastructure/namespaces",
    "infrastructure/external-secrets",
    "secrets",
    "infrastructure/controllers",
    "infrastructure/configs",
    "apps/artzima",
]
# Stand-ins for values that only exist in the cluster (ESO, from Infisical).
TEST_VARS = {"public_domain": "example.test"}
HOST_RULE = re.compile(r"Host\(`([^`]+)`\)")
VAR = re.compile(r"(?<!\$)\$\{([_a-zA-Z][_a-zA-Z0-9]*)\}")

class _Loader(yaml.SafeLoader):
    """SafeLoader that reads a bare `=` (YAML 1.1 "value" tag, seen in CRD enums) as a string."""


_Loader.add_constructor("tag:yaml.org,2002:value", lambda loader, node: loader.construct_scalar(node))


def load_all(text: str):
    return yaml.load_all(text, Loader=_Loader)  # noqa: S506 (SafeLoader subclass)


failed = False
hosts_seen: dict[str, str] = {}
services: dict[tuple[str, str], set[int]] = {}


def collect(text: str, where: str, default_ns: str | None = None) -> None:
    """Record published hostnames and every Service (ns, name) -> ports."""
    for doc in load_all(text):
        if not doc:
            continue
        kind = doc.get("kind")
        meta = doc.get("metadata", {})
        ns = meta.get("namespace") or default_ns
        check_nas(doc, where)
        if kind == "Service":
            services.setdefault((ns, meta["name"]), set()).update(
                int(p["port"]) for p in doc["spec"].get("ports", []))
        elif kind == "Ingress":
            # Publishing is only allowed via the generated routes (/apps.yaml).
            fail(f"{where}: Ingress {ns}/{meta['name']} found: publish apps via /apps.yaml instead")
        elif kind == "IngressRoute":
            if meta.get("labels", {}).get("app.kubernetes.io/managed-by") != "artzima-render":
                fail(f"{where}: hand-written IngressRoute {ns}/{meta['name']}: add the app to /apps.yaml")
            for r in doc.get("spec", {}).get("routes", []) or []:
                for h in HOST_RULE.findall(r.get("match", "")):
                    hosts_seen[h] = where


# The NAS survives lab rebuilds and holds personal files in other shares: every
# NFS StorageClass/PV/snapshot class must point inside artnas_export and RETAIN.
NAS_ROOT = str(yaml.safe_load((REPO / "ansible/group_vars/all/lab.yml").read_text())["artnas_export"]).rstrip("/")


def check_nas(doc: dict, where: str) -> None:
    kind, name, spec = doc.get("kind"), doc.get("metadata", {}).get("name"), doc.get("spec") or {}
    shares = []
    if kind == "StorageClass" and doc.get("provisioner") == "nfs.csi.k8s.io":
        params = doc.get("parameters") or {}
        # The ONE exception: throw-away restore tests, confined to <k8s share>/_scratch/.
        scratch = name == "nfs-artnas-scratch" and str(params.get("subDir", "")).startswith("_scratch/")
        if doc.get("reclaimPolicy") != "Retain" and not scratch:
            fail(f"{where}: StorageClass {name}: reclaimPolicy must be Retain (NAS data survives the cluster)")
        shares.append((doc.get("parameters") or {}).get("share", ""))
    elif kind == "PersistentVolume" and ((spec.get("csi") or {}).get("driver") == "nfs.csi.k8s.io" or "nfs" in spec):
        if spec.get("persistentVolumeReclaimPolicy") != "Retain":
            fail(f"{where}: PersistentVolume {name}: persistentVolumeReclaimPolicy must be Retain")
        shares.append(((spec.get("csi") or {}).get("volumeAttributes") or {}).get("share") or (spec.get("nfs") or {}).get("path", ""))
    elif kind == "VolumeSnapshotClass" and doc.get("driver") == "nfs.csi.k8s.io" and doc.get("deletionPolicy") != "Retain":
        fail(f"{where}: VolumeSnapshotClass {name}: deletionPolicy must be Retain")
    for s in shares:
        s = str(s).rstrip("/")
        if ".." in s.split("/") or not s.startswith("/"):
            fail(f"{where}: {kind} {name}: bad NAS path {s!r}")
        elif not s.startswith(NAS_ROOT + "/"):
            fail(f"{where}: {kind} {name}: NAS path {s!r} is outside {NAS_ROOT} (this repo never writes there)")


def fail(msg: str) -> None:
    global failed
    failed = True
    print(f"FAIL: {msg}", file=sys.stderr)


def run(cmd: list[str], stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, input=stdin, capture_output=True, text=True)


# Flux's envsubst also EVALUATES shell-style forms (default values, prefix/suffix
# removal, ...), e.g. inside a script in a generated ConfigMap. Only plain
# variables (or the escaped form) may appear in what Flux substitutes.
COMPLEX_VAR = re.compile(r"(?<!\$)\$\{(?![_a-zA-Z][_a-zA-Z0-9]*\})[^}\n]*\}")


SUBSTITUTE_OFF = re.compile(r"^\s+kustomize\.toolkit\.fluxcd\.io/substitute:\s*['\"]?disabled", re.M)


def substitute(text: str, env: dict) -> str:
    """Like Flux postBuild: every document, except those annotated
    kustomize.toolkit.fluxcd.io/substitute: disabled (e.g. Grafana dashboards)."""
    docs = re.split(r"(?m)^---\n", text)
    return "---\n".join(d if SUBSTITUTE_OFF.search(d) else substitute_doc(d, env) for d in docs)


def substitute_doc(text: str, env: dict) -> str:
    for m in COMPLEX_VAR.finditer(text):
        fail(f"Flux would evaluate {m.group(0)!r}: only plain variables (or the escaped form) are allowed")

    def rep(m):
        if m.group(1) not in env:
            fail(f"unknown substitution variable ${{{m.group(1)}}}")
            return m.group(0)
        return env[m.group(1)]
    return VAR.sub(rep, text).replace("$${", "${")


def kubeconform(text: str, what: str, crd_dir: str | None = None) -> None:
    cmd = ["kubeconform", "-strict", "-summary", "-ignore-missing-schemas",
           "-kubernetes-version", "1.35.0"]
    locations = ([crd_dir + "/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json"] if crd_dir else []) + SCHEMAS
    for s in locations:
        cmd += ["-schema-location", s]
    res = run(cmd + ["-"], text)
    out = (res.stdout + res.stderr).strip()
    print(f"  kubeconform {what}: {out.splitlines()[-1] if out else ''}")
    if res.returncode != 0:
        fail(f"kubeconform {what}:\n{out}")


def _strict(node):
    """CRD openAPIV3Schema -> JSON schema the way kubeconform's openapi2jsonschema
    does it: closed objects (strict), int-or-string, nullable."""
    if isinstance(node, list):
        return [_strict(x) for x in node]
    if not isinstance(node, dict):
        return node
    out = {k: _strict(v) for k, v in node.items()}
    if out.pop("x-kubernetes-int-or-string", False):
        out.pop("type", None)
        out["oneOf"] = [{"type": "string"}, {"type": "integer"}]
    if out.pop("nullable", False) and isinstance(out.get("type"), str):
        out["type"] = [out["type"], "null"]
    if ("properties" in out and "additionalProperties" not in out
            and not out.get("x-kubernetes-preserve-unknown-fields")):
        out["additionalProperties"] = False
    return out


def write_crd_schemas(texts: list[str], root: pathlib.Path) -> int:
    n = 0
    for text in texts:
        for doc in load_all(text):
            if not doc or doc.get("kind") != "CustomResourceDefinition":
                continue
            spec = doc["spec"]
            for v in spec.get("versions", []):
                schema = (v.get("schema") or {}).get("openAPIV3Schema")
                if not schema:
                    continue
                out = root / spec["group"] / f"{spec['names']['kind'].lower()}_{v['name']}.json"
                out.parent.mkdir(parents=True, exist_ok=True)
                schema = _strict(schema)
                # The API server always accepts these, even if a CRD omits them.
                props = schema.setdefault("properties", {})
                props.setdefault("apiVersion", {"type": "string"})
                props.setdefault("kind", {"type": "string"})
                props.setdefault("metadata", {"type": "object"})
                out.write_text(json.dumps(schema))
                n += 1
    return n


def main() -> None:
    no_helm = "--no-helm" in sys.argv
    cv = yaml.safe_load((REPO / "examples/cluster-vars.yaml").read_text())["data"]
    cv = {**cv, **TEST_VARS}
    repos = {}
    built = {}
    for k in KUSTOMIZATIONS:
        d = K8S / k
        if (d / "kustomization.yaml").exists():
            res = run(["kustomize", "build", str(d)])
        else:  # Flux auto-generates a kustomization: emulate with a temp one
            # Like Flux: a subdirectory with its own kustomization.yaml (e.g. the
            # flux-system/ written by `flux bootstrap`) is one directory resource.
            subs = sorted(p.parent for p in d.rglob("kustomization.yaml"))
            files = sorted(str(p.relative_to(d)) for p in d.rglob("*.yaml")
                           if not any(s in p.parents for s in subs))
            files += [str(s.relative_to(d)) for s in subs]
            (d / "kustomization.yaml").write_text(
                "resources:\n" + "".join(f"  - {f}\n" for f in files))
            try:
                res = run(["kustomize", "build", str(d)])
            finally:
                (d / "kustomization.yaml").unlink()
        if res.returncode != 0:
            fail(f"kustomize build {k}: {res.stderr}")
            continue
        # The cluster dir is applied by flux-system (gotk-sync.yaml), which has no
        # postBuild: nothing there is substituted (Flux's own CRDs document ${var:=x}).
        text = res.stdout if k == "clusters/artzima" else substitute(res.stdout, cv)
        built[k] = text
        print(f"built {k}: {text.count(chr(10) + 'kind: ') + text.startswith('kind: ')} objects")
        collect(text, k)
        for doc in load_all(text):
            if doc and doc.get("kind") == "HelmRepository":
                repos[doc["metadata"]["name"]] = doc["spec"]["url"]

    rendered: dict[str, str] = {}
    if not no_helm:
        env = dict(os.environ,
                   HELM_CACHE_HOME=str(TOOLS / "helm-cache/cache"),
                   HELM_CONFIG_HOME=str(TOOLS / "helm-cache/config"),
                   HELM_DATA_HOME=str(TOOLS / "helm-cache/data"))
        for k, text in built.items():
            for doc in load_all(text):
                if not doc or doc.get("kind") != "HelmRelease":
                    continue
                spec = doc["spec"]["chart"]["spec"]
                name = f"{doc['metadata']['namespace']}/{doc['metadata']['name']}"
                url = repos.get(spec["sourceRef"]["name"])
                if not url:
                    fail(f"{name}: unknown HelmRepository {spec['sourceRef']['name']}")
                    continue
                with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as vf:
                    yaml.safe_dump(doc["spec"].get("values") or {}, vf)
                res = subprocess.run(
                    ["helm", "template", doc["metadata"]["name"], spec["chart"],
                     "--repo", url, "--version", str(spec["version"]),
                     "--namespace", doc["metadata"]["namespace"], "-f", vf.name,
                     "--include-crds", "--kube-version", "1.35.0",
                     # CRDs that exist in the real cluster (installed by other releases)
                     "--api-versions", "monitoring.coreos.com/v1",
                     "--api-versions", "snapshot.storage.k8s.io/v1",
                     "--api-versions", "cert-manager.io/v1",
                     "--api-versions", "traefik.io/v1alpha1",
                     "--api-versions", "external-secrets.io/v1"],
                    capture_output=True, text=True, env=env)
                os.unlink(vf.name)
                if res.returncode != 0:
                    fail(f"helm template {name} ({spec['chart']} {spec['version']}): {res.stderr.strip()}")
                    continue
                print(f"helm template {name} ({spec['chart']} {spec['version']}): ok"
                      + ("  [suspended]" if doc["spec"].get("suspend") else ""))
                rendered[name] = res.stdout
                collect(res.stdout, name, doc['metadata']['namespace'])

    with tempfile.TemporaryDirectory(prefix="artzima-crd-schemas-") as crd_dir:
        n = write_crd_schemas(list(rendered.values()), pathlib.Path(crd_dir))
        print(f"CRD schemas from the pinned charts: {n}")
        for what, text in list(built.items()) + list(rendered.items()):
            kubeconform(text, what, crd_dir)

    # /apps.yaml is the single list: routes == apps, and every backend exists.
    apps = yaml.safe_load((REPO / "apps.yaml").read_text())
    expected = {f"{a['name']}.{TEST_VARS['public_domain']}": a for a in apps["apps"]}
    for h, where in sorted(hosts_seen.items()):
        if h not in expected:
            fail(f"host {h} ({where}) is not in /apps.yaml")
    for h in sorted(set(expected) - set(hosts_seen)):
        fail(f"/apps.yaml entry {h} has no route (run `make render`)")
    t = apps["traefik"]
    for h, a in list(expected.items()) + [("tunnel origin", t)]:
        ports = services.get((a["namespace"], a["service"]))
        if ports is None:
            fail(f"{h}: Service {a['namespace']}/{a['service']} not found in any rendered manifest")
        elif int(a["port"]) not in ports:
            fail(f"{h}: Service {a['namespace']}/{a['service']} has ports {sorted(ports)}, not {a['port']}")
    print(f"public apps: {len(hosts_seen)} routed, {len(expected)} in /apps.yaml, backends checked"
          + ("" if set(hosts_seen) == set(expected) else " (MISMATCH)"))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
    sys.exit(1 if failed else 0)
