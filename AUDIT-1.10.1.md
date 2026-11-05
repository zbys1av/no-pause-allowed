# Local audit fixes, 1.10.1

The local source is the authoritative source for this update. It was compared
with the source copy used for the previous verified Windows portable build;
no GitHub checkout was used to overwrite local changes.

Selective analysis inspects packet timestamps without decoding pixels before
enabling accurate input seeking. Seeking is restricted to supported progressive
codecs with verified uniform packet PTS, zero source origin and a sample-aligned
start. Unknown/VFR timing uses one continuous decoder, preserving the existing
sampling cadence. Short jobs, dense windows and large timestamp scans also use
one decoder. Each selected window initializes its own temporal tracker and
retains context. Progress is weighted by window duration.

Benchmark reproduction (run separately from the GUI):

```
python scripts/benchmark_manual.py --seconds 60 --policy legacy --output build/before.json
python scripts/benchmark_manual.py --seconds 60 --policy auto --output build/after.json
python scripts/benchmark_manual.py --seconds 120 --policy auto --fragments 12 --output build/dense.json
```

The benchmark reports wall time, Python CPU, FFmpeg wall time (including pipe
backpressure), selected RGB frames, pair checks, process count and strategy.
It does not report decoder-internal frame count or peak process-tree memory.
Numbers from concurrent runs are unsuitable for direct timing comparisons.

Both timeline modes share segment/layer visibility policy. Hidden ranges are
scoped to video paths, can be restored individually, and retain visibility when
only the editing action changes. A range whose boundaries are changed, split or
merged becomes visible; stale exact-range keys are removed. Filters do not alter
plans, analysis, statistics or export.

Remaining validation: broad real-world VFR/codec corpus, decoder-internal frame
and process-tree memory metrics, visual review of the native EXE, further UI
spacing/animation work and audio-visual sync measurement beyond existing media
and export tests. This patch does not claim completion of the entire audit.
