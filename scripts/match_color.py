#!/usr/bin/env python3
"""Analyze and render a bounded SDR color match with FFmpeg."""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import shutil
import statistics
import subprocess
import sys
import uuid


HDR_TRANSFERS = {"arib-std-b67", "smpte2084"}


class MatchColorError(RuntimeError):
    pass


def clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        raise MatchColorError("Cannot calculate statistics from an empty sample.")
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return ordered[low]
    weight = position - low
    return ordered[low] * (1.0 - weight) + ordered[high] * weight


def executable(requested: str) -> str:
    resolved = shutil.which(requested)
    if not resolved:
        raise MatchColorError(f"Required executable not found: {requested}")
    return resolved


def run_capture(command: list[str], *, binary: bool = False) -> bytes | str:
    completed = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        error = completed.stderr.decode("utf-8", "replace").strip()
        raise MatchColorError(error or f"Command failed with exit code {completed.returncode}")
    if binary:
        return completed.stdout
    return completed.stdout.decode("utf-8", "replace")


def probe_media(path: Path, ffprobe: str) -> dict:
    if not path.is_file():
        raise MatchColorError(f"Input file does not exist: {path}")
    command = [
        ffprobe,
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=codec_name,width,height,r_frame_rate,duration,color_transfer,color_primaries,color_space:format=duration",
        "-of",
        "json",
        str(path),
    ]
    try:
        payload = json.loads(run_capture(command))
    except json.JSONDecodeError as exc:
        raise MatchColorError(f"ffprobe returned invalid JSON for {path}") from exc
    streams = payload.get("streams") or []
    if not streams:
        raise MatchColorError(f"No video stream found: {path}")
    stream = streams[0]
    duration_raw = stream.get("duration") or (payload.get("format") or {}).get("duration")
    try:
        duration = float(duration_raw) if duration_raw not in (None, "N/A") else None
    except (TypeError, ValueError):
        duration = None
    transfer = (stream.get("color_transfer") or "unknown").lower()
    if transfer in HDR_TRANSFERS:
        raise MatchColorError(
            f"HDR transfer {transfer!r} detected in {path.name}; normalize to SDR before matching."
        )
    return {
        "path": str(path.resolve()),
        "name": path.name,
        "codec": stream.get("codec_name") or "unknown",
        "width": int(stream.get("width") or 0),
        "height": int(stream.get("height") or 0),
        "frame_rate": stream.get("r_frame_rate") or "unknown",
        "duration": duration,
        "color_transfer": transfer,
        "color_primaries": stream.get("color_primaries") or "unknown",
        "color_space": stream.get("color_space") or "unknown",
    }


def sample_window(
    info: dict,
    requested_start: float | None,
    requested_end: float | None,
) -> tuple[float, float | None]:
    duration = info["duration"]
    if duration is None or duration <= 0:
        start = requested_start or 0.0
        end = requested_end
        if start < 0 or (end is not None and end <= start):
            raise MatchColorError(
                f"Invalid sample window for {info['name']}: start={start}, end={end}"
            )
        return start, end
    default_margin = min(duration * 0.05, 1.0) if duration > 2.0 else 0.0
    start = default_margin if requested_start is None else requested_start
    end = duration - default_margin if requested_end is None else requested_end
    if start < 0 or end <= start or end > duration + 0.05:
        raise MatchColorError(
            f"Invalid sample window for {info['name']}: start={start}, end={end}, duration={duration}"
        )
    return start, end


def extract_samples(
    path: Path,
    info: dict,
    ffmpeg: str,
    *,
    start: float,
    end: float | None,
    samples: int,
    size: int,
) -> tuple[bytes, int]:
    frame_bytes = size * size * 3
    command = [ffmpeg, "-v", "error", "-nostdin"]
    if start > 0:
        command.extend(["-ss", f"{start:.6f}"])
    if end is not None:
        span = end - start
        command.extend(["-t", f"{span:.6f}"])
    command.extend(["-i", str(path), "-an"])
    duration = info["duration"]
    if duration is not None and duration > 0 and end is not None:
        span = max(end - start, 0.001)
        rate = samples / span
        video_filter = f"fps={rate:.12f},scale={size}:{size}:flags=area,format=rgb24"
        frame_limit = samples
    else:
        video_filter = f"scale={size}:{size}:flags=area,format=rgb24"
        frame_limit = 1
    command.extend(
        [
            "-vf",
            video_filter,
            "-frames:v",
            str(frame_limit),
            "-pix_fmt",
            "rgb24",
            "-f",
            "rawvideo",
            "pipe:1",
        ]
    )
    raw = run_capture(command, binary=True)
    usable = len(raw) - (len(raw) % frame_bytes)
    raw = raw[:usable]
    frame_count = len(raw) // frame_bytes
    if frame_count == 0:
        raise MatchColorError(f"FFmpeg returned no sample frames for {path}")
    return raw, frame_count


