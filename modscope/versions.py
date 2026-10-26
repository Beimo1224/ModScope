"""Version comparison and loader-specific dependency version predicates.

The public entry points are intentionally small:

* :func:`parse_maven_range` for Forge/NeoForge and legacy Maven ranges.
* :func:`parse_fabric_predicate` for fabric.mod.json.
* :func:`parse_quilt_spec` for quilt.mod.json.
* :func:`parse_loose_constraint` for metadata that does not identify a loader.

No third-party dependency is required.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import functools
import re
from typing import Iterable, Sequence


_RELEASE_WORDS = {
    "a", "alpha", "b", "beta", "pre", "prerelease", "rc", "snapshot", "snap",
    "dev", "preview", "test",
}


@dataclass(frozen=True)
class _Token:
    numeric: bool
    value: int | str


def _tokenize(version: str) -> tuple[_Token, ...]:
    text = version.strip()
    if text[:1] in ("v", "V") and len(text) > 1 and text[1].isdigit():
        text = text[1:]
    # Semver build metadata does not participate in precedence.
    if "+" in text:
        text = text.split("+", 1)[0]
    tokens: list[_Token] = []
    for part in re.split(r"[^0-9A-Za-z]+", text):
        if not part:
            continue
        for piece in re.findall(r"\d+|[A-Za-z]+", part):
            if piece.isdigit():
                tokens.append(_Token(True, int(piece)))
            else:
                tokens.append(_Token(False, piece.lower()))
    return tuple(tokens)


@dataclass(frozen=True)
class Version:
    """A forgiving version value used for dependency comparisons."""

    raw: str
    tokens: tuple[_Token, ...] = field(default=(), repr=False, compare=False)

    def __post_init__(self) -> None:
        if not self.tokens:
            object.__setattr__(self, "tokens", _tokenize(self.raw))

    @property
    def normalized(self) -> str:
        return self.raw.strip()

    def __str__(self) -> str:
        return self.normalized

    def __lt__(self, other: "Version") -> bool:
        return _compare_tokens(self.tokens, other.tokens) < 0

    def __le__(self, other: "Version") -> bool:
        return _compare_tokens(self.tokens, other.tokens) <= 0

    def __gt__(self, other: "Version") -> bool:
        return _compare_tokens(self.tokens, other.tokens) > 0

    def __ge__(self, other: "Version") -> bool:
        return _compare_tokens(self.tokens, other.tokens) >= 0


def _remaining_is_zero(tokens: Sequence[_Token]) -> bool:
    return all(t.numeric and int(t.value) == 0 for t in tokens)


def _compare_exhausted(remaining: Sequence[_Token]) -> int:
    """Compare a non-empty suffix with an exhausted version.

    A suffix made only of zero components is equivalent (1.20 == 1.20.0).
    A numeric suffix is newer (1.20.1 > 1.20).  A textual suffix is a
    pre-release/qualifier and is older (1.20-pre1 < 1.20).
    """
    if _remaining_is_zero(remaining):
        return 0
    if any(not t.numeric for t in remaining):
        return -1
    return 1


def _compare_tokens(left: Sequence[_Token], right: Sequence[_Token]) -> int:
    i = 0
    while i < len(left) and i < len(right):
        a, b = left[i], right[i]
        if a.numeric and b.numeric:
            if int(a.value) != int(b.value):
                return -1 if int(a.value) < int(b.value) else 1
        elif not a.numeric and not b.numeric:
            if str(a.value) != str(b.value):
                # Release labels sort above pre-release labels.
                av, bv = str(a.value), str(b.value)
                if av in _RELEASE_WORDS and bv not in _RELEASE_WORDS:
                    return -1
                if bv in _RELEASE_WORDS and av not in _RELEASE_WORDS:
                    return 1
                return -1 if av < bv else 1
        else:
            # A numeric component is older than a textual qualifier at the
            # same position.  This mirrors SemVer's identifier precedence
            # closely enough for Minecraft/loader versions.
            return -1 if a.numeric else 1
        i += 1
    if i == len(left) and i == len(right):
        return 0
    if i == len(left):
        return -_compare_exhausted(right[i:])
    return _compare_exhausted(left[i:])


def compare(left: str | Version, right: str | Version) -> int:
    a = left if isinstance(left, Version) else Version(str(left))
    b = right if isinstance(right, Version) else Version(str(right))
    return _compare_tokens(a.tokens, b.tokens)


def versions_equal(left: str | Version, right: str | Version) -> bool:
    return compare(left, right) == 0


@dataclass(frozen=True)
class Interval:
    low: Version | None = None
    low_inclusive: bool = True
    high: Version | None = None
    high_inclusive: bool = False

    def contains(self, version: Version) -> bool:
        if self.low is not None:
            c = _compare_tokens(version.tokens, self.low.tokens)
            if c < 0 or (c == 0 and not self.low_inclusive):
                return False
        if self.high is not None:
            c = _compare_tokens(version.tokens, self.high.tokens)
            if c > 0 or (c == 0 and not self.high_inclusive):
                return False
        return True

    def intersect(self, other: "Interval") -> "Interval | None":
        low, low_inc = self.low, self.low_inclusive
        if other.low is not None:
            if low is None:
                low, low_inc = other.low, other.low_inclusive
            else:
                c = _compare_tokens(other.low.tokens, low.tokens)
                if c > 0:
                    low, low_inc = other.low, other.low_inclusive
                elif c == 0:
                    low_inc = low_inc and other.low_inclusive
        high, high_inc = self.high, self.high_inclusive
        if other.high is not None:
            if high is None:
                high, high_inc = other.high, other.high_inclusive
            else:
                c = _compare_tokens(other.high.tokens, high.tokens)
                if c < 0:
                    high, high_inc = other.high, other.high_inclusive
                elif c == 0:
                    high_inc = high_inc and other.high_inclusive
        if low is not None and high is not None:
            c = _compare_tokens(low.tokens, high.tokens)
            if c > 0 or (c == 0 and not (low_inc and high_inc)):
                return None
        return Interval(low, low_inc, high, high_inc)


@dataclass
class VersionSpec:
    """A union of half-open/closed version intervals."""

    intervals: list[Interval] = field(default_factory=list)
    original: str = ""
    parse_error: str | None = None

    @classmethod
    def any(cls, original: str = "*") -> "VersionSpec":
        return cls([Interval()], original or "*")

    @classmethod
    def exact(cls, version: str, original: str | None = None) -> "VersionSpec":
        v = Version(version)
        return cls([Interval(v, True, v, True)], original if original is not None else version)

    @classmethod
    def error(cls, message: str, original: str = "") -> "VersionSpec":
        return cls([], original, message)

    def __bool__(self) -> bool:
        return bool(self.intervals) and self.parse_error is None

    @property
    def is_any(self) -> bool:
        return self.intervals == [Interval()] and self.parse_error is None

    def matches(self, version: str | Version | None) -> bool:
        if version is None or not self or self.parse_error:
            return False
        v = version if isinstance(version, Version) else Version(str(version))
        if not v.tokens:
            return False
        return any(interval.contains(v) for interval in self.intervals)

    def intersect(self, other: "VersionSpec") -> "VersionSpec":
        if self.parse_error:
            return self
        if other.parse_error:
            return other
        result: list[Interval] = []
        for left in self.intervals:
            for right in other.intervals:
                merged = left.intersect(right)
                if merged is not None:
                    result.append(merged)
        original = f"{self.original} & {other.original}".strip(" &")
        if not result:
            return VersionSpec([], original, "版本约束没有交集")
        return VersionSpec(result, original)

    def intersect_all(self, specs: Iterable["VersionSpec"]) -> "VersionSpec":
        result = self
        for spec in specs:
            result = result.intersect(spec)
            if result.parse_error:
                break
        return result

    @property
    def exact_version(self) -> str | None:
        if self.parse_error or len(self.intervals) != 1:
            return None
        interval = self.intervals[0]
        if (
            interval.low is not None
            and interval.high is not None
            and interval.low_inclusive
            and interval.high_inclusive
            and _compare_tokens(interval.low.tokens, interval.high.tokens) == 0
        ):
            return interval.low.raw
        return None

    def describe(self) -> str:
        if self.parse_error:
            return self.original or "无法解析"
        if self.is_any:
            return "*"
        chunks: list[str] = []
        for interval in self.intervals:
            if (
                interval.low is not None
                and interval.high is not None
                and _compare_tokens(interval.low.tokens, interval.high.tokens) == 0
                and interval.low_inclusive
                and interval.high_inclusive
            ):
                chunks.append(str(interval.low))
                continue
            left = ("[" if interval.low_inclusive else "(") if interval.low is not None else "(-∞"
            right = ("]" if interval.high_inclusive else ")") if interval.high is not None else "∞)"
            if interval.low is None:
                low_text = "-∞"
            else:
                low_text = str(interval.low)
            if interval.high is None:
                high_text = "∞"
            else:
                high_text = str(interval.high)
            if interval.low is not None and interval.high is not None:
                chunks.append(f"{left}{low_text}, {high_text}{right}")
            elif interval.low is not None:
                chunks.append(f"{left}{low_text}, ∞)")
            else:
                chunks.append(f"(-∞, {high_text}{right}")
        return " ∪ ".join(chunks) if chunks else self.original


def _strip_outer_quotes(text: str) -> str:
    value = text.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1].strip()
    return value


def parse_maven_range(spec: str | None) -> VersionSpec:
    """Parse a Maven version range, including unions such as ``(,1.0],[1.2,)``."""
    raw = _strip_outer_quotes(str(spec or "").strip())
    if not raw or raw in {"*", "any", "ANY"}:
        return VersionSpec.any(raw or "*")

    matches = list(re.finditer(r"([\[(])([^\[\]()]*)([\])])", raw))
    if matches:
        # Everything outside bracket groups must be only commas/whitespace.
        residue_parts: list[str] = []
        cursor = 0
        for match in matches:
            residue_parts.append(raw[cursor:match.start()])
            cursor = match.end()
        residue_parts.append(raw[cursor:])
        residue = "".join(residue_parts)
        if residue.strip(" \t\r\n,"):
            return VersionSpec.error("无法识别的 Maven 版本范围", raw)

        intervals: list[Interval] = []
        for match in matches:
            opening, inner, closing = match.groups()
            pieces = [part.strip() for part in inner.split(",")]
            low_inc = opening == "["
            high_inc = closing == "]"
            if len(pieces) == 1:
                if not pieces[0] or not (low_inc and high_inc):
                    return VersionSpec.error("Maven 单版本范围格式不完整", raw)
                v = Version(pieces[0])
                intervals.append(Interval(v, True, v, True))
            elif len(pieces) == 2:
                low_text, high_text = pieces
                low = Version(low_text) if low_text else None
                high = Version(high_text) if high_text else None
                if low is None and high is None:
                    return VersionSpec.error("Maven 范围为空", raw)
                if low is not None and not low.tokens:
                    return VersionSpec.error("Maven 范围下界无效", raw)
                if high is not None and not high.tokens:
                    return VersionSpec.error("Maven 范围上界无效", raw)
                intervals.append(Interval(low, low_inc if low is not None else True,
                                          high, high_inc if high is not None else False))
            else:
                return VersionSpec.error("Maven 范围包含过多逗号", raw)
        return VersionSpec(_merge_intervals(intervals), raw)

    # A plain value.  Maven calls this a "soft" version; treating it as an
    # exact value is the least surprising and safest interpretation.
    if re.search(r"\s", raw):
        return VersionSpec.error("无法识别的 Maven 版本范围", raw)
    return VersionSpec.exact(raw, raw)


def _split_predicate_tokens(text: str) -> list[str]:
    text = text.strip()
    if not text:
        return []
    # Join an operator separated from its value:  ">= 1.2.3" -> ">=1.2.3"
    text = re.sub(r"(>=|<=|>|<|=|\^|~)\s+", r"\1", text)
    return [token for token in re.split(r"\s+", text) if token]


def _numeric_prefix(version_text: str) -> tuple[list[int], int]:
    """Return numeric components and the number of components that lead."""
    components: list[int] = []
    for part in version_text.split("."):
        m = re.match(r"^(\d+)", part)
        if not m:
            break
        components.append(int(m.group(1)))
    return components, len(components)


def _wildcard_to_interval(token: str) -> Interval | None:
    if "*" not in token.lower() and "x" not in token.lower():
        return None
    lower_parts = token.lower().replace("*", "x").split(".")
    nums: list[int] = []
    for part in lower_parts:
        if part in {"x", ""}:
            break
        if not part.isdigit():
            return None
        nums.append(int(part))
    if not nums:
        return Interval()
    # Move to the first component after the fixed prefix.
    lower_nums = nums + [0] * (3 - len(nums))
    lower = Version(".".join(str(n) for n in lower_nums))
    upper_nums = nums[:-1] + [nums[-1] + 1]
    upper = Version(".".join(str(n) for n in upper_nums))
    return Interval(lower, True, upper, False)


def _tilde_interval(base: str) -> Interval | None:
    nums, _ = _numeric_prefix(base)
    if not nums:
        return None
    lower = Version(base)
    if len(nums) == 1:
        upper_nums = [nums[0] + 1, 0, 0]
    else:
        upper_nums = [*nums[:2], 0]
        if len(nums) >= 2:
            upper_nums = [nums[0], nums[1] + 1, 0]
    upper = Version(".".join(str(n) for n in upper_nums))
    return Interval(lower, True, upper, False)


def _caret_interval(base: str) -> Interval | None:
    nums, _ = _numeric_prefix(base)
    if not nums:
        return None
    lower = Version(base)
    if nums[0] > 0:
        upper_nums = [nums[0] + 1, 0, 0]
    elif len(nums) >= 2 and nums[1] > 0:
        upper_nums = [0, nums[1] + 1, 0]
    elif len(nums) >= 3:
        upper_nums = [0, 0, nums[2] + 1]
    else:
        upper_nums = [0, 1, 0]
    upper = Version(".".join(str(n) for n in upper_nums))
    return Interval(lower, True, upper, False)


def _bare_interval(token: str, *, quilt: bool) -> Interval | None:
    wildcard = _wildcard_to_interval(token)
    if wildcard is not None:
        return wildcard
    if not re.search(r"\d", token):
        return None
    if quilt:
        # Quilt RFC: a bare version is a SemVer-compatible range.
        return _caret_interval(token)
    return Interval(Version(token), True, Version(token), True)


def _parse_comparator(token: str, *, quilt: bool) -> Interval | None:
    match = re.match(r"^(>=|<=|>|<|=|\^|~)?\s*(.+)$", token)
    if not match:
        return None
    operator, base = match.groups()
    base = base.strip()
    if not base:
        return None
    if "*" in base or re.search(r"(?i)(?:^|\.)x(?:\.|$)", base):
        wildcard = _wildcard_to_interval(base)
        if wildcard is None:
            return None
        if operator in (None, "", "="):
            return wildcard
        # Wildcards with relational operators are not a standard predicate.
        return None
    if operator in (None, "", "="):
        return _bare_interval(base, quilt=quilt)
    if operator == ">=":
        return Interval(Version(base), True, None, False)
    if operator == ">":
        return Interval(Version(base), False, None, False)
    if operator == "<=":
        return Interval(None, True, Version(base), True)
    if operator == "<":
        return Interval(None, True, Version(base), False)
    if operator == "~":
        return _tilde_interval(base)
    if operator == "^":
        return _caret_interval(base)
    return None


def _intersect_intervals(intervals: list[Interval]) -> Interval | None:
    if not intervals:
        return None
    result = intervals[0]
    for interval in intervals[1:]:
        merged = result.intersect(interval)
        if merged is None:
            return None
        result = merged
    return result


def _merge_intervals(intervals: list[Interval]) -> list[Interval]:
    """Union overlapping intervals and discard empty/contained ones."""
    cleaned: list[Interval] = []
    for interval in intervals:
        if interval.low is not None and interval.high is not None:
            c = _compare_tokens(interval.low.tokens, interval.high.tokens)
            if c > 0 or (c == 0 and not (interval.low_inclusive and interval.high_inclusive)):
                continue
        cleaned.append(interval)
    if not cleaned:
        return []
    cleaned.sort(key=_interval_sort_key)
    result: list[Interval] = []
    for interval in cleaned:
        if not result:
            result.append(interval)
            continue
        last = result[-1]
        if last.high is None:
            continue
        if interval.low is None:
            # Only possible for the first interval after sorting; be safe.
            result[-1] = Interval(
                interval.low, interval.low_inclusive,
                last.high if last.high is not None else interval.high,
                last.high_inclusive if last.high is not None else interval.high_inclusive,
            )
            continue
        c = _compare_tokens(interval.low.tokens, last.high.tokens)
        touching = c < 0 or (c == 0 and (last.high_inclusive or interval.low_inclusive))
        if not touching:
            result.append(interval)
            continue
        # Extend the last interval's upper bound.
        if interval.high is None:
            result[-1] = Interval(last.low, last.low_inclusive, None, False)
        else:
            hc = _compare_tokens(interval.high.tokens, last.high.tokens)
            if hc > 0:
                result[-1] = Interval(
                    last.low, last.low_inclusive, interval.high, interval.high_inclusive
                )
            elif hc == 0:
                result[-1] = Interval(
                    last.low, last.low_inclusive,
                    last.high, last.high_inclusive or interval.high_inclusive,
                )
    return result


def _interval_sort_key(interval: Interval):
    return functools.cmp_to_key(_compare_interval_bounds)(interval)


def _compare_interval_bounds(left: Interval, right: Interval) -> int:
    if left.low is None and right.low is None:
        return 0
    if left.low is None:
        return -1
    if right.low is None:
        return 1
    c = _compare_tokens(left.low.tokens, right.low.tokens)
    if c != 0:
        return c
    if left.low_inclusive != right.low_inclusive:
        return -1 if left.low_inclusive else 1
    if left.high is None and right.high is None:
        return 0
    if left.high is None:
        return 1
    if right.high is None:
        return -1
    return _compare_tokens(left.high.tokens, right.high.tokens)


def _parse_space_and_predicate(spec: str, *, quilt: bool) -> VersionSpec:
    raw = _strip_outer_quotes(spec)
    if not raw or raw in {"*", "any", "ANY"}:
        return VersionSpec.any(raw or "*")
    tokens = _split_predicate_tokens(raw)
    if not tokens:
        return VersionSpec.any("*")
    intervals: list[Interval] = []
    for token in tokens:
        if token in {"||", "|"}:
            return VersionSpec.error("不支持用 | 组合版本约束", raw)
        interval = _parse_comparator(token, quilt=quilt)
        if interval is None:
            return VersionSpec.error(f"无法解析版本约束“{token}”", raw)
        intervals.append(interval)
    merged = _intersect_intervals(intervals)
    if merged is None:
        return VersionSpec([], raw, "版本约束没有交集")
    return VersionSpec(_merge_intervals([merged]), raw)


def parse_fabric_predicate(spec: str | None) -> VersionSpec:
    """Parse Fabric Loader's whitespace-separated SemVer predicate."""
    if isinstance(spec, (list, tuple)):
        return _parse_or_values(spec, parse_fabric_predicate)
    return _parse_space_and_predicate(str(spec or "*"), quilt=False)


