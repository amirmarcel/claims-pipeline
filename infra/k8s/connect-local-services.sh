#!/usr/bin/env bash
# The fiddly part of the local K8s lift: making kind pods reach the
# LocalStack + Postgres containers from infra/local/docker-compose.yml.
#
# Those containers run on the host's Docker daemon, outside the kind
# cluster's pod network. The approach:
#
#   1. Attach both containers to the "kind" Docker network (the network kind
#      node containers themselves run on), so they get a routable IP that
#      kind's node -- and therefore pods, via the node's network namespace --
#      can reach directly.
#   2. Create selector-less Services + matching Endpoints in the
#      claims-pipeline namespace pointing at those IPs, giving pods stable
#      in-cluster DNS names (localstack.claims-pipeline.svc.cluster.local,
#      postgres.claims-pipeline.svc.cluster.local) instead of hardcoding an
#      IP that can change if the containers are recreated.
#   3. LocalStack's SQS responses embed a hostname
#      (sqs.<region>.localhost.localstack.cloud) in every QueueUrl it
#      returns -- real public DNS that resolves to 127.0.0.1, which is
#      correct for a host process but wrong from inside a pod (127.0.0.1
#      there is the pod itself, not LocalStack). CoreDNS gets a rewrite rule
#      so that hostname resolves in-cluster to the LocalStack Service
#      instead, without touching infra/local/docker-compose.yml or any
#      LocalStack config Sessions 1-5 already depend on.
#
# Safe to re-run: docker network connect on an already-connected container
# is a no-op error we ignore; kubectl apply is idempotent.
set -euo pipefail

NAMESPACE="claims-pipeline"
KIND_NETWORK="kind"
LOCALSTACK_CONTAINER="claims-pipeline-localstack"
POSTGRES_CONTAINER="claims-pipeline-postgres"

for container in "$LOCALSTACK_CONTAINER" "$POSTGRES_CONTAINER"; do
  if ! docker inspect "$container" >/dev/null 2>&1; then
    echo "error: $container is not running -- start it first:" >&2
    echo "  docker compose -f infra/local/docker-compose.yml up -d" >&2
    exit 1
  fi
  docker network connect "$KIND_NETWORK" "$container" 2>/dev/null || true
done

localstack_ip=$(docker inspect -f '{{.NetworkSettings.Networks.kind.IPAddress}}' "$LOCALSTACK_CONTAINER")
postgres_ip=$(docker inspect -f '{{.NetworkSettings.Networks.kind.IPAddress}}' "$POSTGRES_CONTAINER")

if [[ -z "$localstack_ip" || -z "$postgres_ip" ]]; then
  echo "error: could not resolve container IPs on the kind network" >&2
  exit 1
fi

kubectl create namespace "$NAMESPACE" --dry-run=client -o yaml | kubectl apply -f -

kubectl apply -f - <<EOF
apiVersion: v1
kind: Service
metadata:
  name: localstack
  namespace: $NAMESPACE
spec:
  ports:
    - port: 4566
      targetPort: 4566
---
apiVersion: v1
kind: Endpoints
metadata:
  name: localstack
  namespace: $NAMESPACE
subsets:
  - addresses:
      - ip: $localstack_ip
    ports:
      - port: 4566
---
apiVersion: v1
kind: Service
metadata:
  name: postgres
  namespace: $NAMESPACE
spec:
  ports:
    - port: 5432
      targetPort: 5432
---
apiVersion: v1
kind: Endpoints
metadata:
  name: postgres
  namespace: $NAMESPACE
subsets:
  - addresses:
      - ip: $postgres_ip
    ports:
      - port: 5432
EOF

# LocalStack's SQS QueueUrl responses point at
# sqs.<region>.localhost.localstack.cloud, which is real public DNS
# resolving to 127.0.0.1 -- correct for a host process, wrong inside a pod.
# Rewrite it in-cluster to the localstack Service above.
kubectl -n kube-system get configmap coredns -o yaml > /tmp/coredns-original.yaml
if ! grep -q "localhost.localstack.cloud" /tmp/coredns-original.yaml; then
  python3 - "$NAMESPACE" <<'PYEOF'
import subprocess
import sys

namespace = sys.argv[1]
raw = subprocess.check_output(["kubectl", "-n", "kube-system", "get", "configmap", "coredns", "-o", "jsonpath={.data.Corefile}"]).decode()
rewrite_line = f"    rewrite name regex (.*)\\.localhost\\.localstack\\.cloud localstack.{namespace}.svc.cluster.local\n"
lines = raw.splitlines(keepends=True)
out = []
inserted = False
for line in lines:
    out.append(line)
    if line.strip() == "errors" and not inserted:
        out.append(rewrite_line)
        inserted = True
patched = "".join(out)
subprocess.run(
    ["kubectl", "-n", "kube-system", "create", "configmap", "coredns",
     "--from-literal=Corefile=" + patched, "--dry-run=client", "-o", "yaml"],
    input=None, check=True,
    stdout=open("/tmp/coredns-patched.yaml", "w"),
)
PYEOF
  kubectl apply -f /tmp/coredns-patched.yaml
  kubectl -n kube-system rollout restart deployment coredns
  kubectl -n kube-system rollout status deployment coredns --timeout=60s
fi

echo "localstack=$localstack_ip postgres=$postgres_ip (attached to Docker network '$KIND_NETWORK')"
echo "in-cluster DNS: localstack.$NAMESPACE.svc.cluster.local:4566, postgres.$NAMESPACE.svc.cluster.local:5432"