def analyze_pixels(raw: bytes, frame_count: int) -> dict:
    red: list[float] = []
    green: list[float] = []
    blue: list[float] = []
    luminance: list[float] = []
    saturation: list[float] = []
    neutral_red: list[float] = []
    neutral_green: list[float] = []
    neutral_blue: list[float] = []

    for index in range(0, len(raw), 3):
        r = raw[index] / 255.0
        g = raw[index + 1] / 255.0
        b = raw[index + 2] / 255.0
        maximum = max(r, g, b)
        minimum = min(r, g, b)
        if maximum <= 0.025:
            continue
        y = 0.2126 * r + 0.7152 * g + 0.0722 * b
        sat = (maximum - minimum) / maximum if maximum > 0 else 0.0
        red.append(r)
        green.append(g)
        blue.append(b)
        luminance.append(y)
        saturation.append(sat)
        if 0.08 <= y <= 0.92 and sat <= 0.18:
            neutral_red.append(r)
            neutral_green.append(g)
            neutral_blue.append(b)

    if len(luminance) < 100:
        raise MatchColorError("Too few non-black pixels remained for reliable analysis.")
    neutral_count = len(neutral_red)
    if neutral_count:
        nr = statistics.fmean(neutral_red)
        ng = statistics.fmean(neutral_green)
        nb = statistics.fmean(neutral_blue)
        neutral_average = (nr + ng + nb) / 3.0
        cast = {
            "red": nr / neutral_average - 1.0,
            "green": ng / neutral_average - 1.0,
            "blue": nb / neutral_average - 1.0,
        }
    else:
        cast = {"red": 0.0, "green": 0.0, "blue": 0.0}

    low = percentile(luminance, 0.10)
    median = percentile(luminance, 0.50)
    high = percentile(luminance, 0.90)
    return {
        "sample_frames": frame_count,
        "sampled_pixels": len(luminance),
        "luminance": {
            "p10": round(low, 6),
            "p50": round(median, 6),
            "p90": round(high, 6),
            "span": round(high - low, 6),
        },
        "saturation_p75": round(percentile(saturation, 0.75), 6),
        "rgb_mean": {
            "red": round(statistics.fmean(red), 6),
            "green": round(statistics.fmean(green), 6),
            "blue": round(statistics.fmean(blue), 6),
        },
        "neutral_pixels": neutral_count,
        "neutral_fraction": round(neutral_count / len(luminance), 6),
        "neutral_cast": {key: round(value, 6) for key, value in cast.items()},
    }


