# Match Color

A portable AI skill for making A-roll, B-roll, pickups, and mixed-camera footage look like they belong in the same video.

Give a skill-compatible agent a preferred reference shot and a target clip. The agent samples both files locally, compares exposure, contrast, saturation, and neutral color balance, proposes bounded corrections, renders a separate matched file with FFmpeg, and verifies the result.

The source footage stays unchanged and no media is uploaded.

## What it does

- Matches a B-roll or second-camera clip to a preferred A-roll reference.
- Corrects exposure, contrast, saturation, and neutral color balance.
- Samples multiple frames instead of trusting one possibly unrepresentative still.
- Limits every correction so different locations do not acquire extreme color casts.
- Supports scene-specific time windows for files with changing lighting.
- Includes an optional restrained `warm-punch` finishing look.
- Produces a reviewable JSON report containing the measurements and FFmpeg filter.
- Refuses to overwrite the source unless overwrite behavior is explicitly enabled.

## Included files

- `SKILL.md` — the complete agent workflow, matching guidance, and visual QA rules.
- `scripts/match_color.py` — analyzes two shots, reports the proposed correction, and renders the matched output.
- `agents/openai.yaml` — optional UI metadata for OpenAI-compatible skill hosts.

## Requirements

- Python 3.10 or newer
- `ffmpeg` and `ffprobe`
- SDR source footage for the automatic matcher

The bundled script makes no network requests, installs no packages, and reads no credentials.

HDR/PQ/HLG footage must first be normalized to SDR in a color-managed workflow. The matcher detects those transfer functions and stops instead of silently applying an incorrect SDR grade.

## Install

Clone the repository:

```bash
git clone https://github.com/RBYHNDRDS/match-color.git
cd match-color
```

### Codex

```bash
mkdir -p "${HOME}/.codex/skills"
ln -s "${PWD}" "${HOME}/.codex/skills/match-color"
```

### Claude Code

```bash
mkdir -p "${HOME}/.claude/skills"
ln -s "${PWD}" "${HOME}/.claude/skills/match-color"
```

### Other compatible agents

```bash
mkdir -p "${HOME}/.agents/skills"
ln -s "${PWD}" "${HOME}/.agents/skills/match-color"
```

## Use it

Ask in normal language:

```text
Use $match-color to make this B-roll match my A-roll.
Keep the skin and product colors natural, render a separate review file,
and show me the analysis before applying a finishing look.
```

For several clips or lighting setups:

```text
Use $match-color on these A-roll and B-roll files.
Group the clips by lighting setup, choose the correct reference for each group,
and do not reuse one correction across unrelated scenes.
```

The agent should inspect the actual footage before selecting the reference and should visually compare the result against both source files after rendering.

## Analyze without rendering

Print the measurements, proposed correction, safety warnings, and generated FFmpeg filter:

```bash
python3 scripts/match_color.py analyze \
  --reference a-roll.mp4 \
  --input b-roll.mp4
```

Save the same analysis as JSON:

```bash
python3 scripts/match_color.py analyze \
  --reference a-roll.mp4 \
  --input b-roll.mp4 \
  --report color-match-report.json
```

## Render a neutral match

The default strength is `0.85`. The source is never replaced:

```bash
python3 scripts/match_color.py render \
  --reference a-roll.mp4 \
  --input b-roll.mp4 \
  --output b-roll-matched.mp4
```

Use a lower strength when the locations are intentionally different:

```bash
python3 scripts/match_color.py render \
  --reference a-roll.mp4 \
  --input b-roll.mp4 \
  --output b-roll-matched.mp4 \
  --strength 0.65
```

## Match specific sections

Use representative windows when a file contains fades, title cards, or multiple lighting conditions:

```bash
python3 scripts/match_color.py analyze \
  --reference a-roll.mp4 \
  --reference-start 12.0 \
  --reference-end 18.0 \
  --input b-roll.mp4 \
  --input-start 3.0 \
  --input-end 8.0
```

## Optional Warm + Punch finish

The optional finishing look adds a restrained contrast and saturation lift, a filmic curve, warm color balance, and a subtle vignette after the neutral match:

```bash
python3 scripts/match_color.py render \
  --reference a-roll.mp4 \
  --input b-roll.mp4 \
  --output b-roll-matched-warm.mp4 \
  --look warm-punch
```

Apply the same finishing look consistently across the complete sequence. Do not add it only to the B-roll when the A-roll has not received an equivalent finish.

## How the automatic match stays restrained

The analyzer measures representative frames from both files and derives bounded corrections:

| Adjustment | What it compares | Safety behavior |
|---|---|---|
| Exposure | Median luminance | Limits the brightness shift |
| Contrast | Shadow-to-highlight span | Caps expansion and compression |
| Saturation | Upper-quartile color intensity | Prevents extreme saturation changes |
| Color balance | Low-saturation neutral pixels | Skips correction when neutral evidence is weak |

This is a technical first pass, not a replacement for visual judgment. Skin, white objects, product colors, clipping, and the cut between shots still require review.

## Privacy and safety

- Video, audio, sample data, and reports stay local.
- The repository contains no client footage or example media.
- Source files are never replaced.
- Failed renders remain partial and cannot masquerade as completed outputs.
- HDR footage is rejected rather than incorrectly processed as SDR.
- Media, reports, work folders, environment files, keys, and tokens are excluded from Git.
- No telemetry, downloader, API integration, or credential reader is included.

## License

MIT. See `LICENSE`.
