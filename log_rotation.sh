#!/bin/sh
set -eu

log_file=${1:-/mnt/rsyslog/syslog.jsonl}
for number in 4 3 2 1; do
    if [ -f "$log_file.$number" ]; then
        mv -- "$log_file.$number" "$log_file.$((number + 1))"
    fi
done
if [ -f "$log_file" ]; then
    mv -- "$log_file" "$log_file.1"
fi