def derive_adjustments(reference: dict, target: dict, strength: float, look: str) -> tuple[dict, list[str]]:
    ref_luma = reference["luminance"]
    target_luma = target["luminance"]
    target_span = max(target_luma["span"], 0.02)
    full_contrast = clamp(ref_luma["span"] / target_span, 0.82, 1.22)
    contrast = 1.0 + (full_contrast - 1.0) * strength
    predicted_median = 0.5 + contrast * (target_luma["p50"] - 0.5)
    brightness = clamp((ref_luma["p50"] - predicted_median) * strength, -0.12, 0.12)

    target_saturation = max(target["saturation_p75"], 0.04)
    full_saturation = clamp(reference["saturation_p75"] / target_saturation, 0.75, 1.35)
    saturation = 1.0 + (full_saturation - 1.0) * strength

    warnings: list[str] = []
    enough_neutrals = reference["neutral_fraction"] >= 0.01 and target["neutral_fraction"] >= 0.01
    if enough_neutrals:
        balance = {}
        for channel in ("red", "green", "blue"):
            difference = reference["neutral_cast"][channel] - target["neutral_cast"][channel]
            # FFmpeg's colorbalance control has a stronger visible response than
            # its numeric value suggests, so use a deliberately conservative gain.
            balance[channel] = clamp(difference * 0.30 * strength, -0.05, 0.05)
        average_balance = statistics.fmean(balance.values())
        balance = {key: value - average_balance for key, value in balance.items()}
    else:
        balance = {"red": 0.0, "green": 0.0, "blue": 0.0}
        warnings.append(
            "Fewer than 1% neutral pixels were found in the reference or target; white-balance correction was skipped."
        )

    if full_contrast in (0.82, 1.22):
        warnings.append("The requested contrast correction hit the safety limit; inspect the chosen sample windows.")
    if full_saturation in (0.75, 1.35):
        warnings.append("The requested saturation correction hit the safety limit; inspect the chosen sample windows.")

    if look == "warm-punch":
        contrast *= 1.10
        saturation *= 1.20

    adjustments = {
        "strength": round(strength, 4),
        "look": look,
        "contrast": round(clamp(contrast, 0.65, 1.55), 6),
        "brightness": round(brightness, 6),
        "saturation": round(clamp(saturation, 0.60, 1.75), 6),
        "balance": {key: round(value, 6) for key, value in balance.items()},
    }
    return adjustments, warnings


def build_filter(adjustments: dict) -> str:
    balance = adjustments["balance"]
    stages = [
        "eq="
        f"contrast={adjustments['contrast']:.6f}:"
        f"brightness={adjustments['brightness']:.6f}:"
        f"saturation={adjustments['saturation']:.6f}"
    ]
    if any(abs(value) >= 0.0005 for value in balance.values()):
        stages.append(
            "colorbalance="
            f"rs={balance['red']:.6f}:gs={balance['green']:.6f}:bs={balance['blue']:.6f}:"
            f"rm={balance['red']:.6f}:gm={balance['green']:.6f}:bm={balance['blue']:.6f}:"
            f"rh={balance['red']:.6f}:gh={balance['green']:.6f}:bh={balance['blue']:.6f}"
        )
    if adjustments["look"] == "warm-punch":
        stages.extend(
            [
                "curves=all='0/0 0.18/0.15 0.50/0.54 0.82/0.88 1/1'",
                "colorbalance=rs=0.025000:bs=-0.025000:rm=0.035000:bm=-0.035000:rh=0.020000:bh=-0.020000",
                "vignette=PI/14:eval=frame",
            ]
        )
    stages.append("format=yuv420p")
    return ",".join(stages)


def analyze_match(args: argparse.Namespace) -> dict:
    ffmpeg = executable(args.ffmpeg)
    ffprobe = executable(args.ffprobe)
    reference_path = Path(args.reference).expanduser()
    input_path = Path(args.input).expanduser()
    reference_info = probe_media(reference_path, ffprobe)
    input_info = probe_media(input_path, ffprobe)
    reference_start, reference_end = sample_window(
        reference_info, args.reference_start, args.reference_end
    )
    input_start, input_end = sample_window(input_info, args.input_start, args.input_end)
    reference_raw, reference_frames = extract_samples(
        reference_path,
        reference_info,
        ffmpeg,
        start=reference_start,
        end=reference_end,
        samples=args.samples,
        size=args.sample_size,
    )
    input_raw, input_frames = extract_samples(
        input_path,
        input_info,
        ffmpeg,
        start=input_start,
        end=input_end,
        samples=args.samples,
        size=args.sample_size,
    )
    reference_stats = analyze_pixels(reference_raw, reference_frames)
    input_stats = analyze_pixels(input_raw, input_frames)
    adjustments, warnings = derive_adjustments(reference_stats, input_stats, args.strength, args.look)
    video_filter = build_filter(adjustments)
    return {
        "schema": 1,
        "reference": {
            **reference_info,
            "sample_window": {"start": reference_start, "end": reference_end},
            "statistics": reference_stats,
        },
        "input": {
            **input_info,
            "sample_window": {"start": input_start, "end": input_end},
            "statistics": input_stats,
        },
        "adjustments": adjustments,
        "warnings": warnings,
        "ffmpeg_filter": video_filter,
    }


