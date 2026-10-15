"""Small statistics helpers. Unknown (None / non-finite) values are excluded, never 0."""

import math
from statistics import mean, median


def is_known(value):
    return type(value) in (int, float) and math.isfinite(value)


def known(values):
    return [v for v in values if is_known(v)]


def med(values):
    v = known(values)
    return median(v) if v else None


def avg(values):
    v = known(values)
    return mean(v) if v else None


def ranks(values):
    """1-based average ranks; tied values share the mean of their positions."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    result = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            result[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return result


def spearman(xs, ys):
    """(rho, n) over pairs where both values are known; rho is None if n<3 or a side is constant."""
    pairs = [(x, y) for x, y in zip(xs, ys, strict=True) if is_known(x) and is_known(y)]
    n = len(pairs)
    if n < 3:
        return None, n
    rx, ry = ranks([p[0] for p in pairs]), ranks([p[1] for p in pairs])
    mx, my = sum(rx) / n, sum(ry) / n
    sxx = sum((a - mx) ** 2 for a in rx)
    syy = sum((b - my) ** 2 for b in ry)
    if sxx == 0 or syy == 0:
        return None, n
    sxy = sum((a - mx) * (b - my) for a, b in zip(rx, ry, strict=True))
    return sxy / math.sqrt(sxx * syy), n
