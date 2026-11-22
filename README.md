# Syslog to Loki

A Kubernetes-hosted syslog gateway for **Linux systems and network devices outside Kubernetes**.

External senders → LoadBalancer TCP/UDP 514 → rsyslog → JSON log file → Grafana Alloy → Loki.

## How it works

- The `rsyslog` Service keeps the external address `192.168.0.141` and DNS name `rsyslog.lab.local`. Adjust these in `service.yaml` for your network.
- rsyslog accepts RFC3164 and RFC5424 syslog over TCP or UDP. It normalizes records into `/mnt/rsyslog/syslog.jsonl` using an escaped JSON template.
- Each new JSON record is also written to the rsyslog container's stdout, so incoming syslog is available through pod logs. Historical files are not replayed into stdout.
- Alloy v1.22.0 tails that file and sends it to `http://grafana-loki-gateway.monitoring.svc.cluster.local/loki/api/v1/push`.
- Loki labels are `job="varlogs"`, `hostname`, `appname`, `facility`, and `severity`. Process IDs remain in the JSON body rather than becoming stream labels. The file source also adds a `filename` label.
- Alloy stores read positions under `/var/lib/alloy`, mounted from the existing shared volume's `alloy` directory.
- At approximately 100MiB, rsyslog renames the active file and retains five archives (`syslog.jsonl.1` through `.5`). It writes each record once.

Only the active file is automatically tailed. Renamed files are drained by an already-running reader, but archives left unread across a restart require manual replay. Retention is bounded: the oldest archive is replaced at the next rotation. These files and persistent read positions are **not an end-to-end delivery queue**: Alloy can exhaust its Loki retries after reading a record. UDP delivery is also best effort. Prefer TCP on senders that support it.

## Build and deploy

Requirements: Docker, Python 3, Kubernetes with an existing `monitoring` namespace, suitable storage for the existing PVC, and a reachable Loki gateway. Flux is optional for manual deployment.

Build, test and publish the updated rsyslog image **before** deploying its new tag:

```sh
# Authenticate to the existing Docker Hub repository first.
make publish
kubectl apply -k .
```

`make publish` builds `ninjatec/rsyslog:1.0.16`, runs the rotation tests and the Docker pipeline smoke test, then pushes the image. Use `make test` to build and test without pushing. The smoke test starts disposable rsyslog, Alloy and Loki containers on an isolated Docker network, sends TCP/RFC5424 and UDP/RFC3164 messages, checks Loki labels and JSON, verifies matching container stdout, then verifies read-position persistence after an Alloy restart.

GitHub Actions runs `make test` on pull requests targeting `main` and `make publish` on pushes to `main`. Both run the same build and pipeline checks; only pushes authenticate to Docker Hub and publish. The workflow is `.github/workflows/build.yml`.

Before the first publish, add these repository secrets under **Settings → Secrets and variables → Actions**:

- `DOCKER_USER`: the Docker Hub username with push access to `ninjatec/rsyslog`.
- `DOCKER_PASS`: a Docker Hub access token with write permission for that repository.

After the first successful GitHub Actions run, disable the old CircleCI project and remove its Docker credentials if they are no longer used elsewhere. If branch protection requires CircleCI checks, replace those requirements with the GitHub Actions `build` check so pull requests can merge.

For the checked-in Flux bootstrap, `clusters/my-cluster/kustomization.yaml` includes a `syslogtoloki` Flux Kustomization that applies the root application manifests. Existing Flux installations can instead point their own Kustomization at the repository root. The `image-policy.yaml` and `image-update.yaml` examples remain disabled; image updates are not automatically committed. Update the rsyslog version in both `Makefile` and `deployment.yaml` when releasing.

Alloy configuration lives in `config.alloy`. Kustomize generates a ConfigMap with a content hash, so configuration changes update the Deployment's reference and roll the pod. The single receiver uses `Recreate` to prevent overlapping readers and writers; a deployment therefore briefly interrupts reception.

## Agent migration

The external Service address, DNS name, ports and Loki gateway stay the same. Senders need no migration-specific changes.

The new receiver writes `syslog.jsonl`. The old `rsyslog.log` is left untouched and is not automatically ingested by Alloy, preventing mixed formats and an unplanned replay of historical records. Export or replay old records separately if needed. A manual `kubectl apply -k .` also leaves the unused `grafana-agent-logs` ConfigMap behind; it can be removed after the rollout.

Log bodies now contain JSON rather than only the extracted message. Facility reflects each event rather than the old constant value, and `procid` is no longer a label. Update dashboards that depend on those old shapes. To display just the message:

```logql
{job="varlogs"} | json | line_format "{{.message}}"
```

The old node-exporter integration and self-scrape are not included; this gateway is scoped to forwarding syslog.

## Verify reception

Send valid syslog messages from a machine outside the cluster:

```sh
# RFC5424 over TCP
printf '<134>1 %s test-linux syslog-test 123 - - syslog-to-loki TCP test\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" | nc -w 2 rsyslog.lab.local 514

# RFC3164 over UDP
printf '<28>%s test-switch syslog-test[456]: syslog-to-loki UDP test\n' "$(LC_ALL=C date '+%b %e %H:%M:%S')" | nc -u -w 2 rsyslog.lab.local 514
```

Query `{job="varlogs"} |= "syslog-to-loki"` in Grafana. Confirm the expected hostname, application, facility and severity.

```sh
kubectl get pods,svc -n monitoring -l app=rsyslog
kubectl logs -f -n monitoring deployment/rsyslog -c rsyslog
kubectl logs -n monitoring deployment/rsyslog -c alloy
kubectl rollout status -n monitoring deployment/rsyslog
```

Alloy exposes readiness at `/-/ready`, health at `/-/healthy`, and metrics at `/metrics` on port 12345. These are internal to the pod; they are not exposed by the syslog LoadBalancer.

```sh
kubectl port-forward -n monitoring deployment/rsyslog 12345:12345
```

Use `http://localhost:12345` for Alloy's UI and component diagnostics. Check Alloy errors for rejected timestamps or Loki write failures; readiness alone does not prove successful delivery to Loki.
