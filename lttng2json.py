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

class Span:
    """
    A time span defined by an opening and a close event.
    For example, entering and eventually exiting a system call.
    If across a user process, the opening event is the process being
    scheduled in, and the start_event is the original entry.
    """
    def __init__(self, open_event):
        self.open_event = open_event
        self.start_event = open_event
        self.close_event = None
        self.children = []

    def close(self, close_event):
        self.close_event = close_event

    def output_iter(self, state):
        dt = time_diff(self.open_event.secs, self.open_event.nsecs,
                self.close_event.secs, self.close_event.nsecs)
        t = time_diff(state.start_secs, state.start_nsecs,
                self.open_event.secs, self.open_event.nsecs)
        dbg = "%s -> %s" % (self.open_event, self.close_event)
        o = {'t':t, 'dt':dt, 'dbg':dbg}
        self.decorate(o)
        yield o
        for child in self.children:
            for p in child.output_iter(state):
                yield p

class SchedSpan(Span):
    def __init__(self, ev1):
        Span.__init__(self, ev1)

    def decorate(self, o):
        o['cl'] = 's'

class ProcessSpan(Span):
    def __init__(self, ev1):
        Span.__init__(self, ev1)

    def decorate(self, o):
        o['cl'] = 'p'
        o['pid'] = self.start_event.args['proc']

class PortSpan(Span):
    def __init__(self, ev1):
        Span.__init__(self, ev1)

    def decorate(self, o):
        o['cl'] = 't'

class SyscallSpan(Span):
    def __init__(self, ev1):
        Span.__init__(self, ev1)

    def decorate(self, o):
        o['cl'] = 'c'

class HIRQSpan(Span):
    def __init__(self, ev1):
        Span.__init__(self, ev1)

    def decorate(self, o):
        o['cl'] = 'h'

class SIRQSpan(Span):
    def __init__(self, ev1):
        Span.__init__(self, ev1)

    def decorate(self, o):
        o['cl'] = 'f'

class ErlangThread:
    def __init__(self, num):
        self.running = False
        self.secs = None
        self.nsecs = None
        self.last_event = None
        self.in_syscall = None
        self.stack = []
        self.span = None

    def sched_out(self, sched_out_event):
        self.running = False
        for span in self.stack:
            span.close_event = sched_out_event

    def sched_in(self, sched_in_event):
        self.running = True

    def get_events_iter(self, state):
        return self.span.output_iter(state)

    def current_span(self):
        return self.stack[-1] if len(self.stack) else None

class Scheduler(ErlangThread):
    def __init__(self, num, tid):
        ErlangThread.__init__(self, num)
        self.number = num
        self.process = None
        self.tid = tid

    def sched_in(self, sched_in_event):
        ErlangThread.sched_in(self, sched_in_event)
        self.span = SchedSpan(sched_in_event)
        self.stack.append(self.span)

class CPU:
    def __init__(self, num):
        self.thread = None
        self.number = num

class TraceState:
    """
    State of the world as trace events are parsed, including
    what schedulers are running and on what CPUs, etc
    """
    def __init__(self, tid2sched):
        self.start_secs = None
        self.start_nsecs = None
        self.scheds = {tid: Scheduler(snum, tid)\
                for tid, snum in tid2sched.iteritems() }
        self.cpus = [ CPU(n) for n in range(0, 128) ]

def event_iter(state):
    """
    Converts output from Babeltrace in stdin into
    a stream of TraceEvent objects
    """
    for line in sys.stdin:
        print("Original line is ", line)
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

def do_sched_switch(ev, state):
    prev_tid = ev.args['prev_tid']
    next_tid = ev.args['next_tid']
    out = []

    if prev_tid in state.scheds:
        #print("Matched prev_tid on ", ev)
        sched = state.scheds[prev_tid]
        if sched.running:
            state.cpus[ev.cpu].thread = None
            sched.sched_out(ev)
            for b in sched.get_events_iter(state):
                out.append((sched.number, b))
        else:
            print("scheduler", sched, "not running")

    if next_tid in state.scheds:
        #print("Matched next_tid on ", ev)
        sched = state.scheds[next_tid]
        sched.sched_in(ev)
        state.cpus[ev.cpu].thread = sched

    return out

def do_generic_open(ev, state, constructor):
    thread = state.cpus[ev.cpu].thread
    if thread and thread.running and ev.tid == thread.tid:
        current = thread.current_span()
        if current:
            s = constructor(ev)
            current.children.append(s)
            thread.stack.append(s)

def do_generic_close(ev, state, span_type):
    thread = state.cpus[ev.cpu].thread
    if thread and thread.running and ev.tid == thread.tid:
        current = thread.current_span()
        if isinstance(current, span_type):
            current.close(ev)
            thread.stack.pop()

def do_process_scheduled(ev, state):
    print("Process schedule", ev)
    do_generic_open(ev, state, ProcessSpan)
    return []

def do_process_unscheduled(ev, state):
    print("Process unschedule", ev)
    do_generic_close(ev, state, ProcessSpan)
    return []

def do_port_begin(ev, state):
    print("Port start", ev)
    do_generic_open(ev, state, PortSpan)
    return []

def do_port_end(ev, state):
    print("Port end", ev)
    do_generic_close(ev, state, PortSpan)
    return []

def do_syscall_entry(ev, state):
    do_generic_open(ev, state, SyscallSpan)
    return []

def do_syscall_exit(ev, state):
    do_generic_close(ev, state, SyscallSpan)
    return []

def do_hirq_entry(ev, state):
    do_generic_open(ev, state, HIRQSpan)
    return []

def do_hirq_exit(ev, state):
    do_generic_close(ev, state, HIRQSpan)
    return []

def do_sirq_entry(ev, state):
    do_generic_open(ev, state, SIRQSpan)
    return []

def do_sirq_exit(ev, state):
    do_generic_close(ev, state, SIRQSpan)
    return []

class Handlers:
    handlers = {
            'sched_switch': do_sched_switch,
            'erlang:process_scheduled': do_process_scheduled,
            'erlang:process_unscheduled': do_process_unscheduled,
            'erlang:begin_port_tasks': do_port_begin,
            'erlang:end_port_tasks': do_port_end,
            'irq_handler_entry':do_hirq_entry,
            'irq_handler_exit':do_hirq_exit,
            'softirq_entry':do_sirq_entry,
            'softirq_exit':do_sirq_exit,
            'exit_syscall':do_syscall_exit
            }

    @classmethod
    def get_handler(cls, event_name):
        handler = Handlers.handlers.get(event_name)
        if not handler and event_name.startswith('sys_'):
            return do_syscall_entry
        return handler


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
    state = TraceState(sched_tids)

    for e in event_iter(state):
        print("Event", e)
        handler = Handlers.get_handler(e.name)
        if handler:
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
    for sblock in visual_blocks_iter(tids):
        #print("sblock", sblock)
        s, block = sblock
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