def write_report(path: str, report: dict, overwrite: bool) -> None:
    destination = Path(path).expanduser()
    if destination.exists() and not overwrite:
        raise MatchColorError(f"Report already exists (use --overwrite): {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


def render_match(args: argparse.Namespace, report: dict) -> None:
    ffmpeg = executable(args.ffmpeg)
    source = Path(args.input).expanduser()
    output = Path(args.output).expanduser()
    if source.resolve() == output.resolve():
        raise MatchColorError("Output must differ from the input path.")
    if output.exists() and not args.overwrite:
        raise MatchColorError(f"Output already exists (use --overwrite): {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    suffix = output.suffix or ".mp4"
    temporary = output.with_name(f".{output.stem}.partial-{uuid.uuid4().hex}{suffix}")
    command = [
        ffmpeg,
        "-v",
        "error",
        "-stats",
        "-nostdin",
        "-i",
        str(source),
        "-map",
        "0:v:0",
        "-map",
        "0:a?",
        "-vf",
        report["ffmpeg_filter"],
        "-c:v",
        "libx264",
        "-crf",
        str(args.crf),
        "-preset",
        args.preset,
        "-c:a",
        "aac",
        "-b:a",
        args.audio_bitrate,
        "-map_metadata",
        "-1",
    ]
    if suffix.lower() in {".mp4", ".m4v", ".mov"}:
        command.extend(["-movflags", "+faststart"])
    command.append(str(temporary))
    try:
        completed = subprocess.run(command, check=False)
        if completed.returncode != 0:
            raise MatchColorError(f"FFmpeg render failed with exit code {completed.returncode}")
        os.replace(temporary, output)
    finally:
        if temporary.exists():
            temporary.unlink()


def add_analysis_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--reference", required=True, help="Reference A-roll or preferred shot")
    parser.add_argument("--input", required=True, help="Target clip to match")
    parser.add_argument("--reference-start", type=float)
    parser.add_argument("--reference-end", type=float)
    parser.add_argument("--input-start", type=float)
    parser.add_argument("--input-end", type=float)
    parser.add_argument("--samples", type=int, default=12, help="Sample frames per video (default: 12)")
    parser.add_argument("--sample-size", type=int, default=96, help="Square analysis size (default: 96)")
    parser.add_argument("--strength", type=float, default=0.85, help="Match strength from 0 to 1")
    parser.add_argument("--look", choices=("neutral", "warm-punch"), default="neutral")
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--ffprobe", default="ffprobe")
    parser.add_argument("--report", help="Optional path for the JSON analysis report")
    parser.add_argument("--overwrite", action="store_true")


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    subparsers = root.add_subparsers(dest="command", required=True)
    analyze = subparsers.add_parser("analyze", help="Print a color-match report without rendering")
    add_analysis_arguments(analyze)
    render = subparsers.add_parser("render", help="Analyze and render a matched copy")
    add_analysis_arguments(render)
    render.add_argument("--output", required=True)
    render.add_argument("--crf", type=int, default=18)
    render.add_argument("--preset", default="medium")
    render.add_argument("--audio-bitrate", default="192k")
    return root


def validate_arguments(args: argparse.Namespace) -> None:
    if not 0.0 <= args.strength <= 1.0:
        raise MatchColorError("--strength must be between 0 and 1.")
    if not 2 <= args.samples <= 60:
        raise MatchColorError("--samples must be between 2 and 60.")
    if not 32 <= args.sample_size <= 256:
        raise MatchColorError("--sample-size must be between 32 and 256.")
    if hasattr(args, "crf") and not 0 <= args.crf <= 51:
        raise MatchColorError("--crf must be between 0 and 51.")


def main() -> int:
    args = parser().parse_args()
    try:
        validate_arguments(args)
        report = analyze_match(args)
        if args.report:
            write_report(args.report, report, args.overwrite)
        if args.command == "render":
            render_match(args, report)
            report["output"] = str(Path(args.output).expanduser().resolve())
        print(json.dumps(report, indent=2))
        return 0
    except MatchColorError as exc:
        print(f"match-color: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
