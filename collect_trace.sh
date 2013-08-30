#!/usr/bin/env bash
set -e
S=$1
echo applying to session $S
lttng start $S && sleep 0.400 && lttng stop $S
lttng destroy $S
