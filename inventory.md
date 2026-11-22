# Project inventory

## Active application

| File | Purpose |
| --- | --- |
| `Dockerfile` | Ubuntu 24.04 image containing rsyslog |
| `rsyslog.conf` | TCP/UDP 514 inputs, JSON file and stdout outputs, and size-based rotation |
| `log_rotation.sh` | Rename active logs and retain five archives |
| `config.alloy` | File reader, JSON processing, event labels and Loki writer |
| `deployment.yaml` | Single rsyslog/Alloy pod and the existing PVC definition |
| `service.yaml` | External syslog LoadBalancer and Cilium/external-DNS configuration |
| `kustomization.yaml` | Application resources and generated Alloy ConfigMap |
| `Makefile` | Build, test and publish rsyslog 1.0.16 |
| `.github/workflows/build.yml` | Build and test pull requests; tested Docker Hub publication from main |
| `tests/test_rotation.py` | Archive retention and rotation failure regression tests |
| `tests/smoke.py` | Disposable Docker pipeline test with real rsyslog, Alloy and Loki |

## Runtime

| Resource | Value |
| --- | --- |
| Namespace | `monitoring` (must already exist) |
| Deployment / Service | `rsyslog` |
| rsyslog image | `ninjatec/rsyslog:1.0.16` |
| Alloy image | `grafana/alloy:v1.22.0` |
| External endpoint | `rsyslog.lab.local`, `192.168.0.141`, TCP/UDP 514 |
| Loki endpoint | `http://grafana-loki-gateway.monitoring.svc.cluster.local/loki/api/v1/push` |
| Active log | `/mnt/rsyslog/syslog.jsonl` in rsyslog, `/var/log/remote/syslog.jsonl` in Alloy |
| Archives | Five files of approximately 100MiB each; manual replay if unread across a restart |
| Reader state | `/var/lib/alloy`, backed by the volume's `alloy` directory |
| Storage | Existing `rsyslog-pv-claim`; provision and manage according to deployment needs |
| Rollout strategy | `Recreate` |
| Alloy HTTP | Pod port 12345: UI, metrics, readiness and health |

## GitOps

`clusters/my-cluster/kustomization.yaml` includes the generated Flux bootstrap and a `syslogtoloki` Flux Kustomization targeting the repository root. Generated Flux controller definitions are unchanged.

The separate `image-policy.yaml` and `image-update.yaml` examples are excluded from the application Kustomization. There is no active image-update automation or SOPS configuration in the application manifests.

## Delivery limits

The JSON file retains full message text and process IDs. Loki indexes hostname, application, facility and severity alongside the existing `varlogs` job label. Read positions persist, but they track reading rather than acknowledged Loki delivery. UDP, exhausted Loki retries, retention expiry and receiver downtime can lose logs. See `README.md` for cutover, verification and recovery limits.
