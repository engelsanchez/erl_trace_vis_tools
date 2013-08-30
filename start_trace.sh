#!/usr/bin/env bash
set -e
SESSION=$1
lttng create $SESSION
lttng enable-channel channel0 -s $SESSION -k --subbuf-size 2M
lttng enable-channel channel0 -s $SESSION -u --subbuf-size 2M
lttng enable-event -s $SESSION -a -k
lttng disable-event sched_stat_runtime,sched_stat_wait -s $SESSION -k
lttng enable-event -s $SESSION -a -u
lttng add-context -s $SESSION -k -t vpid -t vtid -t procname
lttng add-context -s $SESSION -u -t procname -t vpid -t vtid
