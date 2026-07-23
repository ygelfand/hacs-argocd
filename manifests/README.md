# Kubernetes RBAC for the ArgoCD integration

These manifests are only needed for the **Kubernetes backend** (reading ArgoCD
`Application` CRDs directly from the kube-apiserver). If you use the **REST API
backend**, you don't need any of this — just an ArgoCD API token.

## What to apply

| File | When |
| --- | --- |
| `serviceaccount-readonly.yaml` | Always — SA + read-only RBAC (status entities). |
| `serviceaccount-readwrite.yaml` | Instead of readonly, if you want sync/refresh actions. |
| `serviceaccount-token-secret.yaml` | **External HA only** — mints a non-expiring token. In-cluster HA does not need it. |

The credential (the token Secret) is deliberately a separate, opt-in file: in-cluster
deployments never create a standing token, so nothing sensitive is applied by default.

## 1. Apply the RBAC

Pick one:

```bash
# Status only (sensors, binary sensors, summary)
kubectl apply -f serviceaccount-readonly.yaml

# Also allow sync/refresh actions (buttons + services)
kubectl apply -f serviceaccount-readwrite.yaml
```

The `ServiceAccount` is created in the `argocd` namespace. Adjust the namespace
in the files if your Applications live elsewhere.

## 2. Get a token

### Home Assistant running **inside** the cluster

Mount the ServiceAccount into the Home Assistant pod and choose **In-cluster
ServiceAccount** in the config flow. The integration reads the token and CA from
`/var/run/secrets/kubernetes.io/serviceaccount/` automatically — nothing else to
configure.

### Home Assistant running **outside** the cluster

Use a **non-expiring** ServiceAccount token (a bound
`kubernetes.io/service-account-token` Secret). This is the right choice for a
standing integration — the Manual backend does **not** refresh the token, so a
short-lived one from `kubectl create token` would eventually expire and force a
re-auth.

```bash
# Create the bound token Secret, then read the token + CA out of it
kubectl apply -f serviceaccount-token-secret.yaml
kubectl -n argocd get secret home-assistant-argocd-token -o jsonpath='{.data.token}'  | base64 -d
kubectl -n argocd get secret home-assistant-argocd-token -o jsonpath='{.data.ca\.crt}' | base64 -d

# API server URL
kubectl config view --minify -o jsonpath='{.clusters[0].cluster.server}'
```

Enter the URL, token, and CA (paste the PEM, or a file path) in the **Manual**
config flow.

> Prefer the **In-cluster** option when Home Assistant runs in the cluster: it
> reads and auto-refreshes the projected token itself, so no static token is
> needed. `kubectl create token` (short-lived) is fine only for a quick test —
> if it expires, Home Assistant will prompt you to re-authenticate.
