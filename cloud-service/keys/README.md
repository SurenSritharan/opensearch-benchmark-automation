# cloud-service/keys

SSH deploy keys for the benchmark pod's git commit-back feature.

| File | Description |
|---|---|
| `github-deploy-key` | **Private key** — never commit this. Create the K8s secret from it (see below). |
| `github-deploy-key.pub` | Public key — add this to the repo's Deploy Keys with write access. |

## Setup

**1. Add the public key to GitHub**

Go to the repo → Settings → Deploy keys → Add deploy key.
Paste the contents of `github-deploy-key.pub` and check **Allow write access**.

**2. Create the Kubernetes secret**

```bash
kubectl create secret generic github-deploy-key \
  --from-file=id_ed25519=cloud-service/keys/github-deploy-key \
  -n benchmark-api
```

## Regenerating the key

```bash
ssh-keygen -t ed25519 -C "benchmark-bot" -f cloud-service/keys/github-deploy-key -N ""
```

Then repeat the two setup steps above.
