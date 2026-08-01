"""
Deterministic random substreams.

Every stochastic decision in this package draws from a substream keyed by a
stable string. The substream seed is SHA-256(MASTER_SEED || key), so:

  * regenerating any single instance reproduces it bit-for-bit, independently
    of generation order or of how many other instances exist;
  * adding a size class or a replicate does not perturb instances already
    generated (no shared global generator state);
  * the whole benchmark is reproducible from one integer in config/design.py.

This matters more than it looks: with a single shared `random` instance, adding
one cell to the design silently changes every instance generated after it, and
results computed before and after the change are no longer comparable.
"""

from __future__ import annotations

import hashlib
import random

from config import design


def seed_for(key: str) -> int:
    h = hashlib.sha256(f"{design.MASTER_SEED}|{key}".encode()).digest()
    return int.from_bytes(h[:8], "big")


def substream(key: str) -> random.Random:
    return random.Random(seed_for(key))
