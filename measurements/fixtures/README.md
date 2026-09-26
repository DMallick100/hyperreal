# Captured host artefacts

Things recorded from the **real** Claude Code host, so the shim host
(`measurements/live_shim_host.py`) reproduces them instead of assuming them.

## `host_pretooluse_envelope.json` — CAPTURED 2026-09-26

**Status: present. Spec N8 gate 3 is CLOSED as a fact as well as a mechanism.**
Captured by `measurements/capture_host_envelope.py` from one real `claude -p`
session (~2¢) once the operator authorised that call, alongside the $2 ceiling
that closed gate 0.

`load_host_envelope()` raises `EnvelopeMissing` when the file is absent rather
than falling back to a handwritten envelope. That is deliberate:
`measurements/envelope_ablation.py` already measured that three envelope shapes
gave the same verdict on this gate, so a handwritten one is *probably* harmless —
and "probably harmless" is exactly the class of assumption that gets published as
a gate's behaviour. A refusal makes the gate something the runner enforces rather
than something a document promises.

### What the capture found that nobody had written down

The host's envelope carries **nine** keys, and two of them are ones this repo had
not thought about: `permission_mode` and `prompt_id`. `envelope_for()` templates
five (`session_id`, `cwd`, `tool_input`, `tool_use_id`, `transcript_path`) and
carries the rest through verbatim, so both survive into every shim envelope —
which is the entire reason the fixture is *captured* rather than *written*. A
handwritten envelope would have had seven keys and looked complete.

Note the consequence, since it is a limitation and not a feature: `prompt_id` is
a per-call field of the *capture* session, and the shim reproduces that one
constant on every call rather than minting a fresh one. `envelope_ablation.py`
measured prompt id as one of the fields that changed no verdict on this gate, so
it is recorded here rather than fixed.

Seven hooks echoed the envelope back and all seven agreed on everything but the
templated fields; the capture refuses to store anything if they disagree, because
"this is the host's envelope" should be a measurement and not a claim.

### How it was captured (and how to recapture)

`python3 measurements/capture_host_envelope.py --force`, which does the below and
then finds the envelope **structurally** — by the keys only the host can write —
rather than reconstructing it from the flags passed in:

Run one case against the real host with hook events on, and store the PreToolUse
envelope the host handed its hooks:

```
claude -p 'Run this exact shell command with the Bash tool, verbatim, and then stop.

echo hyperreal-envelope-capture' \
  --output-format stream-json --verbose --include-hook-events \
  --tools Bash --allowedTools Bash --permission-mode acceptEdits
```

The envelope is the JSON handed to the hook on stdin, visible in the
`system`/`hook_response` events. Store it here verbatim, including any field this
repo has not thought about — `envelope_for()` templates exactly five fields
(`session_id`, `cwd`, `tool_input`, `tool_use_id`, `transcript_path`) and carries
everything else through, and `tests/test_shim_host.py` asserts both halves of that.

### What it does not settle

Which hook wins when two answer. That is the real host's business, it is
unmeasured, and every shim row therefore carries
`combination_rule: "first_deny_wins (shim assumption, unverified)"` until the
bridge arm (spec N2.3, `measurements/live_shim_bridge.py`) measures it.
