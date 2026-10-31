"""Sequential bounded-size encoding, shared by edited preview and final export."""
import os
from pathlib import Path
import tempfile
from .model import validate_plan
from .process import run, tool
from .errors import AppError
from .i18n import Text


def tempo(speed):
    filters = []
    while speed > 2:
        filters.append("atempo=2")
        speed /= 2
    filters.append(f"atempo={speed:.8f}")
    return ",".join(filters)


def export(video, plan, destination, job, progress=lambda *_: None,
           crf=23, mute=False, preview=False, encoder="libx264"):
    validate_plan(plan, video.duration)
    destination = Path(destination).absolute()
    if destination.resolve() == video.path.resolve():
        raise AppError("error.original")
    if destination.exists():
        raise AppError("error.exists")
    if destination.suffix.lower() != ".mp4":
        raise AppError("error.mp4")
    if crf not in (18, 23, 28) or encoder not in ("libx264", "h264_nvenc", "h264_qsv", "h264_amf"):
        raise AppError("error.encoder")
    kept = [s for s in plan if s.action != "remove"]
    total = sum(s.output_duration for s in kept)
    if not kept or total < .02:
        raise AppError("error.empty")
    # Same volume as destination: final hard-link publishes atomically, without clobber.
    with tempfile.TemporaryDirectory(prefix="smartcut-", dir=destination.parent) as directory:
        temp = Path(directory)
        done = 0.
        audio = video.audio and not mute
        for i, s in enumerate(kept):
            job.check()
            speed = s.speed if s.action == "speed" else 1
            vf = f"setpts=(PTS-STARTPTS)/{speed}"
            if preview:
                vf += ",scale=w='min(1280,iw)':h=-2"
            # yuv420 H.264 requires even dimensions. Pad at most one pixel, never crop.
            vf += ",pad=ceil(iw/2)*2:ceil(ih/2)*2,format=yuv420p"
            args = [tool("ffmpeg"), "-hide_banner", "-loglevel", "error", "-nostdin", "-n",
                    "-filter_threads", "2", "-filter_complex_threads", "2",
                    "-threads", "2", "-ss", f"{s.start:.9f}", "-t", f"{s.end-s.start:.9f}",
                    "-i", str(video.path)]
            if audio:
                # A finite silence bed preserves delayed/missing audio at either edge
                # of a cut. first_pts=0 inserts leading silence from source timestamps.
                args += ["-f", "lavfi", "-i", f"anullsrc=r=48000:cl=stereo:d={s.end-s.start:.9f}"]
            args += ["-map", "0:v:0", "-vf", vf, "-fps_mode", "vfr",
                    "-c:v", encoder, "-threads", "2"]
            if encoder == "libx264":
                # Segment encoding must not wait for future frames while the
                # audio mixer is waiting for the first video packet. Removing
                # lookahead also bounds memory for short cuts on modest PCs.
                args += ["-crf", str(crf), "-preset", "veryfast" if preview else "fast",
                         "-tune", "zerolatency"]
            elif encoder == "h264_nvenc":
                args += ["-rc", "vbr", "-cq", str(crf), "-b:v", "0"]
            elif encoder == "h264_qsv":
                args += ["-global_quality", str(crf)]
            else:
                args += ["-rc", "cqp", "-qp_i", str(crf), "-qp_p", str(crf)]
            if audio:
                audio_speed = f",{tempo(speed)}" if speed != 1 else ""
                graph = ("[0:a:0]aresample=48000:async=1:first_pts=0,aformat=channel_layouts=stereo[a];"
                         "[1:a:0][a]amix=inputs=2:duration=first:dropout_transition=0:normalize=0,"
                         f"asetpts=PTS-STARTPTS{audio_speed},apad[outa]")
                args += ["-filter_complex", graph, "-map", "[outa]",
                         "-ac", "2", "-c:a", "pcm_s16le"]
            else:
                args += ["-an"]
            # MOV carries explicit decode timing for H.264 and supports PCM.
            # Matroska may omit DTS; remuxing a short FFV1-derived segment can
            # then infer 1000 fps and collapse an otherwise valid 0.7 s cut.
            args += ["-t", f"{s.output_duration:.9f}", "-progress", "pipe:1", str(temp / f"part{i}.mov")]
            base = done
            run(args, job, s.output_duration,
                lambda fraction, stage: progress(.9*(base+fraction*s.output_duration)/total, stage),
                Text("stage.encoding", {"section":i+1,"count":len(kept)}))
            done += s.output_duration
        manifest = temp / "segments.txt"
        manifest.write_text("".join(f"file 'part{i}.mov'\nduration {s.output_duration:.9f}\n" for i, s in enumerate(kept)), encoding="utf-8")
        args = [tool("ffmpeg"), "-hide_banner", "-loglevel", "error", "-nostdin", "-n",
                "-f", "concat", "-safe", "1", "-i", str(manifest), "-map", "0:v:0", "-c:v", "copy"]
        args += ["-map", "0:a:0", "-c:a", "aac", "-b:a", "192k"] if audio else ["-an"]
        pending = temp / "output.mp4"
        args += ["-t", f"{total:.9f}", "-movflags", "+faststart", "-progress", "pipe:1", str(pending)]
        run(args, job, total, lambda f, stage: progress(.9+.1*f, stage), Text("stage.finalizing"))
        job.check()
        # os.link fails rather than replacing a file created concurrently.
        os.link(pending, destination)
    progress(1., Text("stage.export_complete"))
    return destination


def proxy(video, destination, job, progress=lambda *_: None):
    from .model import Segment
    return export(video, [Segment(0, video.duration)], destination, job, progress, preview=True)
