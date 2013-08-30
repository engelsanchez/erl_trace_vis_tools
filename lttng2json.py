#!/usr/bin/env python

from __future__ import print_function
import os, re, json, sys
from collections import namedtuple

TraceEvent = namedtuple('TraceEvent',
        ['secs', 'nsecs', 'name', 'cpu', 'pid', 'tid', 'procname', 'args'])

rx = re.compile(r"""^
    # 24hr based timestamp
    \[(?P<hours>\d\d):(?P<mins>\d\d):(?P<secs>\d\d)\.(?P<nsecs>\d{9})\]
    \s+
    \([^)]+\)   # Time relative to last event
    \s+
    \S+         # Host
    \s+
    (?P<name>\S+):         # Event name
    \s+
    {\s+cpu_id\s+ = \s+(?P<cpu>\d+)\s+} # cpu_id
    \s*,\s*
    {\s*(?P<context>.*)\s*} 
    \s*,\s*
    {\s*(?P<args>.*)\s*}
    \s*$
    """, re.X)

start_secs = None 
start_nsecs = None
secs_in_day = 24 * 60 * 60

def time_diff(secs1, nsecs1, secs2, nsecs2):
    dnsecs = nsecs2 - nsecs1
    if dnsecs < 0:
        dnsecs += 1000000000
        secs2 -= 1
    dsecs = secs2 - secs1
    if dsecs < 0:
        dsecs += secs_in_day
    return dsecs + dnsecs / 1e9

dict_pattern = re.compile("\s*(\S+)\s* = \s*(([-+]?[0-9]+)|\"([^\"]*)\"|(\S+))\s*(,|$)")

def to_dict(str):
    """
    Parses a list of name value pairs separated by '=' into a dict.
    Uses a naive approach that should be enough for babeltrace output.
    """
    result = {}
    for m in dict_pattern.finditer(str):
        name = m.group(1)
        if m.group(3):
            result[name] = int(m.group(3))
        elif m.group(4):
            result[name] = m.group(4)
        else:
            result[name] = m.group(5)
    return result

def event_iter():
    global start_secs, start_nsecs
    for line in sys.stdin:
        #print("Original line is ", line)
        m = rx.match(line)
        if m:
            ctx = to_dict(m.group("context"))
            args = to_dict(m.group("args"))
            hours = int(m.group("hours"))
            mins = int(m.group("mins"))
            secs = int(m.group("secs"))
            nsecs = int(m.group("nsecs"))
            day_secs = secs + (mins + hours * 60) * 60
            event = TraceEvent(
                    secs = day_secs,
                    nsecs = nsecs,
                    name = m.group("name"),
                    cpu = int(m.group("cpu")),
                    tid = ctx["vtid"],
                    pid = ctx["vpid"],
                    procname = ctx["procname"],
                    args = args
                    )
            if start_secs is None:
                start_secs = day_secs
                start_nsecs = nsecs
            yield event
        else:
            print("Could not parse line : ", line, file=sys.stderr)

def load_sched_tids(fname):
    """
    Loads file containing mapping of Beam scheduler thread to tid
    and returns it a dict of 'tid' -> scheduler number.
    """
    result = {}
    with open(fname, 'r') as f:
        p = re.compile("^(\d+)\s+(\d+)$")
        for line in f:
            m = p.match(line)
            if m:
                result[int(m.group(2))] = int(m.group(1))
    #print("Tids results", result)
    return result

def do_sched_switch(ev, scheds):
    prev_tid = ev.args['prev_tid']
    next_tid = ev.args['next_tid']
    out = []

    if prev_tid in scheds:
        #print("Matched prev_tid on ", ev)
        s = scheds[prev_tid]
        if s['running']:
            p = s.get('process')
            dt = time_diff(s['secs'], s['nsecs'], ev.secs, ev.nsecs) 
            t = s['last_time']
            snum = s['number']
            if p:
                out.append((snum, {'cl':'p', 't':t, 'dt':dt, 'x':{'pid':p['pid']} }))
            else:
                out.append((snum, {'cl':'s', 't':t, 'dt':dt}))
            s['running'] = False

    if next_tid in scheds:
        #print("Matched next_tid on ", ev)
        s = scheds[next_tid]
        print("Setting time ", start_secs, start_nsecs, ev.secs, ev.nsecs)
        s['last_time'] = time_diff(start_secs, start_nsecs, ev.secs, ev.nsecs)
        s['secs'] = ev.secs
        s['nsecs'] = ev.nsecs
        s['running'] = True  # But maybe not in userspace yet until syscall exit

    return out

def do_process_scheduled(event, scheds):
    return []

def do_process_unscheduled(event, scheds):
    return []

def do_irq_entry(event, scheds):
    return []

def do_irq_exit(event, scheds):
    return []

def visual_blocks_iter(sched_tids):
    """
    Convert raw events into visualization items:
        - Beam scheduler running
        - Enter with sched_switch with next_tid = scheduler tid
        - Exit with sched_switch with prev_tid = scheduler tid
        - Process scheduled
        - Enter with erlang:process_scheduled
        - Exit with beam scheduler exit and erlang:process_unscheduled
    """
    scheds = {tid:{'number':snum, 'running':False} for tid, snum in sched_tids.iteritems() }
    #print("Scheds ", scheds)
    handlers = {
            'sched_switch': do_sched_switch,
            'erlang:process_scheduled': do_process_scheduled,
            'erlang:process_unscheduled': do_process_unscheduled,
            'irq_handler_entry':do_irq_entry,
            'irq_handler_exit':do_irq_exit,
            'softirq_entry':do_irq_entry,
            'softirq_exit':do_irq_exit
            }

    for e in event_iter():
        handler = handlers.get(e.name)
        if handler:
            for el in handler(e, scheds):
                yield el


tids_file = sys.argv[1]
sname = sys.argv[2] if len(sys.argv) > 2 else "default"
os.mkdir(sname)
tids = load_sched_tids(tids_file)
s_files = ['dummy0']
s_file_started = {}

try:
    for sn in range(1,len(tids)+1):
        s_files.append(open("%s/sched%d.json" % (sname, sn), 'w'))
    for s, block in visual_blocks_iter(tids):
        f = s_files[s]
        if s_file_started.get(s, False):
            print(',', file=f)
        else:
            print('{"data":[', file=f)
            s_file_started[s] = True
        json.dump(block, f)
finally:
    for f in s_files:
        try:
            print('\n]}', file=f)
            f.close()
        except:
            pass

