"""GF(256), polynomial basis 0x11D, alpha=2. Polynomials use explicit order."""

PRIMITIVE = 0x11D
EXP = [0] * 510
LOG = [0] * 256
_x = 1
for _i in range(255):
    EXP[_i] = _x
    LOG[_x] = _i
    _x <<= 1
    if _x & 256:
        _x ^= PRIMITIVE
EXP[255:] = EXP[:255]


def gf_mul(a: int, b: int) -> int:
    return EXP[LOG[a] + LOG[b]] if a and b else 0


def gf_inv(a: int) -> int:
    """Arithmetic convention inv(0)=0; division still rejects zero."""
    return EXP[255 - LOG[a]] if a else 0


def gf_div(a: int, b: int) -> int:
    if not b:
        raise ZeroDivisionError("GF division by zero")
    return EXP[(LOG[a] - LOG[b]) % 255] if a else 0


def poly_eval_high(poly, x):
    result = 0
    for coefficient in poly:
        result = gf_mul(result, x) ^ coefficient
    return result


def poly_eval_low(poly, x):
    return poly_eval_high(reversed(poly), x)


def poly_mul(a, b):
    result = [0] * (len(a) + len(b) - 1)
    for i, x in enumerate(a):
        for j, y in enumerate(b):
            result[i + j] ^= gf_mul(x, y)
    return result


def solve(matrix, rhs):
    """Independent Gaussian elimination in GF(256), raises on singular input."""
    n = len(rhs)
    if len(matrix) != n or any(len(row) != n for row in matrix):
        raise ValueError("square system required")
    a = [list(row) + [value] for row, value in zip(matrix, rhs)]
    for col in range(n):
        pivot = next((i for i in range(col, n) if a[i][col]), None)
        if pivot is None:
            raise ValueError("singular GF system")
        a[col], a[pivot] = a[pivot], a[col]
        inv = gf_inv(a[col][col])
        a[col] = [gf_mul(v, inv) for v in a[col]]
        for row in range(n):
            if row != col and a[row][col]:
                factor = a[row][col]
                a[row] = [x ^ gf_mul(factor, y) for x, y in zip(a[row], a[col])]
    return [row[-1] for row in a]
