# Captured host artefacts

Things recorded from the **real** Claude Code host, so the shim host
(`measurements/live_shim_host.py`) reproduces them instead of assuming them.

## `host_pretooluse_envelope.json` — NOT YET CAPTURED

**Status 2026-09-26: absent. Spec N8 gate 3 is OPEN, and the shim refuses to run
any arm until this file exists.**

`load_host_envelope()` raises `EnvelopeMissing` rather than falling back to a
handwritten envelope. That is deliberate: `measurements/envelope_ablation.py`
already measured that three envelope shapes gave the same verdict on this gate, so
a handwritten one is *probably* harmless — and "probably harmless" is exactly the
class of assumption that gets published as a gate's behaviour. A refusal makes the
gate something the runner enforces rather than something a document promises.

It is absent because capturing it means launching a real `claude -p` session
(~2¢), and the brief that built the shim said **build and test only, launch
nothing**. One session, not an arm, is all it needs.

### How to capture it

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
