# 1.10.3: AV1 seeking and exact EOF ranges

The supplied 1.10.2 reports identified AV1 as the seek rejection reason. AV1 is
now eligible only when the existing uniform-PTS, zero-origin and progressive
checks pass and the container presentation-frame count equals the timestamped
packet count. Unknown AV1 layouts still use single-process filtered decoding.
The AV1 regression encodes libaom MP4 with a multi-second GOP and verifies equal
sample timestamps, scores, classifications and evidence after a non-keyframe
seek. It also exercises the automatic strategy selection.

Input decoding is bounded before -i, with one second of decoder context after
the final window. Previously the output limit could not stop a select filter
which discarded all subsequent frames. Window selection still uses exact
original-time coordinates and the normal sampling cadence.

The displayed maximum of the millisecond range editor now means the exact
source EOF. Both upward and downward rounding are tested, including the
3268.733333-second source reported by the user. Interior boundaries are not
snapped. Manual confirmations and editing actions share these coordinates.

Original user media was not supplied. These validations use generated AV1 and
existing media tests; they cannot establish timing or codec eligibility for
that exact recording. No original videos are modified.
