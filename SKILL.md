---
name: match-color
description: Match the exposure, contrast, saturation, and neutral color balance of B-roll, inserts, pickups, or a second camera to a reference A-roll shot with local FFmpeg analysis and rendering. Use when the user asks to color-match shots, fix mixed-camera footage, make A-roll and B-roll look coherent, correct flat or cool footage against a preferred shot, or apply a restrained warm-and-punchy finishing look. Run after the edit is picture-locked; for a Palmier Pro timeline, finish and export through palmier-editing first.
---

# Video Color Match

Match each target clip to a representative reference clip without uploading media. Use `scripts/match_color.py` to sample both shots, estimate bounded corrections, and render a separate file.

## Guardrails

- Keep source media unchanged. The renderer refuses to overwrite an output unless `--overwrite` is explicit.
- Work from original-quality exports, not social-platform downloads, when both exist.
- Match exposure and white balance before adding a look. Do not force unrelated locations into identical colors.
- Reject HDR/PQ/HLG media rather than grading it with the SDR matcher. Normalize HDR footage in a color-managed editor first.
- Keep client media, reports, previews, and generated files outside this skill folder.
- Treat the automated match as a first pass. Skin, white objects, product colors, and clipping require visual review.

## Workflow

### 1. Choose the reference

Use the shot whose skin tone, white balance, exposure, and contrast should define the edit. Prefer a clean three-to-ten-second section with normal lighting. Avoid fades, title cards, extreme highlights, or a reference dominated by one colored object.

For multi-scene videos, choose one reference per lighting setup. Do not match daylight B-roll to a tungsten reference merely because both occur in the same edit.

### 2. Analyze before rendering

Confirm `python3`, `ffmpeg`, and `ffprobe` are available. Resolve the script path from this skill directory, then run:

```bash
python3 scripts/match_color.py analyze \
  --reference a-roll.mp4 \
  --input b-roll.mp4
```

The JSON report contains sampled luminance and saturation statistics, neutral-pixel confidence, the bounded adjustment values, warnings, and the generated FFmpeg filter. Use time windows when an otherwise useful file contains multiple lighting conditions:

```bash
python3 scripts/match_color.py analyze \
  --reference a-roll.mp4 --reference-start 12.0 --reference-end 18.0 \
  --input b-roll.mp4 --input-start 3.0 --input-end 8.0
```

If the report warns that few neutral pixels were found, reduce `--strength` or choose a better reference. The matcher deliberately limits corrections so content differences do not create extreme casts.

### 3. Render the neutral match

Start at strength `0.85`. Render to a new path:

```bash
python3 scripts/match_color.py render \
  --reference a-roll.mp4 \
  --input b-roll.mp4 \
  --output b-roll-matched.mp4 \
  --strength 0.85
```

Use lower values around `0.50`–`0.70` when locations or lighting are intentionally different. Use `1.0` only when the reference and target were shot under comparable conditions.

For the restrained finishing treatment recovered from the original workflow, add `--look warm-punch`. It layers a 1.10 contrast multiplier, 1.20 saturation multiplier, a filmic curve, warm color balance, and a subtle vignette after the neutral match:

```bash
python3 scripts/match_color.py render \
  --reference a-roll.mp4 \
  --input b-roll.mp4 \
  --output b-roll-matched-warm.mp4 \
  --look warm-punch
```

Apply the same look consistently across the sequence. Do not add it to only the B-roll when the A-roll has not received an equivalent finish.

### 4. Match multiple clips

Run analysis and rendering separately for each B-roll or camera source against the correct scene reference. Reusing one correction across every clip is only appropriate when those clips share the same camera and lighting setup.

### 5. Verify

Compare reference, source, and result on the same calibrated display. Check:

- skin and neutral objects do not shift green or magenta;
- whites and blacks retain detail;
- product and brand colors remain credible;
- cuts no longer jump in exposure, contrast, or overall warmth;
- output duration matches the source within one frame;
- audio remains present and synchronized.

If the result is too strong, lower `--strength`. If only one scene is wrong, choose narrower sample windows or a scene-specific reference rather than grading the whole file harder.
