# Deploying to Kubernetes

These manifests are the deployed form of the same four containers
`docker compose` runs on one machine. They are written to be read: almost every
non-obvious line has a comment saying what breaks without it, because most of
them are there because something did.

Nothing here is generated. There is no Helm chart and no Kustomize overlay — one
namespace, one environment, plain YAML. If you end up with a second environment,
that is the moment to reach for Kustomize, not before.

## What runs

| | what | notes |
|---|---|---|
| `30-db-statefulset.yaml` | PostgreSQL | one Pod, one PVC. Delete it and use a managed database if the company offers one |
| `35-migrate-job.yaml` | `alembic upgrade head` | runs to completion *before* the backend is updated |
| `40-backend-deployment.yaml` | FastAPI **+ the sandbox sidecar** | one replica, and that is a constraint — see the file |
| `45-frontend-deployment.yaml` | nginx + the built bundle | serves the app and proxies `/api` |
| `50-ingress.yaml` | the one way in | its annotations are load-bearing |

Two containers in one Pod is the part worth understanding. Uploaded eval-set
scripts execute in the second one, under a different uid, with none of the
backend's environment: no database URL, no settings key, no LLM key, no CA
bundle. The script's SQL is forwarded over a unix socket to the backend, which
owns the credentials and answers it. `backend/app/sandbox/__init__.py` explains
the whole arrangement, including what it does *not* protect against.

## The first deployment

The manifests are numbered in the order they must be applied.

```sh
# 1. Namespace (skip if the platform team gave you one).
kubectl apply -f 00-namespace.yaml

# 2. Secrets. Do NOT apply 10-secrets.example.yaml with the values edited in —
#    it is an example of the shape, not a file to fill in. Create them from
#    values you hold, or from an Azure DevOps variable group in the release
#    pipeline.
kubectl -n skill-studio create secret generic skill-studio-secrets \
  --from-literal=POSTGRES_PASSWORD='...' \
  --from-literal=DATABASE_URL='postgresql+asyncpg://agentopt:...@skill-studio-db:5432/agentopt' \
  --from-literal=SYNC_DATABASE_URL='postgresql+psycopg://agentopt:...@skill-studio-db:5432/agentopt' \
  --from-literal=SETTINGS_SECRET_KEY='...' \
  --from-literal=LLM_API_KEY='...' \
  --from-literal=LANGFUSE_PUBLIC_KEY='' \
  --from-literal=LANGFUSE_SECRET_KEY='' \
  --from-literal=AGENT_API_KEY=''

# The internal CA bundle, if internal services use a private CA. It REPLACES the
# default trust store rather than adding to it, so the file must contain the
# public roots too — copying a corporate Linux machine's own bundle gets both.
kubectl -n skill-studio create secret generic skill-studio-ca \
  --from-file=ca-bundle.crt=/etc/ssl/certs/ca-certificates.crt

# 3. Settings, then the database.
kubectl -n skill-studio apply -f 20-backend-config.yaml
kubectl -n skill-studio apply -f 30-db-statefulset.yaml
kubectl -n skill-studio rollout status statefulset/skill-studio-db

# 4. Schema, and wait for it. The wait is the point: without it the next step
#    starts new code against the old schema.
kubectl -n skill-studio delete job skill-studio-migrate --ignore-not-found
kubectl -n skill-studio apply -f 35-migrate-job.yaml
kubectl -n skill-studio wait --for=condition=complete --timeout=300s job/skill-studio-migrate

# 5. The application.
kubectl -n skill-studio apply -f 40-backend-deployment.yaml -f 45-frontend-deployment.yaml
kubectl -n skill-studio rollout status deployment/skill-studio-backend
kubectl -n skill-studio apply -f 50-ingress.yaml
```

Every subsequent release is steps 4 and 5 with new image tags. That sequence —
migrate, wait, then update the Deployments — is the whole of the CD side, and it
is what an Azure Release Pipeline should run.

## What has to be filled in

Search for `REPLACE-WITH`. Nothing here has a working default, on purpose: a
manifest that deploys to the wrong registry is worse than one that refuses.

| placeholder | where | what it is |
|---|---|---|
| `REPLACE-WITH-REGISTRY.example.com` | every image | the registry login server the three CI pipelines push to |
| `REPLACE-WITH-TAG` | every image | the CI build number. Never `latest` — a moving tag cannot be rolled back |
| `REPLACE-WITH-KEYCLOAK` | `20-backend-config.yaml`, `45-frontend-deployment.yaml` | must be the same value in both, or the browser signs in against one realm while the API validates against another |
| `REPLACE-WITH-HOSTNAME.example.com` | `50-ingress.yaml` | the hostname, which also has to be a registered redirect URI in Keycloak |

If the registry needs credentials to pull from, the cluster needs an
`imagePullSecrets` entry too — ask the platform team whether the namespace
already has one attached to its default ServiceAccount, which is common.

## Things that will bite

**The Pods must not run as root.** They are written for the restricted Pod
Security Standard: `runAsNonRoot`, no privilege escalation, all capabilities
dropped, the default seccomp profile. The one place this is subtle is Postgres,
whose official image starts as root to chown its data directory — naming
`runAsUser: 999` and `fsGroup: 999` skips the step that would have failed.

**`PGDATA` must be a subdirectory of the mount.** This is the single most common
first-deployment failure: most volumes arrive with a `lost+found` at the root,
`initdb` refuses a non-empty directory, and the error reads as though a database
were already there.

**Progress bars will look broken until the ingress stops buffering.** See the
long comment at the top of `50-ingress.yaml`. There is no error message for this
one — that is what makes it expensive.

**One backend replica.** Playground attempts and the SSE hub live in that
process's memory. Two replicas make both fail intermittently depending on which
Pod a request reaches. `40-backend-deployment.yaml` says what would have to
change first.

## What these manifests do not do

**They do not stop an uploaded script reaching the network.** Containers in one
Pod share a network namespace, so the sandbox has the same egress the backend
has. A NetworkPolicy is the only thing that closes it — and because policy
applies to the whole Pod, one restricting the sandbox restricts the backend
identically, which it cannot survive: it has to reach Keycloak, Langfuse, the
LLM endpoint and the agent server. Genuinely closing this means moving the
sandbox out into its own Pod, and nothing here pretends otherwise.
`backend/app/sandbox/__init__.py` records the same gap.

**They do not back up the database.** The PVC is the only copy.

**They do not set resource limits you should trust.** The requests and limits are
starting points from a system nobody has yet run under real load here.
