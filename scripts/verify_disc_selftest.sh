#!/usr/bin/env bash
set -uo pipefail

# Exercise every failure branch of verify_disc.sh.
#
# Usage: verify_disc_selftest.sh <game_dir>
#
# <game_dir> must be a repo with a VERIFYING disc copy and a recomp/disc.manifest; this
# builds deliberately-broken copies from it and checks that each is diagnosed correctly.
#
# The broken copies are symlink farms in a temp dir, so nothing is copied and the real
# disc is only ever READ. Two of these branches exist because this suite found them:
# stat sizing a link instead of its target, and `set -e` swallowing the exit code.

E="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
V="$E/scripts/verify_disc.sh"
REAL="${1:-}"
[[ -n "$REAL" && -d "$REAL" ]] || { echo "Usage: verify_disc_selftest.sh <game_dir>" >&2; exit 64; }
REAL="$(cd "$REAL" && pwd)"
[[ -f "$REAL/recomp/disc.manifest" ]] || { echo "No recomp/disc.manifest in $REAL" >&2; exit 64; }

ELF_REL="$(awk '$1=="elf"{print $4; exit}' "$REAL/recomp/disc.manifest")"
ELF_NAME="$(basename "$ELF_REL")"
SERIAL="$(awk '$1=="serial"{print $2; exit}' "$REAL/recomp/disc.manifest")"
# a small file to damage, and a big one to remove — taken from the manifest, not hardcoded
SMALL="$(awk '$1=="file"{print $2, $4}' "$REAL/recomp/disc.manifest" | sort -n | awk 'NR==2{print $2}')"
SMALL_SIZE="$(awk -v p="$SMALL" '$1=="file" && $4==p {print $2; exit}' "$REAL/recomp/disc.manifest")"
BIG="$(awk '$1=="file"{print $2, $4}' "$REAL/recomp/disc.manifest" | sort -rn | awk 'NR==1{print $2}')"

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
PASS=0; FAILED=0

mkfake() {  # mkfake <name> -> path
    local d="$TMP/$1"; mkdir -p "$d/recomp" "$d/gamefiles"
    cp "$REAL/recomp/disc.manifest" "$d/recomp/"
    printf '[general]\ninput = "%s"\noutput = "tmp/generated"\n' "$ELF_REL" > "$d/recomp/config.toml"
    (cd "$REAL/gamefiles" && find . -mindepth 1 -type d -printf '%P\n') | while read -r r; do mkdir -p "$d/gamefiles/$r"; done
    (cd "$REAL/gamefiles" && find . -type f -printf '%P\n') | while read -r r; do
        case "$r" in .*|*/.*) continue ;; esac
        ln -s "$REAL/gamefiles/$r" "$d/gamefiles/$r"
    done
    echo "$d"
}
expect() {  # expect <want_rc> <label> <dir> [flags...]
    local want="$1" label="$2" dir="$3"; shift 3
    "$V" "$dir" "$@" >/dev/null 2>&1; local got=$?
    if [[ "$got" == "$want" ]]; then printf '  [PASS] rc=%s  %s\n' "$got" "$label"; PASS=$((PASS+1))
    else printf '  [FAIL] rc=%s want=%s  %s\n' "$got" "$want" "$label"; FAILED=$((FAILED+1)); fi
}

echo "verify_disc.sh self-test  (reference copy: $REAL)"

d=$(mkfake good);       expect 0 "a clean copy verifies" "$d"
                        expect 0 "  ... and under --deep" "$d" --deep

d=$(mkfake empty); rm -rf "$d/gamefiles"; mkdir -p "$d/gamefiles"
                        expect 1 "nothing copied" "$d"

d=$(mkfake region); rm -f "$d/gamefiles/SYSTEM.CNF" "$d/gamefiles/$ELF_NAME"
printf 'BOOT2 = cdrom0:\\SLUS_207.70;1\nVMODE = NTSC\n' > "$d/gamefiles/SYSTEM.CNF"
ln -s "$REAL/$ELF_REL" "$d/gamefiles/SLUS_207.70"
                        expect 2 "another region ($SERIAL build, NTSC-U copy)" "$d"

d=$(mkfake noelf); rm -f "$d/gamefiles/$ELF_NAME"
                        expect 3 "data copied, executable not" "$d"

d=$(mkfake revision); rm -f "$d/gamefiles/$ELF_NAME"
cp "$REAL/$ELF_REL" "$d/gamefiles/$ELF_NAME"; chmod u+w "$d/gamefiles/$ELF_NAME"
printf '\xde\xad' | dd of="$d/gamefiles/$ELF_NAME" bs=1 seek=4096 conv=notrunc status=none
                        expect 4 "same serial and size, different bytes" "$d"

d=$(mkfake partial); rm -f "$d/$BIG"
                        expect 3 "a data file missing" "$d"

d=$(mkfake truncated); rm -f "$d/$SMALL"
head -c $(( SMALL_SIZE / 2 )) "$REAL/$SMALL" > "$d/$SMALL"
                        expect 4 "a data file stops short" "$d"

d=$(mkfake nomanifest); rm -f "$d/recomp/disc.manifest"
                        expect 5 "nothing to check against" "$d"

# The tier boundary: right size, wrong bytes is INVISIBLE to the shallow check by design,
# and is the whole reason --deep exists.
d=$(mkfake badread); rm -f "$d/$SMALL"; head -c "$SMALL_SIZE" /dev/zero > "$d/$SMALL"
                        expect 0 "bad read passes the shallow check (size is right)" "$d"
                        expect 4 "  ... and --deep catches it" "$d" --deep

echo "  ---------------------------------------"
printf '  %s passed, %s failed\n' "$PASS" "$FAILED"
exit $(( FAILED > 0 ))
