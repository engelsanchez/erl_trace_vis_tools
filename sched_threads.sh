#!/usr/bin/env bash
S=$1
babeltrace $S | grep sched_num |  sed -r 's/^.*vtid = ([0-9]+) .*sched_num = ([0-9]+).*$/\2 \1/' | sort -nk 1,1 -S 1G | uniq
