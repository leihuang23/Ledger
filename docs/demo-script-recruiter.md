# Recruiter Walkthrough (2-3 minutes)

Narration track for the short portfolio cut:

| Asset | Path |
| --- | --- |
| Silent screen cut | [`docs/assets/ledger-walkthrough-recruiter.webm`](assets/ledger-walkthrough-recruiter.webm) (~140s) |
| Captions (WebVTT) | [`docs/assets/ledger-walkthrough-recruiter.vtt`](assets/ledger-walkthrough-recruiter.vtt) |
| Full five-minute reference | [`docs/demo-script.md`](demo-script.md) + [`docs/assets/ledger-walkthrough.webm`](assets/ledger-walkthrough.webm) (~196s) |

## Honest packaging notes

- The WebM is a **trimmed silent screen capture** from the existing Phase 6 Playwright recording. It is **not** a new voiceover recording.
- Do **not** claim a human VO exists on the file unless you record one separately against this script.
- Captions carry the full recruiter narrative so the piece works muted.
- All UI data is **synthetic seeded portfolio data**, not customer production data.
- Stripe appears only as an **optional test-mode evidence feed** in narration; it is not required for the demo path and is not shown as a live merchant platform.

Spoken length at a normal pace is about **2 minutes 15 seconds to 2 minutes 40 seconds** (~310-340 words). Match the visual segments below; pause on dense UI rather than racing the captions.

## Visual map (output cut)

| Cut time | Source (full webm) | What is on screen |
| --- | --- | --- |
| 0:00-0:20 | 0:00-0:20 | Home: MRR anomaly, failed renewals, seeded incidents |
| 0:20-0:38 | 0:24-0:42 | Agent version: immutable publish snapshot / fork |
| 0:38-0:53 | 0:55-1:10 | Tool registry: schemas, scopes, implementation refs |
| 0:53-1:14 | 1:18-1:39 | Run detail: root cause, citations, trace, cost, blocked tool |
| 1:14-1:24 | 1:39-1:49 | Approvals queue: high-risk drafts rejected with audit |
| 1:24-1:42 | 1:50-2:08 | Observability: success, p95 latency, estimated cost |
| 1:42-1:52 | 2:28-2:38 | Eval Studio: fresh dataset run for a published version |
| 1:52-2:20 | 2:48-3:16 | A-vs-B comparison: intentional regression highlighted |

## Narration

### 0:00-0:20 - Problem (MRR drop)

> “Ledger is a production-shaped revenue investigation agent for SaaS ops. Everything you see is synthetic seed data built for review, not a live customer environment.
>
> The prompt is simple: week-over-week paid MRR dropped. Failed renewals and affected accounts show up as business facts before any model prose.”

### 0:20-0:53 - Investigation setup

> “Published agent versions are immutable snapshots of prompt, model, tools, and permission scopes. Forking a version is how you change capabilities without rewriting history.
>
> The tool surface is explicit: typed input and output schemas, one implementation binding, and one fixed scope per tool. Runtime policy requires both an enabled tool id and an allowed scope.”

### 0:53-1:14 - Evidence, conclusion, citations

> “We launch the seeded MRR-drop incident. The run records ordered steps: revenue SQL, account detail, support tickets, and knowledge search when enabled.
>
> The report states a root cause with medium confidence, names affected accounts, and cites retrieved evidence for every major claim. Blocked tool calls stay in the timeline instead of vanishing into server logs. A local or hosted trace id, token counts, and estimated cost sit next to the report.”

### 1:14-1:24 - Safety (approval gate)

> “Customer-facing follow-up is a mock action only. High-risk drafts enter a global approval queue. Nothing sends until an operator approves; rejection is terminal and writes an audit event. In this recording both high-risk requests are rejected so the run can finish safely.”

### 1:24-1:52 - Auditability (trace, ops, eval)

> “The control plane aggregates success rate, p95 latency, and estimated cost per published version, then drills back into individual runs.
>
> Eval Studio runs the same six seeded incidents against a version so quality is measurable, not anecdotal.”

### 1:52-2:20 - Regression + Stripe honesty + close

> “Here baseline versus candidate shows a deliberate regression: remove a required evidence tool and a case that passed flips to fail. Release risk is visible, not buried in an average score.
>
> Optional later: a Stripe test-mode evidence adapter can feed customers, subscriptions, and invoices into the same Ledger models. Test-mode only, ingestion only, no live credentials, no checkout or refunds, and never required for the public read-only demo.
>
> The portfolio claim is not that the model sounds smart. It is that the system gathers the right evidence, cites it, gates risky actions, records an auditable trace, and catches behavioral regressions before release.”

## Word count and pacing

| Section | Approx. words | Target spoken time |
| --- | --- | --- |
| Problem | 70 | ~30s (overlap holds) |
| Investigation setup | 65 | ~28s |
| Evidence / conclusion | 85 | ~35s |
| Approval safety | 45 | ~18s |
| Audit / eval | 40 | ~16s |
| Regression + Stripe + close | 90 | ~38s |
| **Total** | **~395** | **~2:45 if spoken fully** |

If a recorded VO must finish inside the ~140s silent cut, trim Stripe to one sentence and compress the setup block; keep problem, citations, approval gate, and regression intact.

## Re-export commands

Regenerate the short cut after a new full capture (`cd apps/web && npm run portfolio:assets`):

```bash
# Requires ffmpeg. Source is silent; this only trims/concatenates frames.
ffmpeg -y -i docs/assets/ledger-walkthrough.webm -filter_complex "\
[0:v]trim=start=0:end=20,setpts=PTS-STARTPTS[v0];\
[0:v]trim=start=24:end=42,setpts=PTS-STARTPTS[v1];\
[0:v]trim=start=55:end=70,setpts=PTS-STARTPTS[v2];\
[0:v]trim=start=78:end=99,setpts=PTS-STARTPTS[v3];\
[0:v]trim=start=99:end=109,setpts=PTS-STARTPTS[v4];\
[0:v]trim=start=110:end=128,setpts=PTS-STARTPTS[v5];\
[0:v]trim=start=148:end=158,setpts=PTS-STARTPTS[v6];\
[0:v]trim=start=168:end=195.84,setpts=PTS-STARTPTS[v7];\
[v0][v1][v2][v3][v4][v5][v6][v7]concat=n=8:v=1:a=0[outv]" \
  -map "[outv]" -c:v libvpx -b:v 1M -deadline good -cpu-used 2 -auto-alt-ref 0 \
  docs/assets/ledger-walkthrough-recruiter.webm
```

If the full recording’s scene timings change, re-map source ranges before re-running, then retarget cue times in `ledger-walkthrough-recruiter.vtt`.

Optional: burn captions for a shareable file (does not replace the sidecar VTT):

```bash
ffmpeg -y -i docs/assets/ledger-walkthrough-recruiter.webm \
  -vf "subtitles=docs/assets/ledger-walkthrough-recruiter.vtt:force_style='FontSize=18,Outline=1'" \
  -c:v libvpx -b:v 1M -auto-alt-ref 0 \
  docs/assets/ledger-walkthrough-recruiter-captioned.webm
```

## Optional human voiceover (separate artifact)

If the captain records a VO later:

1. Speak this script while watching the short cut (or stretch holds in a new capture).
2. Mux without inventing that the stock WebM already contains speech:

```bash
ffmpeg -y -i docs/assets/ledger-walkthrough-recruiter.webm -i path/to/vo.wav \
  -c:v copy -c:a libopus -shortest \
  docs/assets/ledger-walkthrough-recruiter-vo.webm
```

Keep the silent cut and VTT as the default portfolio package until that mux exists.
