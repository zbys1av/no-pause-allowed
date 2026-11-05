# 1.10.2: manual ranges in continuous decoding

The supplied selected-parts report used continuous_irregular_or_unknown_pts and
processed exactly the same selected frames as the unmarked run. The fallback
had expanded decode windows to the full video. This release retains the merged
context windows and applies their union after the original sample-selection
filter but before crop/scale/RGB conversion. One FFmpeg process still decodes
sequentially. Only window frames enter Python; each window has a fresh tracker
and pair pipeline, avoiding comparisons across missing time.

VFR timestamps and the sampling phase are retained. Tests compare timestamps,
scores, activity and evidence with independent sequential window analysis.
Seeking remains restricted to verified eligible CFR sources. Dense/short jobs
use the same single-process filtered fallback.

Debug reports now include the exact seek rejection reason and relevant PTS,
manual confirmations, coverage windows and confirmation journal events.

Reproduce the old VFR fallback versus the new one, sequentially:

```
python scripts/benchmark_manual.py --seconds 60 --vfr --policy full --output build/vfr-before.json
python scripts/benchmark_manual.py --seconds 60 --vfr --policy auto --output build/vfr-after.json
```

Selected RGB frames are not decoder-internal frames. The fallback avoids
comparisons/conversion/transfer in manual interiors, not all source decoding.
The user's original media was not provided; the supplied reports cannot reveal
which seek rejection condition fired in 1.10.1. Validate that file with this
release's more detailed report. This release does not claim completion of all
earlier UI, memory instrumentation or broad codec/audio-sync audit items.
