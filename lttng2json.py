#!/usr/bin/env python

from __future__ import print_function
import os, re, json, sys
from collections import namedtuple

TraceEvent = namedtuple('TraceEvent',
        ['timestamp', 'secs', 'nsecs', 'name', 'cpu', 'pid', 'tid', 'procname', 'args'])

rx = re.compile(r"""^
    # 24hr based timestamp
    \[(?P<timestamp>(?P<hours>\d\d):(?P<mins>\d\d):(?P<secs>\d\d)\.(?P<nsecs>\d{9}))\]
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

class ErlangThread:
    def __init__(self, num):
        self.running = False
        self.secs = None
        self.nsecs = None
        self.last_event = None
        self.in_syscall = None

class Scheduler(ErlangThread):
    def __init__(self, num, tid):
        ErlangThread.__init__(self, num)
        self.number = num
        self.process = None
        self.tid = tid

class CPU:
    def __init__(self, num):
        self.thread = None
        self.number = num

class TraceState:
    """
    State of the world as trace events are parsed, including
    what schedulers are running and on what CPUs, etc
    """
    def __init__(self, num_cpus, tid2sched):
        self.start_secs = None
        self.start_nsecs = None
        self.scheds = {tid: Scheduler(snum, tid)\
                for tid, snum in tid2sched.iteritems() }
        self.cpus = [ CPU(n) for n in range(0, num_cpus) ]

def event_iter(state):
    """
    Converts output from Babeltrace in stdin into
    a stream of TraceEvent objects
    """
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
            timestamp = m.group("timestamp")
            day_secs = secs + (mins + hours * 60) * 60
            event = TraceEvent(
                    timestamp = timestamp,
                    secs = day_secs,
                    nsecs = nsecs,
                    name = m.group("name"),
                    cpu = int(m.group("cpu")),
                    tid = ctx["vtid"],
                    pid = ctx["vpid"],
                    procname = ctx["procname"],
                    args = args
                    )
            if state.start_secs is None:
                state.start_secs = day_secs
                state.start_nsecs = nsecs
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
    return result

def exit_sched_event(sched, ev, state):
    p = sched.process
    dt = time_diff(sched.last_event.secs, sched.last_event.nsecs,
            ev.secs, ev.nsecs)
    t = time_diff(state.start_secs, state.start_nsecs,
            sched.last_event.secs, sched.last_event.nsecs)
    snum = sched.number
    if p:
        return (snum,
                {'cl':'p', 't':t, 'dt':dt, 'x':{'pid':p['pid']}})
    else:
        dbg = "%s -> %s" % (sched.last_event, ev)
        o = {'cl':'s', 't':t, 'dt':dt, 'dbg':dbg}
        return (snum, o)

def do_sched_switch(ev, state):
    prev_tid = ev.args['prev_tid']
    next_tid = ev.args['next_tid']
    out = []

    if prev_tid in state.scheds:
        print("Matched prev_tid on ", ev)
        sched = state.scheds[prev_tid]
        if sched.running:
            syscall = sched.in_syscall
            if syscall:
                t = time_diff(state.start_secs, state.start_nsecs,
                    syscall.secs, syscall.nsecs)
                dt = time_diff(syscall.secs, syscall.nsecs,
                    ev.secs, ev.nsecs)
                dbg = "%s -> %s" % (syscall, ev)
                block = {'cl':'sc','t':t,'dt':dt,'n':syscall.name, 'dbg':dbg}
                print('===== syscall interrupted block:', ev, block)
                out.append((sched.number, block))
            else:
                block = exit_sched_event(sched, ev, state)
                print("======= exiting thread:", block)
                state.cpus[ev.cpu].thread = None
                out.append(block)
            sched.running = False
        else:
            print("thread not running")

    if next_tid in state.scheds:
        print("Matched next_tid on ", ev)
        sched = state.scheds[next_tid]
        sched.last_event = ev
        sched.running = True

        state.cpus[ev.cpu].thread = sched

    return out

def do_process_scheduled(ev, state):
    return []

def do_process_unscheduled(ev, state):
    return []

def do_syscall_entry(ev, state):
    thread = state.cpus[ev.cpu].thread
    if thread and thread.running and ev.tid == thread.tid:
        block = exit_sched_event(thread, ev, state)
        print("======= Erlang thread block during syscall:", ev, block)
        thread.in_syscall = ev
        thread.last_event = ev
        return [block]
    return []

def do_syscall_exit(ev, state):
    thread = state.cpus[ev.cpu].thread
    if thread and thread.running and thread.tid == ev.tid:
        syscall = thread.in_syscall
        if syscall:
            last_ev = thread.last_event
            thread.in_syscall = None
            t = time_diff(state.start_secs, state.start_nsecs,
                    last_ev.secs, last_ev.nsecs)
            thread.last_event = ev
            dt = time_diff(last_ev.secs, last_ev.nsecs, ev.secs, ev.nsecs)
            dbg = "%s -> %s" % (last_ev, ev)
            block = {'cl':'sc','t':t,'dt':dt,'n':syscall.name, 'dbg':dbg}
            print('===== syscall block:', ev, block)
            return [(thread.number, block)]
    return []

def do_irq_entry(ev, state):
    return []

def do_irq_exit(ev, state):
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
    state = TraceState(16, sched_tids)
    handlers = {
            'sched_switch': do_sched_switch,
            'erlang:process_scheduled': do_process_scheduled,
            'erlang:process_unscheduled': do_process_unscheduled,
            'irq_handler_entry':do_irq_entry,
            'irq_handler_exit':do_irq_exit,
            'softirq_entry':do_irq_entry,
            'softirq_exit':do_irq_exit,
            'exit_syscall':do_syscall_exit
            }

    for e in event_iter(state):
        handler = handlers.get(e.name)
        if not handler and e.name.startswith('sys_'):
            handler = do_syscall_entry
        if handler:
            #print(handler.func_name, ":", e)
            for el in handler(e, state):
                yield el


tids_file = sys.argv[1]
sname = sys.argv[2] if len(sys.argv) > 2 else "default"
if not os.path.isdir(sname):
    os.mkdir(sname)
tids = load_sched_tids(tids_file)
s_files = ['dummy0']
started_files = set()

try:
    for sn in range(1,len(tids)+1):
        s_files.append(open("%s/sched%d.json" % (sname, sn), 'w'))
    for s, block in visual_blocks_iter(tids):
        f = s_files[s]
        if f in started_files:
            print(',', file=f)
        else:
            print('{"data":[', file=f)
            started_files.add(f)
        json.dump(block, f)
finally:
    for f in s_files:
        try:
            if f in started_files:
                print('\n]}', file=f)
                f.close()
            else:
                f.close()
                os.remove(f.name)
        except:
            pass

