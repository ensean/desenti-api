"""强格式实体的校验函数，用于提升/降低置信度。"""

from __future__ import annotations

# 统一社会信用代码校验
_CC_CHARS = "0123456789ABCDEFGHJKLMNPQRTUWXY"
_CC_WEIGHTS = [1, 3, 9, 27, 19, 26, 16, 17, 20, 29, 25, 13, 8, 24, 10, 30, 28]
_CC_INDEX = {c: i for i, c in enumerate(_CC_CHARS)}


def validate_credit_code(code: str) -> bool:
    """校验 18 位统一社会信用代码的最后一位校验码。"""
    if len(code) != 18:
        return False
    try:
        total = sum(_CC_INDEX[code[i]] * _CC_WEIGHTS[i] for i in range(17))
    except KeyError:
        return False
    check = 31 - (total % 31)
    check = check % 31
    expected = _CC_CHARS[check]
    return expected == code[17]


# 身份证号校验（GB 11643-1999）
_ID_WEIGHTS = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2]
_ID_CHECK = "10X98765432"


def validate_id_card(num: str) -> bool:
    if len(num) != 18:
        return False
    if not num[:17].isdigit():
        return False
    total = sum(int(num[i]) * _ID_WEIGHTS[i] for i in range(17))
    expected = _ID_CHECK[total % 11]
    return expected == num[17].upper()