def parse_quilt_spec(spec: object) -> VersionSpec:
    """Parse the Quilt dependency version specifier grammar."""
    if spec is None:
        return VersionSpec.any("*")
    if isinstance(spec, str):
        return _parse_space_and_predicate(spec, quilt=True)
    if isinstance(spec, (int, float)):
        return VersionSpec.exact(str(spec), str(spec))
    if isinstance(spec, list):
        return _parse_or_values(spec, parse_quilt_spec)
    if isinstance(spec, dict):
        if "all" in spec and isinstance(spec["all"], list):
            result: VersionSpec | None = None
            for item in spec["all"]:
                parsed = parse_quilt_spec(item)
                result = parsed if result is None else result.intersect(parsed)
            return result or VersionSpec.any("*")
        if "any" in spec and isinstance(spec["any"], list):
            return _parse_or_values(spec["any"], parse_quilt_spec)
    return VersionSpec.error("无法解析 Quilt 版本约束", repr(spec))


def _parse_or_values(values: Iterable[object], parser) -> VersionSpec:
    result = VersionSpec([], " | ".join(str(v) for v in values))
    for value in values:
        parsed = parser(value)
        if parsed.parse_error:
            return parsed
        result.intervals.extend(parsed.intervals)
    if not result.intervals:
        return VersionSpec.any(result.original or "*")
    return VersionSpec(_merge_intervals(result.intervals), result.original)


