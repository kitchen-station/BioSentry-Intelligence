"""The float32 step that comes before the forward pass (INTEGRATION.md section 4.6, ruling 9).

Features are computed in double precision, then rounded to float32 once, as research does and as the ONNX
graph takes them. Kept apart from plain_mlp.py so that file stays a byte-identical copy of the research file.
"""

import math
import struct


def to_float32(x: float) -> float:
    """x rounded to the nearest float32, as a float. A finite value beyond the float32 range raises
    OverflowError (CPython 3.11 packs it as infinity without raising, so this checks it): the caller treats
    that bin as not computed. NaN and infinity pass through; the forward pass refuses them."""
    y = struct.unpack("f", struct.pack("f", x))[0]
    if math.isfinite(x) and not math.isfinite(y):
        raise OverflowError("value is beyond the float32 range")
    return y
