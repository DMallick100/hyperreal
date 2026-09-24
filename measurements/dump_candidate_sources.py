"""Print the source of the candidate gates, for reading from outside the repo."""
import os
import sys

HOME = os.path.expanduser("~")
MP = os.path.join(HOME, ".claude/plugins/marketplaces/claude-plugins-official/plugins")

PATHS = [
    os.path.join(MP, "hookify/hooks/hooks.json"),
    os.path.join(MP, "hookify/hooks/pretooluse.py"),
    os.path.join(MP, "plugin-dev/skills/hook-development/examples/validate-write.sh"),
    os.path.join(MP, "security-guidance/hooks/hooks.json"),
]


def main():
    for p in PATHS:
        print("=" * 72)
        print(p.replace(HOME, "~"))
        print("=" * 72)
        try:
            sys.stdout.write(open(p, encoding="utf-8", errors="replace").read())
        except OSError as exc:
            print(f"!! {exc}")
        print()
    # list the security-guidance plugin dir so we can find its real entrypoint
    sg = os.path.join(MP, "security-guidance")
    print("=" * 72)
    print("security-guidance tree")
    print("=" * 72)
    for dirpath, dirnames, filenames in os.walk(sg):
        dirnames[:] = [d for d in dirnames if d not in {"__pycache__", "node_modules"}]
        for fn in sorted(filenames):
            fp = os.path.join(dirpath, fn)
            print(f"{os.path.getsize(fp):>9}  {fp.replace(sg, 'security-guidance')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
