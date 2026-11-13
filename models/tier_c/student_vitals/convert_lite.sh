#!/bin/sh
# Hand-run MindSpore Lite check of the exported MindIR files. Research Python starts no external
# program (no subprocess, no shell; it does use Python's own worker processes for feature extraction), so
# this is a separate step. It never downloads anything and uses no network.
#
#   sh convert_lite.sh <unpacked MindSpore Lite package dir> <model dir> <rows dir> | tee lite.txt
#
# <rows dir> is what `train_student --lite_rows <dir>` wrote. For every *.mindir in <model dir> it runs
# converter_lite (the .ms lands next to the .mindir), then the Lite benchmark with one thread on each
# of the 20 rows. The benchmark's bias is relative (|diff| / p), so a row passes when its bias is under
# 100 x 1e-5 / p percent, that is an absolute difference of at most 1e-5.
set -eu

[ "$#" -eq 3 ] || { echo "usage: sh convert_lite.sh <lite package dir> <model dir> <rows dir>" >&2; exit 2; }
pkg=$1
models=$2
rows=$3
conv="$pkg/tools/converter/converter/converter_lite"
bench="$pkg/tools/benchmark/benchmark"
[ -x "$conv" ] && [ -x "$bench" ] || { echo "no converter_lite and benchmark under the package dir" >&2; exit 2; }
[ -d "$models" ] && [ -d "$rows" ] || { echo "model dir or rows dir is not a folder" >&2; exit 2; }
LD_LIBRARY_PATH="$pkg/runtime/lib:$pkg/runtime/third_party/glog:$pkg/tools/converter/lib"
export LD_LIBRARY_PATH

hash256() {
    if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | cut -d' ' -f1; else shasum -a 256 "$1" | cut -d' ' -f1; fi
}

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
echo "MindSpore Lite check of the exported MindIR files (made by convert_lite.sh; nothing is downloaded)."
echo "For each model: converter_lite turns the .mindir into a .ms file; then the Lite benchmark (1 thread)"
echo "runs 20 fixed training rows and each output must equal MindSpore's p to within 1e-5 (absolute)."
status=0
found=0
for mindir in "$models"/*.mindir; do
    [ -e "$mindir" ] || continue
    found=1
    stem=$(basename "$mindir" .mindir)
    echo "== $stem"
    echo "converter command: <lite package>/tools/converter/converter/converter_lite --fmk=MINDIR --modelFile=$stem.mindir --outputFile=$stem"
    if ! log=$("$conv" --fmk=MINDIR --modelFile="$mindir" --outputFile="$models/$stem" 2>&1); then
        echo "converter failed: $(printf '%s\n' "$log" | tail -n 2)"
        status=1
        continue
    fi
    printf '%s\n' "$log" | grep 'CONVERT RESULT' | tail -n 1 || true
    ms="$models/$stem.ms"
    echo "$stem.ms: $(wc -c <"$ms" | tr -d ' ') bytes, sha256 $(hash256 "$ms")"
    # the output tensor's name is the last Sigmoid node in the converted model
    out=$(grep -a -o 'Default/[A-Za-z0-9_./-]*Sigmoid[A-Za-z0-9_./-]*' "$ms" | tail -n 1 || true)
    if [ -z "$out" ]; then echo "no Sigmoid output tensor found in $stem.ms"; status=1; continue; fi
    ok=0
    total=20
    i=0
    while [ "$i" -lt "$total" ]; do
        bin="$rows/$stem/row$i.bin"
        txt="$rows/$stem/row$i.txt"
        i=$((i + 1))
        if [ ! -f "$bin" ] || [ ! -f "$txt" ]; then
            echo "row $((i - 1)): input or calibration file is missing: counted as failed"
            continue
        fi
        p=$(sed -n 2p "$txt")
        thr=$(awk -v p="$p" 'BEGIN { if (p < 0.00001) p = 0.00001; printf "%.9g", 100 * 0.00001 / p }')
        sed "s|@OUTPUT@|$out|" "$txt" >"$tmp/cal.txt"
        if "$bench" --modelFile="$ms" --inDataFile="$bin" --benchmarkDataFile="$tmp/cal.txt" \
            --numThreads=1 --loopCount=100 --warmUpLoopCount=10 --accuracyThreshold="$thr" >"$tmp/bench.txt" 2>&1; then
            ok=$((ok + 1))
        else
            echo "row $((i - 1)): the benchmark rejected it (relative bias over $thr percent)"
        fi
    done
    echo "rows within 1e-5: $ok of $total"
    if [ "$ok" -ne "$total" ]; then status=1; fi
    # a separate call without a calibration file, for the run time
    avg=
    if [ -f "$rows/$stem/row0.bin" ]; then
        "$bench" --modelFile="$ms" --inDataFile="$rows/$stem/row0.bin" --numThreads=1 --loopCount=100 \
            --warmUpLoopCount=10 >"$tmp/time.txt" 2>&1 || true
        avg=$(grep -o 'AvgRunTime = [0-9.]* ms' "$tmp/time.txt" | tail -n 1 || true)
    fi
    if [ -n "$avg" ]; then echo "average run time: ${avg#AvgRunTime = } (Lite benchmark, 1 thread, row 0, no calibration file)"
    else echo "average run time: not printed by the tool"; fi
done
[ "$found" -eq 1 ] || { echo "no .mindir file in the model dir" >&2; exit 1; }
exit "$status"