def parse_loose_constraint(spec: object, *, loader: str = "") -> VersionSpec:
    """Best-effort parser used when the owning loader is unknown/legacy."""
    if spec is None:
        return VersionSpec.any("*")
    text = _strip_outer_quotes(str(spec))
    if not text or text == "*":
        return VersionSpec.any("*")
    if loader in {"fabric", "quilt"}:
        return parse_quilt_spec(text) if loader == "quilt" else parse_fabric_predicate(text)
    if text.startswith("[") or text.startswith("("):
        return parse_maven_range(text)
    if re.search(r"(?:>=|<=|>|<|\^|~)", text):
        return _parse_space_and_predicate(text, quilt=False)
    return VersionSpec.exact(text, text)


def java_major_to_class_version(java_major: int) -> int:
    return java_major + 44


def class_version_to_java(major: int) -> int | None:
    mapping = {
        45: 1, 46: 2, 47: 3, 48: 4, 49: 5, 50: 6, 51: 7, 52: 8,
        53: 9, 54: 10, 55: 11, 56: 12, 57: 13, 58: 14, 59: 15,
        60: 16, 61: 17, 62: 18, 63: 19, 64: 20, 65: 21, 66: 22,
        67: 23, 68: 24, 69: 25,
    }
    return mapping.get(major)


def is_snapshot_like(version: str) -> bool:
    text = version.strip().lower()
    return bool(re.match(r"^\d{2}w\d{2}[a-z]$", text) or "snapshot" in text)


__all__ = [
    "Interval",
    "Version",
    "VersionSpec",
    "class_version_to_java",
    "compare",
    "is_snapshot_like",
    "java_major_to_class_version",
    "parse_fabric_predicate",
    "parse_loose_constraint",
    "parse_maven_range",
    "parse_quilt_spec",
    "versions_equal",
]
