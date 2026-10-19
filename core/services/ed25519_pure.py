"""纯 Python Ed25519 实现（RFC 8032）— 实验代码，尚未接入产品签名流程。

现状：仅 tests/unit/test_ed25519_vectors.py 引用；signature_service 目前只做
manifest 结构校验与可选 SHA-256 checksums，不会调用本实现。

为什么保留：
本项目为了压体积移除了 cryptography（连带 libcrypto/libssl 约 19MB 死重），
若后续接入 Ed25519 签名，纯 Python 整数运算可以保持运行时零密码学依赖。

安全说明：
- 坐标为扩展扭曲 Edwards 坐标（X:Y:Z:T），避免仿射加法的逐次求逆
- point_decompress 会校验解码点是否在曲线上（y²-1 = d·x²·z²）
- verify 要求标量 s < q 且 R 解码成功；调用方应额外做子群检查
  （本实现的 verify 对 s 严格小于 q 做了校验，这是 RFC 8032 的要求）
"""

from __future__ import annotations

import hashlib
from typing import Tuple

# 域参数
p = 2 ** 255 - 19
q = 2 ** 252 + 27742317777372353535851937790883648493
d = -121665 * pow(121666, p - 2, p) % p
I = pow(2, (p - 1) // 4, p)  # sqrt(-1)

Point = Tuple[int, int, int, int]  # (X, Y, Z, T)
IDENTITY: Point = (0, 1, 1, 0)

G: Point = (
    # X
    15112221349535400772501151409588531511454012693041857206046113283949847762202,
    # Y
    46316835694926478169428394003475163141307993866256225615783033603165251855960,
    # Z（仿射点，取 1）
    1,
    # T = X·Y mod p —— 扩展坐标的第四个分量，点加公式里 C = 2·T1·T2·d 直接用到它。
    # 曾经这里误抄成 Y 的值：因为压缩编码只看 x/y，compress(G) 依然正确，
    # 但一旦做点加/点倍就整体算错（表现为 2G 起全部偏离）。
    46827403850823179245072216630277197565144205554125654976674165829533817101731,
)


def _sha512_modq(data: bytes) -> int:
    return int.from_bytes(hashlib.sha512(data).digest(), "little") % q


def point_add(P: Point, Q: Point) -> Point:
    """扩展坐标下的点加（a = -1 的 twisted Edwards 曲线）"""
    A = (P[1] - P[0]) * (Q[1] - Q[0]) % p
    B = (P[1] + P[0]) * (Q[1] + Q[0]) % p
    C = 2 * P[3] * Q[3] * d % p
    D = 2 * P[2] * Q[2] % p
    E, F, G_, H = B - A, D - C, D + C, B + A
    return (E * F % p, G_ * H % p, F * G_ % p, E * H % p)


def point_double(P: Point) -> Point:
    return point_add(P, P)


def point_mul(s: int, P: Point) -> Point:
    """标量乘（double-and-add）"""
    Q = IDENTITY
    while s > 0:
        if s & 1:
            Q = point_add(Q, P)
        P = point_double(P)
        s >>= 1
    return Q


def point_equal(P: Point, Q: Point) -> bool:
    """比较两点是否相等（比较投影坐标 X1*Z2 == X2*Z1, Y1*Z2 == Y2*Z1）"""
    if (P[0] * Q[2] - Q[0] * P[2]) % p != 0:
        return False
    return (P[1] * Q[2] - Q[1] * P[2]) % p == 0


def _recover_x(y: int, sign: int) -> int | None:
    """由 y 坐标恢复 x，sign 为 x 的符号位；无解返回 None"""
    if y >= p:
        return None
    y2 = y * y % p
    u = (y2 - 1) % p
    v = (d * y2 + 1) % p

    # x = (u/v)^((p+3)/8) 计算后再校验
    vinv = pow(v, p - 2, p)
    x = (u * vinv) % p
    x = pow(x, (p + 3) // 8, p)

    if (v * x * x - u) % p != 0:
        x = x * I % p
    if (v * x * x - u) % p != 0:
        return None  # 无平方根 -> 点不在曲线上

    if x == 0 and sign:
        return None  # x=0 时符号位必须为 0

    if x & 1 != sign:
        x = (p - x) % p
    return x


def point_decompress(s: bytes) -> Point | None:
    """解码 32 字节压缩点；非法编码返回 None"""
    if len(s) != 32:
        return None
    y = int.from_bytes(s, "little")
    sign = y >> 255
    y &= (1 << 255) - 1

    x = _recover_x(y, sign)
    if x is None:
        return None
    return (x, y, 1, x * y % p)


def _secret_expand(secret: bytes) -> Tuple[int, bytes]:
    """RFC 8032 §5.1.5：私钥扩展成 (标量, 前缀)

    标准做法是逐字节操作：
        h[0]  &= 248   # 清掉低 3 位
        h[31] &= 127   # 清掉最高位
        h[31] |= 64    # 置 bit 254
    等价地写成位运算：a &= (1 << 255) - 8; a |= (1 << 254)
    注意掩码必须是 (1<<255)-8（保留 bit 3..254），
    写成 (1<<254)-8 会清掉 bit 254 并保留低 3 位，导致标量完全错误。
    """
    if len(secret) != 32:
        raise ValueError("Ed25519 private key must be 32 bytes")
    h = hashlib.sha512(secret).digest()
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 255) - 8   # 清低 3 位，保留 bit 3..254
    a |= (1 << 254)        # 置 bit 254
    return a, h[32:]


def _compress(point: Point) -> bytes:
    """把点编码为 32 字节压缩格式。

    低 255 位是 y 坐标，最高位（bit 255）携带 x 的奇偶性（符号位）。

    ⚠️ 编码前必须先把投影坐标归一化（x = X/Z, y = Y/Z）。
    扩展坐标是齐次表示，(4X, 4Y, 4, 4T) 与 (X, Y, 1, T) 是同一个点；
    直接取 X/Y 编码，只有 Z=1 的点（如基点本身）才碰巧正确，
    而标量乘的结果几乎都是 Z≠1，会导致公钥与签名全错。
    """
    zinv = pow(point[2], p - 2, p)
    x = point[0] * zinv % p
    y = point[1] * zinv % p
    return (y | ((x & 1) << 255)).to_bytes(32, "little")


def publickey(secret: bytes) -> bytes:
    """由 32 字节私钥导出 32 字节公钥"""
    a, _ = _secret_expand(secret)
    return _compress(point_mul(a, G))


def sign(secret: bytes, message: bytes) -> bytes:
    """生成 64 字节 Ed25519 签名"""
    a, prefix = _secret_expand(secret)
    pub = publickey(secret)
    r = _sha512_modq(prefix + message)
    R = point_mul(r, G)
    Rs = _compress(R)
    h = _sha512_modq(Rs + pub + message)
    s = (r + h * a) % q
    return Rs + s.to_bytes(32, "little")


def verify(public: bytes, message: bytes, signature: bytes) -> bool:
    """验证 Ed25519 签名，任何非法输入一律返回 False（fail-closed）"""
    try:
        if len(public) != 32 or len(signature) != 64:
            return False
        A = point_decompress(public)
        if A is None:
            return False
        Rs = signature[:32]
        R = point_decompress(Rs)
        if R is None:
            return False
        s = int.from_bytes(signature[32:], "little")
        if s >= q:
            # 非规范化标量（小序子群攻击的常见入口）
            return False
        h = _sha512_modq(Rs + public + message)
        # 验证 [s]B == R + [h]A
        lhs = point_mul(s, G)
        rhs = point_add(R, point_mul(h, A))
        return point_equal(lhs, rhs)
    except Exception:
        return False