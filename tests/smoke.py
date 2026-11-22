"""Exercise production rsyslog/Alloy configuration against a disposable Loki."""

import datetime
import json
import os
import socket
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def docker(*args):
    return subprocess.check_output(["docker", *args], text=True).strip()


def wait_for(check):
    deadline = time.monotonic() + 60
    while True:
        try:
            result = check()
            if result:
                return result
        except (OSError, ValueError):
            pass
        if time.monotonic() >= deadline:
            raise AssertionError("Timed out waiting for the log pipeline")
        time.sleep(0.5)


def host_port(container, port):
    return int(docker("port", container, port).rsplit(":", 1)[1])


def http(url):
    with urllib.request.urlopen(url, timeout=5) as response:
        return response.read()


def main():
    image = os.environ["RSYSLOG_IMAGE"]
    alloy_image = next(
        line.split("image:", 1)[1].strip()
        for line in (ROOT / "deployment.yaml").read_text().splitlines()
        if "image: grafana/alloy:" in line
    )
    token = "syslog-smoke-" + uuid.uuid4().hex[:12]
    network = token
    receiver, alloy, loki = (token + suffix for suffix in ("-rsyslog", "-alloy", "-loki"))
    containers = []
    temporary = tempfile.TemporaryDirectory()
    shared = Path(temporary.name)
    docker("network", "create", network)
    try:
        (shared / "state").mkdir()
        config = shared / "config.alloy"
        config.write_text((ROOT / "config.alloy").read_text().replace(
            "http://grafana-loki-gateway.monitoring.svc.cluster.local",
            "http://" + loki + ":3100",
        ))
        docker("run", "--rm", image, "-N1")
        docker("run", "--rm", "-v", f"{config}:/etc/alloy/config.alloy:ro",
               alloy_image, "validate", "/etc/alloy/config.alloy")

        def start(name, *args):
            containers.append(name)
            docker("run", "-d", "--name", name, "--network", network, *args)

        start(loki, "-p", "127.0.0.1::3100", "grafana/loki:3.7.10",
              "-config.file=/etc/loki/local-config.yaml", "-ingester.wal-enabled=false")
        loki_url = f"http://127.0.0.1:{host_port(loki, '3100/tcp')}"
        wait_for(lambda: http(loki_url + "/ready"))
        # Match the runner so bind-mounted state remains readable and removable.
        start(alloy, "--user", f"{os.getuid()}:{os.getgid()}", "-p", "127.0.0.1::12345",
              "-v", f"{shared}:/var/log/remote:ro",
              "-v", f"{shared / 'state'}:/var/lib/alloy",
              "-v", f"{config}:/etc/alloy/config.alloy:ro",
              alloy_image, "run", "--server.http.listen-addr=0.0.0.0:12345",
              "--storage.path=/var/lib/alloy", "/etc/alloy/config.alloy")
        alloy_url = f"http://127.0.0.1:{host_port(alloy, '12345/tcp')}"
        wait_for(lambda: http(alloy_url + "/-/ready"))
        start(receiver, "-p", "127.0.0.1::514/tcp", "-p", "127.0.0.1::514/udp",
              "-v", f"{shared}:/mnt/rsyslog", image)
        tcp_port = host_port(receiver, "514/tcp")
        udp_port = host_port(receiver, "514/udp")

        def tcp(message):
            with socket.create_connection(("127.0.0.1", tcp_port), timeout=2) as sock:
                sock.sendall(message.encode() + b"\n")
            return True

        wait_for(lambda: tcp("<14>1 - readiness smoke - - - ready"))
        now = datetime.datetime.now(datetime.timezone.utc)
        tcp(f'<134>1 {now.isoformat()} linux-node sshd 1234 - - {token}-tcp "quoted"')
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.sendto(
                f"<28>{now.strftime('%b %d %H:%M:%S')} switch-01 switchd[567]: {token}-udp".encode(),
                ("127.0.0.1", udp_port),
            )

        def records():
            query = urllib.parse.urlencode({"query": '{job="varlogs"} |= "' + token + '"'})
            data = json.loads(http(loki_url + "/loki/api/v1/query_range?" + query))
            return [(stream["stream"], json.loads(line))
                    for stream in data["data"]["result"] for _, line in stream["values"]]

        wait_for(lambda: len(records()) == 2)
        written = [json.loads(line) for line in (shared / "syslog.jsonl").read_text().splitlines()]
        assert sum(token in record["message"] for record in written) == 2, "Duplicate file writes"

        def streamed_records():
            return [json.loads(line) for line in docker("logs", receiver).splitlines() if token in line]

        wait_for(lambda: len(streamed_records()) == 2)
        assert sorted(streamed_records(), key=lambda record: record["message"]) == sorted(
            [record for record in written if token in record["message"]],
            key=lambda record: record["message"],
        ), "Container stdout must match the syslog file"
        for labels, record in records():
            is_tcp = "-tcp" in record["message"]
            assert labels["hostname"] == ("linux-node" if is_tcp else "switch-01")
            assert labels["appname"] == ("sshd" if is_tcp else "switchd")
            assert labels["facility"] == ("local0" if is_tcp else "daemon")
            assert labels["severity"] == ("info" if is_tcp else "warning")
            assert record["procid"] == ("1234" if is_tcp else "567")
            assert "procid" not in labels
        time.sleep(1)
        assert len(records()) == 2, "Each syslog event must be written once"

        docker("stop", alloy)
        assert list((shared / "state").rglob("positions.yml")), "Missing persistent read positions"
        docker("start", alloy)
        alloy_url = f"http://127.0.0.1:{host_port(alloy, '12345/tcp')}"
        wait_for(lambda: http(alloy_url + "/-/ready"))
        tcp(f"<134>1 {now.isoformat()} linux-node sshd 1234 - - {token}-restart")
        wait_for(lambda: len(records()) == 3)
        metrics = http(alloy_url + "/metrics").decode()
        reads = sum(float(line.rsplit(" ", 1)[1]) for line in metrics.splitlines()
                    if line.startswith("loki_source_file_read_lines_total{"))
        assert reads == 1, f"Alloy reread old records after restart: {reads} lines"
        print("TCP/RFC5424, UDP/RFC3164, labels, JSON, stdout and persistent read positions passed")
    except BaseException:
        for name in containers:
            subprocess.run(["docker", "logs", "--tail", "40", name], check=False)
        raise
    finally:
        for name in reversed(containers):
            subprocess.run(["docker", "rm", "-f", name], check=False, stdout=subprocess.DEVNULL)
        docker("network", "rm", network)
        temporary.cleanup()


if __name__ == "__main__":
    main()
