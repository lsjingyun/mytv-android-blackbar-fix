#!/usr/bin/env python3
"""Patch mytv-android V3.0.0.235 so the player letterbox area is pure black.

Pipeline: apktool decode -> replace the ColorScheme background read with
Color(0xFF000000) -> apktool build -> zipalign/sign.

Usage:
  python3 patch.py orig.apk -o patched.apk \
      [--apktool apktool.jar] [--signer uber-apk-signer.jar] [--keep]

Requires JDK 17 on PATH (or JAVA_HOME set).
"""

import argparse
import os
import re
import shutil
import subprocess
import sys

# Compose Modifier.background(Modifier, long, Shape) after R8 obfuscation.
BACKGROUND_SIG = "Lfn3;->q(Ld15;JLme7;)Ld15;"
# ColorScheme.background field read (the color we want to override).
THEME_BG = re.compile(r"^(\s*)iget-wide\s+(v\d+),\s*(v\d+),\s*Lty0;->E:J\s*$")
PURE_BLACK = "    const-wide {reg}, 0xff00000000000000L"


def find_target(lines):
    """Return indices of theme-background reads that feed a background() call."""
    hits = []
    for i, line in enumerate(lines):
        m = THEME_BG.match(line.rstrip("\n"))
        if not m:
            continue
        window = lines[i : i + 8]
        if any(BACKGROUND_SIG in w for w in window):
            hits.append((i, m.group(2)))
    return hits


def run(cmd, **kw):
    print("+", " ".join(cmd))
    subprocess.run(cmd, check=True, **kw)


def _block_bounds(lines, label):
    start = next(i for i, l in enumerate(lines) if l.strip() == label)
    end = start + 1
    while end < len(lines):
        s = lines[end].strip()
        if s.startswith("goto "):
            end += 1
            break
        if s.startswith(":") and end > start + 1:
            break
        end += 1
    return start, end


def _normalise_branch(lines, label):
    """Make one display-mode branch scale uniformly (cover).

    Two separate mistakes live in these branches in the stock build:
      * CROP used Lsj7;->e, which is fillMaxWidth (Direction.Horizontal),
        not fillMaxHeight - so it only ever shrank the picture.
      * matchHeightConstraintsFirst was false, so aspectRatio measured
        width-first and returned 1080x607 on a 1080x720 screen.

    v5 is the flag register used by this branch in 235; v6 holds
    VideoPlayerState (Leu8).
    """
    a, b = _block_bounds(lines, label)
    changed = 0
    for i in range(a, b):
        l = lines[i]
        if "Lsj7;->c(Ld15;F)Ld15;" in l or "Lsj7;->e(Ld15;F)Ld15;" in l:
            lines[i] = l.replace("Lsj7;->c(", "Lsj7;->b(").replace(
                "Lsj7;->e(", "Lsj7;->b("
            )
            changed += 1
        elif re.match(r"\s+const/4 v\d+, 0x0\s*$", l):
            lines[i] = re.sub(r"0x0\s*$", "0x1", l)
            changed += 1

    if not any("Lva0;->R(Ld15;FZ)Ld15;" in x for x in lines[a:b]):
        mr = next(
            (i for i in range(a, b) if lines[i].strip().startswith("move-result-object")),
            None,
        )
        if mr is None:
            return changed
        res = lines[mr].strip().split()[-1]
        lines[mr + 1 : mr + 1] = [
            "\n",
            "    iget-object v2, v6, Leu8;->e:Llm7;\n",
            "\n",
            "    invoke-virtual {v2}, Llm7;->r()F\n",
            "\n",
            "    move-result v2\n",
            "\n",
            "    invoke-static {%s, v2, v5}, Lva0;->R(Ld15;FZ)Ld15;\n" % res,
            "\n",
            "    move-result-object %s\n" % res,
        ]
        changed += 1
    return changed


def patch_fill_mode(decoded, work):
    """FILL: fillMaxSize (stretch) -> fillMaxHeight + aspectRatio (cover).
    CROP: fillMaxWidth -> fillMaxHeight (it used to only shrink)."""
    for root, _, files in os.walk(decoded):
        for name in files:
            if not name.endswith(".smali"):
                continue
            path = os.path.join(root, name)
            src = open(path).read()
            if "Ltt8;" not in src or ":cond_2c" not in src:
                continue

            lines = src.splitlines(True)
            n = _normalise_branch(lines, ":cond_2b") + _normalise_branch(
                lines, ":cond_2c"
            )
            out = "".join(lines)
            if out != src:
                open(path, "w").write(out)
                print(
                    "patched FILL/CROP mode in %s (%d edits)"
                    % (os.path.relpath(path, work), n)
                )
                return
    sys.exit("FILL display-mode branch not found - different version?")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("apk")
    ap.add_argument("-o", "--out", default="patched.apk")
    ap.add_argument("--apktool", default=os.environ.get("APKTOOL", "apktool.jar"))
    ap.add_argument("--signer", default=os.environ.get("SIGNER", "uber-apk-signer.jar"))
    ap.add_argument("--keep", action="store_true", help="keep the decoded tree")
    args = ap.parse_args()

    java = shutil.which("java")
    if not java:
        sys.exit("java not found; install JDK 17 or set JAVA_HOME")

    work = os.path.abspath("build")
    decoded = os.path.join(work, "decoded")
    os.makedirs(work, exist_ok=True)

    # 1. decode (skip resources, we only touch smali)
    run([java, "-jar", args.apktool, "d", "-f", "-o", decoded, args.apk])

    # 2. locate and patch
    target = os.path.join(decoded, "smali", "e47.smali")
    if not os.path.exists(target):
        # class order may shift between builds; search every dex folder
        for root, _, files in os.walk(decoded):
            for f in files:
                if not f.endswith(".smali"):
                    continue
                p = os.path.join(root, f)
                with open(p) as fh:
                    if find_target(fh.readlines()):
                        target = p
                        break
    if not os.path.exists(target):
        sys.exit("player composable not found - different version?")

    with open(target) as fh:
        lines = fh.readlines()
    hits = [(i, reg) for i, reg in find_target(lines)]
    if len(hits) != 1:
        sys.exit(
            "expected exactly one theme-background read feeding background(), got %d"
            % len(hits)
        )

    idx, reg = hits[0]
    print("before: %s" % lines[idx].rstrip())
    lines[idx] = PURE_BLACK.format(reg=reg) + "\n"
    print("after : %s" % lines[idx].rstrip())
    with open(target, "w") as fh:
        fh.writelines(lines)
    print("patched %s:%d" % (os.path.relpath(target, work), idx + 1))

    # 2b. FILL display mode: fillMaxSize -> fillMaxHeight + aspectRatio(video)
    patch_fill_mode(decoded, work)

    # 3. rebuild
    unsigned = os.path.join(work, "unsigned.apk")
    run([java, "-jar", args.apktool, "b", decoded, "-o", unsigned])

    # 4. zipalign + sign (v1/v2/v3)
    run([java, "-jar", args.signer, "-a", unsigned, "--allowResign", "--overwrite"])

    signed = unsigned[: -len(".apk")] + "-aligned-debugSigned.apk"
    if not os.path.exists(signed):
        sys.exit("signer output not found: %s" % signed)
    shutil.copyfile(signed, args.out)
    print("\noutput: %s" % os.path.abspath(args.out))
    if not args.keep:
        shutil.rmtree(os.path.join(work, "decoded"), ignore_errors=True)


if __name__ == "__main__":
    main()
