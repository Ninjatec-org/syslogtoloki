FROM ubuntu:24.04
RUN apt-get update \
    && apt-get install -y --no-install-recommends rsyslog \
    && rm -rf /var/lib/apt/lists/*
RUN mkdir -p /mnt/rsyslog
COPY rsyslog.conf /etc/rsyslog.conf
COPY --chmod=755 log_rotation.sh /usr/local/bin/log_rotation.sh
ENTRYPOINT ["rsyslogd", "-n"]
