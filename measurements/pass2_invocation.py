"""Measurement pass 2.

Three questions this pass has to answer before anything is built:

1. What does hookify actually put on stdout when no rules are configured?
   (SILENT and "installed but unconfigured" must not look alike.)
2. Can an ecc PreToolUse gate be invoked the way ecc's own hooks.json says it
   is invoked -- i.e. argv taken from the plugin config via shlex, never
   transcribed by hand?
3. Does ${CLAUDE_PLUGIN_ROOT} expansion + cwd matter to whether it answers?
"""
import json
import os
import shlex
import sys

HOME = os.path.expanduser("~")
sys.path.insert(0, os.path.join(HOME, "hyperreal"))

from hyperreal.adapters.subprocess_gate import SubprocessGate  # noqa: E402

MP = os.path.join(HOME, ".claude/plugins/marketplaces")
OFFICIAL = os.path.join(MP, "claude-plugins-official/plugins")
ECC_ROOT = os.path.join(MP, "ecc")


def hook_input(tool_name, tool_input):
    return {
        "session_id": "hyperreal-probe",
        "transcript_path": "/dev/null",
        "cwd": os.getcwd(),
        "hook_event_name": "PreToolUse",
        "tool_name": tool_name,
        "tool_input": tool_input,
    }


def q1():
    print("== Q1: hookify raw channels, unconfigured ==")
    root = os.path.join(OFFICIAL, "hookify")
    gate = SubprocessGate(
        name="hookify",
        argv=["python3", os.path.join(root, "hooks/pretooluse.py")],
        extra_env={"CLAUDE_PLUGIN_ROOT": root},
    )
    run = gate.run(hook_input("Bash", {"command": "rm -rf /tmp/x"}))
    a = run.answer
    print(f"  verdict={a.verdict.value} channel={a.channel.value} exit={a.exit_code}")
    print(f"  raw_stdout={a.raw_stdout!r}")
    print(f"  raw_stderr={a.raw_stderr!r}")
    # Does it behave differently when CLAUDE_PLUGIN_ROOT is absent (import fails)?
    bare = SubprocessGate(name="hookify-bare", argv=["python3", os.path.join(root, "hooks/pretooluse.py")])
    b = bare.run(hook_input("Bash", {"command": "rm -rf /tmp/x"})).answer
    print(f"  no-PLUGIN_ROOT: verdict={b.verdict.value} stdout={b.raw_stdout!r}")


def ecc_pretooluse_entries():
    data = json.load(open(os.path.join(ECC_ROOT, "hooks/hooks.json"), encoding="utf-8"))
    out = []
    for group in data.get("hooks", {}).get("PreToolUse", []):
        matcher = group.get("matcher", "*")
        for h in group.get("hooks", []):
            out.append((matcher, h.get("command", "")))
    return out


def expand(cmd):
    return cmd.replace("${CLAUDE_PLUGIN_ROOT}", ECC_ROOT)


def q2():
    print("\n== Q2/Q3: ecc gates invoked from ecc's own hooks.json ==")
    for matcher, cmd in ecc_pretooluse_entries():
        argv = shlex.split(expand(cmd))
        label = argv[-3] if len(argv) >= 3 else argv[-1]
        gate = SubprocessGate(
            name=f"ecc:{label}",
            argv=argv,
            cwd=ECC_ROOT,
            extra_env={"CLAUDE_PLUGIN_ROOT": ECC_ROOT},
        )
        run = gate.run(hook_input("Write", {"file_path": "/tmp/../etc/hosts", "content": "x"}))
        a = run.answer
        print(
            f"  matcher={matcher!r:<28} argc={len(argv)} -> {a.verdict.value:<7} "
            f"ch={a.channel.value:<12} exit={a.exit_code} {run.wall_seconds*1000:.0f}ms  [{label}]"
        )
        if a.raw_stdout.strip() or a.raw_stderr.strip():
            print(f"      stdout={a.raw_stdout[:220]!r}")
            print(f"      stderr={a.raw_stderr[:220]!r}")


def main():
    q1()
    q2()
    return 0


if __name__ == "__main__":
    sys.exit(main())
