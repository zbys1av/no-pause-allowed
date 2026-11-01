"""Bounded adjacent-pair workers and a single ordered temporal consumer."""
from collections import deque
from concurrent.futures import ThreadPoolExecutor, wait
import os
from pathlib import Path
import time
from statistics import median


def resources():
    cpus=os.cpu_count() or 1
    try:cpus=min(cpus,len(os.sched_getaffinity(0)))
    except AttributeError:pass
    try:
        quota,period=Path('/sys/fs/cgroup/cpu.max').read_text().split()
        if quota!='max':cpus=min(cpus,max(1,int(quota)//int(period)))
    except (OSError,ValueError):pass
    available=0
    if os.name=='nt':
        import ctypes
        class Memory(ctypes.Structure):
            _fields_=[('length',ctypes.c_ulong),('load',ctypes.c_ulong)]+[(n,ctypes.c_ulonglong) for n in ('total','available','page_total','page_available','virtual_total','virtual_available','extended')]
        data=Memory();data.length=ctypes.sizeof(data)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(data)):available=data.available
    else:
        try:available=os.sysconf('SC_AVPHYS_PAGES')*os.sysconf('SC_PAGE_SIZE')
        except (ValueError,OSError):pass
        try:
            limit=Path('/sys/fs/cgroup/memory.max').read_text().strip()
            used=int(Path('/sys/fs/cgroup/memory.current').read_text())
            if limit!='max':available=min(available or int(limit),max(0,int(limit)-used))
        except (OSError,ValueError):pass
    return cpus,available


def choose_workers(requested,duration,pixels):
    cpus,memory=resources()
    per_worker=max(32*1024**2,pixels*40)
    memory_cap=max(1,min(8,(memory//3)//per_worker)) if memory else 2
    memory_cap=max(n for n in (1,2,4,8) if n<=memory_cap)
    if requested:
        return min(requested,memory_cap),dict(cpus=cpus,available_memory=memory,reason='manual_with_memory_cap')
    count=1
    if duration>=15 and pixels>=300000 and cpus>=4:count=2
    if duration>=120 and pixels>=500000 and cpus>=8:count=4
    return min(count,memory_cap),dict(cpus=cpus,available_memory=memory,reason='auto_conservative_cpu_duration_resolution_memory')


class PairPipeline:
    def __init__(self,workers,prepare,job,auto=False):
        self.limit=workers;self.auto=auto
        self.workers=1 if auto else workers;self.prepare=prepare;self.job=job
        self.executor=None;self.pending=deque();self.failed=False
        self.costs=deque(maxlen=32);self.target=self.workers
        self.stats=dict(workers=self.workers,configured_workers=workers,max_workers_used=self.workers,
                        queue_limit=2*workers,max_pending=0,pairs=0,
                        fallbacks=0,worker_wall_sum_s=0.,errors=[],worker_events=[])

    def __enter__(self):
        if self.workers>1:self.executor=ThreadPoolExecutor(max_workers=self.workers,thread_name_prefix='smartcut-pair')
        return self

    def __exit__(self,*exc):
        for _,_,_,future in self.pending:
            if future is not None:future.cancel()
        self.pending.clear()
        if self.executor:self.executor.shutdown(wait=True,cancel_futures=True)

    def calculate(self,previous,frame):
        self.job.check();start=time.perf_counter()
        value=self.prepare(previous,frame)
        return value,time.perf_counter()-start

    def consume(self):
        timestamp,frame,previous,future=self.pending.popleft()
        if future is None:
            value,elapsed=self.calculate(previous,frame)
        else:
            while not future.done():
                self.job.check()
                wait((future,),timeout=.1)
            self.job.check()
            try:
                value,elapsed=future.result()
            except Exception as error:
                self.job.check()
                self.failed=True;self.stats['fallbacks']+=1
                if len(self.stats['errors'])<10:self.stats['errors'].append(type(error).__name__+': '+str(error))
                # Retry this pair synchronously, preserving the full stream.
                # Worker-raised TimeoutError must also enter this fallback.
                value,elapsed=self.calculate(previous,frame)
        self.stats['pairs']+=1;self.stats['worker_wall_sum_s']+=elapsed
        self.costs.append(elapsed)
        if self.auto and self.limit>1 and not self.failed and self.stats['pairs']%32==0:
            cost=median(self.costs)
            if cost<.001:self.target=1
            elif cost>.0025:self.target=min(self.limit,2 if cost<.006 else 4)
        return timestamp,frame,value

    def resize(self,timestamp):
        if self.target==self.workers:return
        if self.executor:self.executor.shutdown(wait=True,cancel_futures=True)
        self.workers=self.target
        self.executor=ThreadPoolExecutor(max_workers=self.workers,thread_name_prefix='smartcut-pair') if self.workers>1 else None
        self.stats['workers']=self.workers
        self.stats['max_workers_used']=max(self.stats['max_workers_used'],self.workers)
        if len(self.stats['worker_events'])<128:self.stats['worker_events'].append(dict(time_s=timestamp,workers=self.workers,reason='measured_pair_cost'))

    def frames(self,source):
        previous=None
        for timestamp,frame in source:
            self.job.check()
            if previous is None:
                previous=frame;yield timestamp,frame,None;continue
            if self.executor and not self.failed:future=self.executor.submit(self.calculate,previous,frame)
            else:future=None
            self.pending.append((timestamp,frame,previous,future));previous=frame
            self.stats['max_pending']=max(self.stats['max_pending'],len(self.pending))
            if len(self.pending)>=max(1,self.workers*2 if self.executor else 1):
                yield self.consume()
                if self.target!=self.workers:
                    while self.pending:yield self.consume()
                    self.resize(timestamp)
        while self.pending:yield self.consume()
