#!/usr/bin/env bash
# Clone the two external SOTA baselines at pinned commits.
#
# Run from the repository root:
#   bash experiments/baselines/bootstrap_vendor.sh
#
# Update the pinned commits when you reproduce the paper to keep the
# experiment fully deterministic across machines.

set -euo pipefail

VENDOR_DIR="experiments/baselines/_vendor"
mkdir -p "$VENDOR_DIR"

# ---- Paper2Poster (Gao et al., NeurIPS 2024) ------------------------------
PAPER2POSTER_REPO="https://github.com/Paper2Poster/Paper2Poster.git"
PAPER2POSTER_COMMIT="REPLACE_WITH_PINNED_COMMIT"
if [[ ! -d "$VENDOR_DIR/Paper2Poster" ]]; then
  echo "[bootstrap] cloning Paper2Poster..."
  git clone "$PAPER2POSTER_REPO" "$VENDOR_DIR/Paper2Poster"
fi
( cd "$VENDOR_DIR/Paper2Poster" && git fetch --all --tags --prune \
  && git checkout "$PAPER2POSTER_COMMIT" )

# ---- PosterAgent (Li et al., 2025) ----------------------------------------
POSTERAGENT_REPO="https://github.com/Paper2Poster/PosterAgent.git"
POSTERAGENT_COMMIT="REPLACE_WITH_PINNED_COMMIT"
if [[ ! -d "$VENDOR_DIR/PosterAgent" ]]; then
  echo "[bootstrap] cloning PosterAgent..."
  git clone "$POSTERAGENT_REPO" "$VENDOR_DIR/PosterAgent"
fi
( cd "$VENDOR_DIR/PosterAgent" && git fetch --all --tags --prune \
  && git checkout "$POSTERAGENT_COMMIT" )

echo "[bootstrap] done. Vendor repos under: $VENDOR_DIR"
echo "Next: pip install their requirements per their READMEs, then"
echo "      run experiments/scripts/run_one_paper.py --baseline paper2poster ..."
