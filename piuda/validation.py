from __future__ import annotations

import math
from typing import Any


def object_value(value: Any, *, message: str = "JSON 객체가 필요합니다.") -> dict:
    if not isinstance(value, dict):
        raise ValueError(message)
    return value


def text_value(
    value: Any,
    name: str,
    *,
    required: bool = True,
    allow_none: bool = False,
    max_length: int = 500,
    strip: bool = True,
) -> str | None:
    if value is None:
        if allow_none:
            return None
        if required:
            raise ValueError(f"{name}을(를) 입력하세요.")
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{name}은(는) 문자열이어야 합니다.")
    result = value.strip() if strip else value
    if required and not result:
        raise ValueError(f"{name}을(를) 입력하세요.")
    if len(result) > max_length:
        raise ValueError(f"{name}은(는) {max_length}자 이하여야 합니다.")
    return result


def integer_value(
    value: Any,
    name: str,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
    allow_none: bool = False,
) -> int | None:
    if value is None and allow_none:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name}은(는) 정수여야 합니다.")
    if minimum is not None and value < minimum or maximum is not None and value > maximum:
        bounds = f"{minimum}~{maximum}" if minimum is not None and maximum is not None else "허용된"
        raise ValueError(f"{name}은(는) {bounds} 범위여야 합니다.")
    return value


def number_value(
    value: Any,
    name: str,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
    allow_none: bool = False,
) -> float | None:
    if value is None and allow_none:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name}은(는) 숫자여야 합니다.")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name}은(는) 유한한 숫자여야 합니다.")
    if minimum is not None and result < minimum or maximum is not None and result > maximum:
        bounds = f"{minimum:g}~{maximum:g}" if minimum is not None and maximum is not None else "허용된"
        raise ValueError(f"{name}은(는) {bounds} 범위여야 합니다.")
    return result


def boolean_value(value: Any, name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and not isinstance(value, bool) and value in {0, 1}:
        return bool(value)
    raise ValueError(f"{name}은(는) true/false 또는 0/1이어야 합니다.")
