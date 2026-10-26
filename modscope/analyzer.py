"""Static MOD conflict analysis: dependencies, loaders, versions and classes."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import re
import time
import zipfile
from typing import Any, Iterable

from .metadata import (
    canonical_id,
    infer_mod_id_from_filename,
    read_jar_metadata,
    read_nested_mods,
)
from .models import (
    FileRef,
    Finding,
    JarRecord,
    LOADER_FAMILIES,
    LOADER_LABELS,
    ModRecord,
    ScanOptions,
)
from .versions import (
    Version,
    VersionSpec,
    class_version_to_java,
)


APP_VERSION = "1.0.0"
REPORT_SCHEMA = "modscope.report.v1"

SEVERITY_ORDER = {"error": 0, "warning": 1, "notice": 2}
CATEGORY_ORDER = {
    "loader": 0,
    "dependency": 1,
    "version": 2,
    "integrity": 3,
    "duplicate": 4,
    "class": 5,
    "mixin": 6,
    "resource": 7,
    "metadata": 8,
    "info": 9,
}
SEVERITY_LABEL = {"error": "严重", "warning": "警告", "notice": "提示"}
CATEGORY_LABEL = {
    "loader": "加载器",
    "dependency": "依赖",
    "version": "版本",
    "integrity": "文件完整性",
    "duplicate": "重复",
    "class": "类冲突",
    "mixin": "Mixin",
    "resource": "资源",
    "metadata": "元数据",
    "info": "提示",
}

LIBRARY_PREFIXES = (
    "kotlin/", "org/jetbrains/", "org/apache/", "com/google/", "io/netty/",
    "org/slf4j/", "org/objectweb/asm/", "net/bytebuddy/", "org/lwjgl/",
    "it/unimi/", "com/typesafe/", "org/joml/", "org/yaml/", "org/intellij/",
    "com/electronwill/", "org/spongepowered/", "com/mojang/", "joptsimple/",
    "oshi/", "com/sun/", "javax/", "jakarta/", "org/jspecify/",
    "org/checkerframework/", "com/github/", "org/antlr/", "com/ibm/",
    "org/fusesource/", "net/java/", "org/tukaani/", "org/xerial/",
)

COMMON_ASSET_NAMESPACES = {
    "minecraft", "realms", "c", "forge", "neoforge", "fabric", "quilt",
    "fabric-api",
}

VALID_LOADERS = {"auto", "fabric", "quilt", "forge", "neoforge", "liteloader"}
VALID_SIDES = {"client", "server"}


class ScanError(Exception):
    """A user-facing scan input error."""


@dataclass
class _Provider:
    mod_id: str
    version: str
    mod: ModRecord
    jar: JarRecord
    nested: bool = False
    disabled: bool = False
    provided_id: str = ""


@dataclass
class _ConflictGroup:
    owner: ModRecord
    owner_jar: JarRecord
    target: _Provider
    severity: str
    reason: str
    source: str


@dataclass
class _PendingFindings:
    items: list[Finding] = field(default_factory=list)

    def add(self, finding: Finding) -> None:
        self.items.append(finding)


def loader_label(loader: str) -> str:
    return LOADER_LABELS.get(loader, loader or "未知")


def is_mod_filename(filename: str) -> bool:
    return strip_disabled_suffix(filename) is not None


def strip_disabled_suffix(filename: str) -> str | None:
    lower = filename.lower()
    disabled = False
    if lower.endswith(".disabled"):
        lower = lower[:-9]
        disabled = True
    if lower.endswith((".jar", ".litemod", ".zip")):
        return lower if disabled else lower
    return None


def _is_disabled_filename(filename: str) -> bool:
    return filename.lower().endswith(".disabled") and _base_archive_name(filename) is not None


def _base_archive_name(filename: str) -> str | None:
    lower = filename.lower()
    if lower.endswith(".disabled"):
        lower = lower[:-9]
    if lower.endswith((".jar", ".litemod", ".zip")):
        return lower
    return None


def normalize_mc_version(value: str) -> str:
    text = str(value or "").strip().strip("\"'")
    text = re.sub(r"(?i)^minecraft\s*", "", text).strip()
    if not text:
        return ""
    if not re.match(r"^[0-9][0-9A-Za-z._+\-]*$", text):
        raise ScanError("Minecraft 版本格式无法识别，请输入例如 1.20.1 或 24w14a。")
    return text


def validate_options(options: ScanOptions) -> ScanOptions:
    options.mc_version = normalize_mc_version(options.mc_version)
    options.loader = (options.loader or "auto").strip().lower()
    if options.loader not in VALID_LOADERS:
        raise ScanError("未知的加载器选项。")
    options.side = (options.side or "client").strip().lower()
    if options.side not in VALID_SIDES:
        raise ScanError("未知的运行端选项。")
    if options.java_version is not None:
        try:
            java = int(options.java_version)
        except (TypeError, ValueError):
            raise ScanError("Java 主版本必须是数字。") from None
        if java < 1 or java > 99:
            raise ScanError("Java 主版本超出合理范围。")
        options.java_version = java
    return options


def discover_path_files(path_value: str, options: ScanOptions) -> tuple[list[FileRef], str]:
    raw = str(path_value or "").strip().strip("\"'")
    if not raw:
        raise ScanError("请输入 mods 文件夹路径，或直接选择 MOD 文件。")
    raw = os.path.expandvars(os.path.expanduser(raw))
    path = Path(raw)
    try:
        path = path.resolve()
    except OSError:
        pass
    if not path.exists():
        raise ScanError(f"路径不存在：{path}")
    refs: list[FileRef] = []
    if path.is_file():
        if not is_mod_filename(path.name):
            raise ScanError("请选择 .jar 或 .litemod 文件。")
        if not _is_disabled_filename(path.name) or options.include_disabled:
            refs.append(FileRef(str(path), path.name, _is_disabled_filename(path.name)))
        source_label = str(path.parent)
    elif path.is_dir():
        entries = sorted(path.iterdir(), key=lambda item: item.name.casefold())
        for entry in entries:
            if not entry.is_file() or not is_mod_filename(entry.name):
                continue
            disabled = _is_disabled_filename(entry.name)
            if disabled and not options.include_disabled:
                continue
            refs.append(FileRef(str(entry), entry.name, disabled))
        source_label = str(path)
    else:
        raise ScanError("该路径不是文件或文件夹。")
    if not refs:
        raise ScanError("没有找到可分析的 .jar 或 .litemod 文件。")
    return refs, source_label


def _sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _read_class_major(zf: zipfile.ZipFile, info: zipfile.ZipInfo) -> int | None:
    try:
        with zf.open(info, "r") as handle:
            header = handle.read(8)
        if len(header) < 8 or header[:4] != b"\xca\xfe\xba\xbe":
            return None
        major = int.from_bytes(header[6:8], "big")
        return major if major > 0 else None
    except Exception:
        return None


def _sample_class_infos(infos: list[zipfile.ZipInfo]) -> list[zipfile.ZipInfo]:
    if not infos:
        return []
    if len(infos) <= 80:
        return infos
    by_size = sorted(infos, key=lambda info: info.file_size, reverse=True)[:40]
    stride = max(1, len(infos) // 40)
    spread = infos[::stride][:40]
    seen: set[str] = set()
    result: list[zipfile.ZipInfo] = []
    for info in by_size + spread:
        if info.filename not in seen:
            seen.add(info.filename)
            result.append(info)
    return result


def _scan_one(ref: FileRef, index: int, options: ScanOptions,
              class_map: dict[str, list[tuple[str, int, int]]]) -> JarRecord:
    display = ref.relative_name or ref.display_name
    jar = JarRecord(
        key=f"jar-{index:04d}",
        path=ref.path,
        display_name=display,
        disabled=ref.disabled,
    )
    try:
        jar.size = os.path.getsize(ref.path)
        jar.sha256 = _sha256_file(ref.path)
    except OSError as exc:
        jar.valid = False
        jar.error = f"无法读取文件：{exc}"
        jar.issues.append(jar.error)
        return jar
    try:
        zf = zipfile.ZipFile(ref.path, "r")
    except (zipfile.BadZipFile, OSError) as exc:
        jar.valid = False
        jar.error = f"不是有效的 JAR/ZIP 文件：{exc}"
        jar.issues.append(jar.error)
        return jar
    with zf:
        try:
            infos = zf.infolist()
        except Exception as exc:
            jar.valid = False
            jar.error = f"读取 JAR 目录失败：{exc}"
            jar.issues.append(jar.error)
            return jar
        jar.entry_count = sum(1 for info in infos if not info.is_dir())
        class_infos: list[zipfile.ZipInfo] = []
        class_crcs: dict[str, tuple[int, int]] = {}
        for info in infos:
            if info.is_dir():
                continue
            name = info.filename
            lower = name.lower()
            if lower.startswith("assets/") or lower.startswith("data/"):
                jar.resource_count += 1
                parts = name.split("/")
                if len(parts) >= 2 and parts[1]:
                    if lower.startswith("assets/"):
                        jar.asset_namespaces.add(parts[1])
                    else:
                        jar.data_namespaces.add(parts[1])
            if (
                lower.endswith(".class")
                and not lower.startswith("meta-inf/")
                and not lower.endswith("/module-info.class")
                and lower != "module-info.class"
                and not lower.endswith("/package-info.class")
                and lower != "package-info.class"
            ):
                class_infos.append(info)
                class_crcs[name] = (info.CRC, info.file_size)
                jar.class_names.add(name)
        try:
            records, issues = read_jar_metadata(zf, jar)
            jar.mods = records
            jar.issues.extend(issues)
        except Exception as exc:
            jar.issues.append(f"读取 MOD 元数据时发生异常：{exc}")
        if not jar.disabled and jar.recognized:
            try:
                jar.nested_mods = read_nested_mods(zf, jar)
            except Exception as exc:
                jar.issues.append(f"读取内置 JAR 失败：{exc}")
        if options.deep_scan:
            jar.max_class_major = max(
                (
                    major for major in
                    (_read_class_major(zf, info) for info in _sample_class_infos(class_infos))
                    if major is not None
                ),
                default=None,
            )
        if (
            options.deep_scan
            and not jar.disabled
            and jar.recognized
        ):
            for name, (crc, size) in class_crcs.items():
                class_map[name].append((jar.key, crc, size))
    return jar


def _format_version_specs(specs: Iterable[VersionSpec]) -> str:
    seen: list[str] = []
    for spec in specs:
        text = spec.describe()
        if text not in seen:
            seen.append(text)
    return " / ".join(seen) if seen else "未声明"


def _spec_matches(spec: VersionSpec, version: str) -> bool | None:
    if spec.parse_error:
        return None
    if not version or "${" in version or version.strip().lower() in {"unknown", "n/a"}:
        return None
    if spec.is_any:
        return True
    try:
        return spec.matches(version)
    except Exception:
        return None


def _mod_ref(mod: ModRecord, jar: JarRecord) -> dict[str, str]:
    return {
        "id": mod.id,
        "name": mod.name or mod.id,
        "version": mod.version,
        "file": jar.display_name,
        "loader": loader_label(mod.primary_loader),
    }


def _dedupe_mod_refs(refs: Iterable[dict[str, str]]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for ref in refs:
        key = (ref.get("id", ""), ref.get("file", ""))
        if key in seen:
            continue
        seen.add(key)
        result.append(ref)
    return result


class Analyzer:
    def __init__(self, options: ScanOptions | None = None):
        self.options = validate_options(options or ScanOptions())

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def analyze(self, files: list[FileRef], source_label: str) -> dict[str, Any]:
        if not files:
            raise ScanError("没有可分析的文件。")
        started = time.perf_counter()
        class_map: dict[str, list[tuple[str, int, int]]] = defaultdict(list)
        jars = [
            _scan_one(ref, index, self.options, class_map)
            for index, ref in enumerate(files, start=1)
        ]

        active_mods: list[tuple[ModRecord, JarRecord]] = []
        disabled_mods: list[tuple[ModRecord, JarRecord]] = []
        nested_mods: list[tuple[ModRecord, JarRecord]] = []
        for jar in jars:
            if jar.disabled:
                disabled_mods.extend((mod, jar) for mod in jar.mods)
                continue
            if jar.recognized:
                active_mods.extend((mod, jar) for mod in jar.mods)
                nested_mods.extend((mod, jar) for mod in jar.nested_mods)

        # Top-level and nested IDs are separate inventories.
        providers: dict[str, list[_Provider]] = defaultdict(list)
        disabled_providers: dict[str, list[_Provider]] = defaultdict(list)
        for mod, jar in active_mods + nested_mods:
            is_nested = mod.nested
            for provided_id, version in self._provided_entries(mod):
                providers[provided_id].append(_Provider(
                    mod.id, version, mod, jar, nested=is_nested, provided_id=provided_id
                ))
        for mod, jar in disabled_mods:
            for provided_id, version in self._provided_entries(mod):
                disabled_providers[provided_id].append(_Provider(
                    mod.id, version, mod, jar, disabled=True, provided_id=provided_id
                ))

        findings = _PendingFindings()
        self._check_integrity(jars, active_mods, findings)
        if not active_mods:
            findings.add(Finding(
                severity="notice", category="info",
                title="没有检测到启用的 MOD",
                summary="文件夹中的文件要么已禁用，要么没有可识别的 MOD 元数据。",
                evidence=[
                    jar.display_name for jar in jars
                    if jar.disabled or not jar.recognized
                ][:30],
                recommendation="确认扫描的是正确的 mods 文件夹，并检查文件是否被改名为 .disabled。",
            ))
        self._check_duplicate_ids(active_mods, disabled_mods, nested_mods, findings)
        self._check_loader_mismatch(active_mods, findings)
        mc_info = self._check_minecraft_versions(active_mods, findings)
        self._check_dependencies(active_mods, providers, disabled_providers, findings)
        self._check_incompatibilities(active_mods, providers, findings)
        self._check_java(active_mods, jars, findings)
        if self.options.deep_scan:
            class_info = self._check_classes(jars, class_map, findings)
            self._check_mixin_collisions(jars, active_mods, findings)
            self._check_namespace_collisions(jars, active_mods, findings)
        else:
            class_info = {"duplicate_class_count": 0, "core_conflicts": 0,
                          "library_conflicts": 0, "identical_classes": 0}
        self._check_duplicate_files(jars, active_mods, findings)

        all_mod_dicts = self._serialize_mods(active_mods, disabled_mods, nested_mods, jars)
        jar_dicts = [self._serialize_jar(jar) for jar in jars]
        ordered = self._order_findings(findings.items)
        counts = Counter(finding.severity for finding in ordered)
        health = self._health_score(ordered)
        grade, grade_label = self._grade(health, counts)

        loader_counts = Counter(mod.primary_loader for mod, _ in active_mods)
        disabled_count = len(disabled_mods)
        nested_count = len(nested_mods)
        unknown_jars = [
            jar for jar in jars
            if jar.valid and not jar.recognized and not jar.disabled
        ]
        effective_loader = self._effective_loader(loader_counts)
        elapsed = time.perf_counter() - started
        if self.options.loader != "auto":
            target_loader = self.options.loader
        else:
            target_loader = effective_loader
        if target_loader == "legacyforge":
            target_loader = "forge"
        exact_mc = mc_info.get("exact_versions", [])
        caveats = [
            "这是静态扫描，不会启动 Minecraft。Mixin 注入点、运行时事件顺序等动态行为无法被完全推断。",
            "依赖版本按各加载器的声明规则比对；MOD 元数据写错时，需要以日志和实际启动结果为准。",
            "内置 JAR 会参与依赖可用性判断，但不会递归分析其内部类冲突或单独报告它的依赖。",
        ]
        if not self.options.mc_version:
            caveats.append("未指定 Minecraft 版本：已检查各 MOD 声明的版本约束是否有公共交集，但未逐个校验目标版本。")
        if self.options.java_version is None:
            caveats.append("未指定 Java 主版本：已读取字节码版本，但未检查 Java 依赖约束是否满足。")
        caveats.append("加载器自身的版本范围未校验（扫描时没有加载器实际版本信息）。")

        summary_label = self._summary_label(ordered, counts)
        report = {
            "schema": REPORT_SCHEMA,
            "scanner": {"name": "ModScope", "version": APP_VERSION},
            "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
            "duration_ms": round(elapsed * 1000),
            "source": {
                "kind": self.options.source_kind,
                "label": source_label,
            },
            "options": {
                "mc_version": self.options.mc_version,
                "loader": target_loader,
                "loader_requested": self.options.loader,
                "side": self.options.side,
                "java_version": self.options.java_version,
                "deep_scan": self.options.deep_scan,
                "include_disabled": self.options.include_disabled,
            },
            "summary": {
                "health_score": health,
                "grade": grade,
                "grade_label": grade_label,
                "headline": summary_label,
                "errors": counts.get("error", 0),
                "warnings": counts.get("warning", 0),
                "notices": counts.get("notice", 0),
                "total_findings": len(ordered),
            },
            "target": {
                "loader": target_loader,
                "loader_label": "混合" if target_loader == "mixed" else loader_label(target_loader),
                "loader_detection": "auto" if self.options.loader == "auto" else "manual",
                "loaders": [
                    {"id": loader, "label": loader_label(loader), "count": count}
                    for loader, count in loader_counts.most_common()
                ],
                "mc_version": self.options.mc_version,
                "detected_mc_versions": exact_mc,
                "mc_constraints": mc_info.get("constraint_counts", []),
                "common_mc_range": mc_info.get("common_range"),
                "side": self.options.side,
                "java_version": self.options.java_version,
            },
            "stats": {
                "scanned_files": len(files),
                "jar_count": len(jars),
                "valid_jars": sum(1 for jar in jars if jar.valid),
                "active_mods": len(active_mods),
                "disabled_mods": disabled_count,
                "nested_mods": nested_count,
                "unknown_jars": len(unknown_jars),
                "class_count": sum(len(jar.class_names) for jar in jars),
                **class_info,
            },
            "findings": [finding.to_dict() for finding in ordered],
            "mods": all_mod_dicts,
            "jars": jar_dicts,
            "caveats": caveats,
        }
        return report

    # ------------------------------------------------------------------
    # Checks
    # ------------------------------------------------------------------
    def _check_integrity(self, jars: list[JarRecord], active_mods,
                         findings: _PendingFindings) -> None:
        invalid = [jar for jar in jars if not jar.valid]
        if invalid:
            findings.add(Finding(
                severity="error", category="integrity",
                title=f"{len(invalid)} 个 JAR 无法读取",
                summary="损坏或不是有效的 JAR/ZIP 文件，游戏通常会在加载阶段报错。",
                evidence=[f"{jar.display_name}：{jar.error or '未知错误'}" for jar in invalid[:30]],
                count=len(invalid),
                recommendation="重新下载这些文件，或把它们移出 mods 文件夹后逐一排查。",
            ))
        issue_jars = [
            jar for jar in jars
            if jar.valid and not jar.disabled and jar.recognized and jar.issues
        ]
        if issue_jars:
            evidence: list[str] = []
            for jar in issue_jars[:30]:
                for issue in jar.issues[:6]:
                    evidence.append(f"{jar.display_name}：{issue}")
            findings.add(Finding(
                severity="warning", category="metadata",
                title=f"{len(issue_jars)} 个 JAR 的元数据或 Mixin 配置存在异常",
                summary="这些问题会降低依赖分析的确定性，部分也可能导致启动失败。",
                evidence=evidence[:60],
                count=len(issue_jars),
                recommendation="检查对应 MOD 是否为正确版本，必要时重新下载。",
            ))
        unknown = [
            jar for jar in jars
            if jar.valid and not jar.disabled and not jar.recognized
        ]
        if unknown:
            findings.add(Finding(
                severity="notice", category="metadata",
                title=f"{len(unknown)} 个 JAR 未识别为 MOD",
                summary="它们可能是资源包、安装器或缺少标准元数据的库，已跳过依赖分析。",
                evidence=[jar.display_name for jar in unknown[:30]],
                count=len(unknown),
                recommendation="如果它是应加载的 MOD，请确认版本和加载器，并检查其 META-INF。",
            ))
        inferred = [
            (mod, jar) for mod, jar in active_mods
            if mod.inference_note
        ]
        if inferred:
            findings.add(Finding(
                severity="notice", category="metadata",
                title=f"{len(inferred)} 个 MOD 使用了推断元数据",
                summary="这些文件缺少标准 MOD 元数据，名称和 ID 由文件名或 Manifest 推断；依赖分析可能不完整。",
                evidence=[
                    f"{jar.display_name} → {mod.name}（{mod.id}）"
                    for mod, jar in inferred[:30]
                ],
                mods=_dedupe_mod_refs(_mod_ref(mod, jar) for mod, jar in inferred),
                count=len(inferred),
                confidence="medium",
                recommendation="确认这些文件是否确实属于当前 mods 文件夹；优先使用带完整元数据的官方构建。",
            ))

    def _check_duplicate_ids(self, active_mods, disabled_mods, nested_mods,
                             findings: _PendingFindings) -> None:
        by_id: dict[str, list[tuple[ModRecord, JarRecord]]] = defaultdict(list)
        for mod, jar in active_mods:
            by_id[canonical_id(mod.id)].append((mod, jar))
        duplicates: list[tuple[str, list[tuple[ModRecord, JarRecord]]]] = []
        for mod_id, entries in by_id.items():
            if len({jar.key for _, jar in entries}) < 2:
                continue
            duplicates.append((mod_id, entries))
        if duplicates:
            evidence: list[str] = []
            refs: list[dict[str, str]] = []
            for mod_id, entries in duplicates:
                detail = "；".join(
                    f"{mod.name} {mod.version}（{jar.display_name}）" for mod, jar in entries
                )
                evidence.append(f"{mod_id}：{detail}")
                refs.extend(_mod_ref(mod, jar) for mod, jar in entries)
            findings.add(Finding(
                severity="error", category="duplicate",
                title=f"{len(duplicates)} 个 MOD ID 被重复安装",
                summary="同一个 MOD ID 同时存在多个 JAR 时，加载器可能拒绝启动，或只加载其中一个版本。",
                detail="常见于手动更新 MOD 后旧文件没有删除，或同时放了不同版本/不同加载器的副本。",
                evidence=evidence[:40],
                mods=_dedupe_mod_refs(refs),
                count=len(duplicates),
                recommendation="每个 MOD ID 只保留一个与当前游戏版本、加载器匹配的 JAR。",
            ))

        # Duplicate IDs declared multiple times inside one JAR.
        same_jar: list[str] = []
        versions_by_jar_id: dict[tuple[str, str], set[str]] = defaultdict(set)
        for mod, jar in active_mods:
            versions_by_jar_id[(jar.key, canonical_id(mod.id))].add(mod.version)
        for (jar_key, mod_id), versions in versions_by_jar_id.items():
            if len(versions) <= 1:
                continue
            jar = next(jar for mod, jar in active_mods if jar.key == jar_key)
            same_jar.append(f"{jar.display_name}：{mod_id} 声明了多个版本（{' / '.join(sorted(versions))}）")
        if same_jar:
            findings.add(Finding(
                severity="warning", category="metadata",
                title="同一个 JAR 内出现重复且版本不同的 MOD ID",
                summary="混合加载器元数据可能描述了两个不同的构建，加载器会以其中一个为准。",
                evidence=list(dict.fromkeys(same_jar))[:20],
                recommendation="确认该 JAR 是否适合当前加载器；优先使用官方发布的对应版本。",
            ))

        top_ids = {canonical_id(mod.id): (mod, jar) for mod, jar in active_mods}
        nested_dups = []
        for mod, jar in nested_mods:
            top = top_ids.get(canonical_id(mod.id))
            if top:
                nested_dups.append(f"{mod.name} {mod.version} 内置于 {jar.display_name}，同时已独立安装 {top[0].version}")
        if nested_dups:
            findings.add(Finding(
                severity="notice", category="duplicate",
                title="内置 MOD 与独立安装的 MOD 同名",
                summary="加载器通常会自动选择版本，但同名 JIJ 与独立安装并存时仍可能出现版本错位。",
                evidence=list(dict.fromkeys(nested_dups))[:30],
                recommendation="优先保留较新且匹配游戏版本的版本；排查依赖问题时再删除重复项。",
            ))

    def _check_loader_mismatch(self, active_mods, findings: _PendingFindings) -> None:
        if not active_mods:
            return
        selected = self.options.loader
        if selected == "auto":
            family_counts: Counter = Counter()
            family_refs: dict[str, list[tuple[ModRecord, JarRecord]]] = defaultdict(list)
            for mod, jar in active_mods:
                families: set[str] = set()
                for loader in mod.loaders:
                    family = LOADER_FAMILIES.get(loader)
                    if family and family not in {"liteloader", "unknown"}:
                        families.add(family)
                if len(families) == 1:
                    family = next(iter(families))
                    family_counts[family] += 1
                    family_refs[family].append((mod, jar))
            incompatible_families = [
                family for family in family_counts
                if family in {"fabric", "forge", "neoforge", "rift"}
            ]
            if len(incompatible_families) > 1:
                refs = []
                evidence = []
                for family in incompatible_families:
                    mods = family_refs[family]
                    samples = "、".join(mod.name for mod, _ in mods[:4])
                    evidence.append(f"{family.upper()}：{len(mods)} 个 MOD（{samples}）")
                    refs.extend(_mod_ref(mod, jar) for mod, jar in mods)
                findings.add(Finding(
                    severity="error", category="loader",
                    title="检测到多个互不兼容的加载器",
                    summary="Forge、NeoForge、Fabric 的 MOD 不能直接混装在同一个实例中（Quilt 与 Fabric 属同一生态）。",
                    evidence=evidence,
                    mods=_dedupe_mod_refs(refs),
                    recommendation="为不同加载器分别建立实例，或只保留当前加载器对应的 MOD。",
                ))
            return

        allowed = {
            "fabric": {"fabric"},
            "quilt": {"quilt", "fabric"},
            "forge": {"forge", "legacyforge"},
            "neoforge": {"neoforge"},
            "liteloader": {"liteloader", "forge", "legacyforge"},
        }.get(selected, {selected})
        mismatched: list[tuple[ModRecord, JarRecord]] = []
        for mod, jar in active_mods:
            if not mod.loaders:
                continue
            known_loaders = {loader for loader in mod.loaders if loader != "unknown"}
            if known_loaders and not (known_loaders & allowed):
                mismatched.append((mod, jar))
        if mismatched:
            evidence = [
                f"{mod.name} {mod.version}：{loader_label(mod.primary_loader)}（{jar.display_name}）"
                for mod, jar in mismatched[:40]
            ]
            findings.add(Finding(
                severity="error", category="loader",
                title=f"{len(mismatched)} 个 MOD 与所选加载器不匹配",
                summary=f"当前选择的是 {loader_label(selected)}，这些 MOD 使用的是其他加载器格式。",
                evidence=evidence,
                mods=_dedupe_mod_refs(_mod_ref(mod, jar) for mod, jar in mismatched),
                count=len(mismatched),
                recommendation="更换对应的加载器版本，或删除/替换这些 MOD。",
            ))

    def _check_minecraft_versions(self, active_mods, findings: _PendingFindings) -> dict[str, Any]:
        constraint_counts: Counter = Counter()
        exact_counts: Counter = Counter()
        mismatched: list[tuple[ModRecord, JarRecord, list[str]]] = []
        inconsistent: list[tuple[ModRecord, JarRecord, str]] = []
        parse_errors: list[str] = []
        mod_common: list[tuple[ModRecord, JarRecord, VersionSpec]] = []
        target = self.options.mc_version
        for mod, jar in active_mods:
            if not mod.mc_specs:
                continue
            specs_display = [spec.describe() for spec in mod.mc_specs]
            for text in specs_display:
                constraint_counts[text] += 1
            for spec in mod.mc_specs:
                exact = spec.exact_version
                if exact:
                    exact_counts[exact] += 1
                if spec.parse_error:
                    parse_errors.append(f"{mod.name}：{spec.describe()}")
            combined = VersionSpec.any("*")
            first = True
            for spec in mod.mc_specs:
                if spec.parse_error:
                    combined = spec
                    break
                combined = spec if first else combined.intersect(spec)
                first = False
            if combined.parse_error:
                inconsistent.append((mod, jar, combined.parse_error))
                continue
            mod_common.append((mod, jar, combined))
            if target:
                bad = [spec.describe() for spec in mod.mc_specs if not spec.matches(target)]
                if bad:
                    mismatched.append((mod, jar, bad))

        if mismatched:
            evidence = [
                f"{mod.name} {mod.version}（{jar.display_name}）要求 {' / '.join(ranges)}"
                for mod, jar, ranges in mismatched[:50]
            ]
            findings.add(Finding(
                severity="error", category="version",
                title=f"{len(mismatched)} 个 MOD 与 Minecraft {target} 不匹配",
                summary="这些 MOD 声明的游戏版本范围不包含当前目标版本，启动时很可能崩溃或拒绝加载。",
                detail="Forge/NeoForge 的版本范围通常写在 dependencies.minecraft；Fabric/Quilt 写在 depends.minecraft；旧版来自 mcmod.info。",
                evidence=evidence,
                mods=_dedupe_mod_refs(_mod_ref(mod, jar) for mod, jar, _ in mismatched),
                count=len(mismatched),
                recommendation="下载与游戏版本匹配的 MOD，或在启动器中切换到它们共同支持的版本。",
            ))

        common_range = None
        if mod_common:
            common = VersionSpec.any("*")
            first = True
            for _, _, spec in mod_common:
                common = spec if first else common.intersect(spec)
                first = False
                if common.parse_error:
                    break
            if common.parse_error:
                if len(mod_common) >= 2 and not target:
                    evidence = [
                        f"{mod.name}：{_format_version_specs(mod.mc_specs)}"
                        for mod, _, _ in mod_common[:50]
                    ]
                    findings.add(Finding(
                        severity="warning", category="version",
                        title="没有找到同时兼容所有 MOD 的 Minecraft 版本",
                        summary="这些 MOD 声明的游戏版本范围没有公共交集；即使未指定目标版本，也能确定它们不能一起运行。",
                        evidence=evidence,
                        mods=_dedupe_mod_refs(_mod_ref(mod, jar) for mod, jar, _ in mod_common),
                        count=len(mod_common),
                        confidence="medium",
                        recommendation="按游戏版本拆分实例，或更换其中一部分 MOD。",
                    ))
            else:
                common_range = common.describe() if not common.is_any else "*"
        if parse_errors:
            findings.add(Finding(
                severity="notice", category="metadata",
                title=f"{len(parse_errors)} 个 Minecraft 版本约束无法解析",
                summary="这些约束不会被用于版本交集判断。",
                evidence=parse_errors[:30],
                recommendation="确认 MOD 元数据是否是给其他加载器/游戏版本的版本。",
            ))
        return {
            "exact_versions": [
                {"version": version, "count": count}
                for version, count in exact_counts.most_common(12)
            ],
            "constraint_counts": [
                {"constraint": constraint, "count": count}
                for constraint, count in constraint_counts.most_common(10)
            ],
            "common_range": common_range,
        }

    def _check_dependencies(self, active_mods, providers, disabled_providers,
                            findings: _PendingFindings) -> None:
        missing: dict[str, dict[str, Any]] = {}
        disabled_missing: dict[str, dict[str, Any]] = {}
        version_mismatch: dict[str, dict[str, Any]] = {}
        unknown_versions = 0
        parse_issues: list[str] = []
        for mod, jar in active_mods:
            for requirement in mod.requirements:
                if requirement.kind != "required":
                    continue
                if requirement.side and requirement.side != self.options.side:
                    continue
                if self._requirement_waived(requirement, providers):
                    continue
                alternatives = [
                    alt for alt in requirement.alternatives
                    if canonical_id(alt.id) and canonical_id(alt.id) not in {
                        "minecraft", "forge", "neoforge", "fabricloader", "fabric_loader",
                        "quiltloader", "quilt_loader", "liteloader", "java", "javafml",
                        "lowcodefml", "fml", "rift",
                    }
                ]
                if not alternatives:
                    continue
                if any(canonical_id(alt.id) == canonical_id(mod.id) for alt in alternatives):
                    alternatives = [
                        alt for alt in alternatives
                        if canonical_id(alt.id) != canonical_id(mod.id)
                    ]
                    if not alternatives:
                        continue
                active_hits: list[tuple[Any, _Provider, bool | None]] = []
                disabled_hits: list[tuple[Any, _Provider]] = []
                for alt in alternatives:
                    key = canonical_id(alt.id)
                    if alt.spec.parse_error:
                        parse_issues.append(
                            f"{mod.name} 对 {alt.id} 的版本约束无法解析：{alt.spec.describe()}"
                        )
                    for provider in providers.get(key, []):
                        if provider.mod is mod:
                            continue
                        verdict = _spec_matches(alt.spec, provider.version)
                        if verdict is None and not alt.spec.parse_error:
                            unknown_versions += 1
                        active_hits.append((alt, provider, verdict))
                    for provider in disabled_providers.get(key, []):
                        if provider.mod is mod:
                            continue
                        disabled_hits.append((alt, provider))
                if active_hits and any(verdict is True for _, _, verdict in active_hits):
                    continue
                if active_hits and all(verdict is None for _, _, verdict in active_hits):
                    # Present, but the installed MOD's version is unusable.
                    continue
                if active_hits:
                    # At least one alternative is present, but none satisfies
                    # the version predicate.  Group by target signature.
                    signature = " / ".join(sorted({alt.id for alt, _, _ in active_hits}))
                    entry = version_mismatch.setdefault(signature, {
                        "owners": [], "hits": [], "specs": set(),
                    })
                    entry["owners"].append((mod, jar))
                    for alt, provider, _ in active_hits:
                        entry["hits"].append((alt, provider))
                        entry["specs"].add(alt.spec.describe())
                    continue
                if disabled_hits:
                    signature = " / ".join(sorted({alt.id for alt, _ in disabled_hits}))
                    entry = disabled_missing.setdefault(signature, {
                        "owners": [], "hits": [],
                    })
                    entry["owners"].append((mod, jar))
                    entry["hits"].extend(disabled_hits)
                    continue
                signature = " / ".join(sorted({alt.id for alt in alternatives}))
                entry = missing.setdefault(signature, {
                    "owners": [], "specs": set(), "reasons": set(),
                })
                entry["owners"].append((mod, jar))
                for alt in alternatives:
                    entry["specs"].add(alt.spec.describe())
                if requirement.reason:
                    entry["reasons"].add(requirement.reason)

        if missing:
            evidence: list[str] = []
            refs: list[dict[str, str]] = []
            for target, entry in sorted(missing.items(), key=lambda item: item[0]):
                reason = next(iter(entry["reasons"]), "")
                owners = "、".join(sorted({mod.name for mod, _ in entry["owners"]}))
                spec = " / ".join(sorted(entry["specs"]))
                line = f"缺失 {target}（要求 {spec}）← {owners}"
                if reason:
                    line += f"：{reason}"
                evidence.append(line)
                refs.extend(_mod_ref(mod, jar) for mod, jar in entry["owners"])
            n = len(missing)
            findings.add(Finding(
                severity="error", category="dependency",
                title=(f"缺少必需依赖：{next(iter(missing))}" if n == 1
                       else f"缺少 {n} 个必需依赖"),
                summary="依赖没有被任何已安装 MOD 提供；对应 MOD 无法完成加载，通常会直接崩溃。",
                evidence=evidence[:60],
                mods=_dedupe_mod_refs(refs),
                count=n,
                recommendation="安装缺失的 MOD，并确保版本符合要求；如果依赖来自其他加载器，请换用对应版本。",
            ))
        if disabled_missing:
            evidence = []
            refs = []
            for target, entry in sorted(disabled_missing.items(), key=lambda item: item[0]):
                owners = "、".join(sorted({mod.name for mod, _ in entry["owners"]}))
                files = " / ".join(sorted({provider.jar.display_name for _, provider in entry["hits"]}))
                evidence.append(f"{target} 存在于 {files}，但文件已禁用；需要它的是 {owners}")
                refs.extend(_mod_ref(mod, jar) for mod, jar in entry["owners"])
            findings.add(Finding(
                severity="warning", category="dependency",
                title=f"{len(disabled_missing)} 个依赖被禁用",
                summary="所需 MOD 的文件仍在文件夹中，但扩展名被改为 .disabled，因此不会被加载器读取。",
                evidence=evidence[:40],
                mods=_dedupe_mod_refs(refs),
                count=len(disabled_missing),
                recommendation="重新启用对应文件，或移除依赖它的 MOD。",
            ))
        if version_mismatch:
            evidence = []
            refs = []
            for signature, entry in sorted(version_mismatch.items(), key=lambda item: item[0]):
                owners = "、".join(sorted({mod.name for mod, _ in entry["owners"]}))
                for alt, provider in entry["hits"]:
                    evidence.append(
                        f"{provider.mod.name} 已安装 {provider.version or '未知版本'}，"
                        f"但 {owners} 要求 {alt.id} {alt.spec.describe()}"
                    )
                refs.extend(_mod_ref(mod, jar) for mod, jar in entry["owners"])
                refs.extend(_mod_ref(provider.mod, provider.jar) for _, provider in entry["hits"])
            n = len(version_mismatch)
            findings.add(Finding(
                severity="error", category="dependency",
                title=(f"依赖版本不满足要求" if n == 1 else f"{n} 个依赖的版本不满足要求"),
                summary="依赖 MOD 已安装，但版本不在需求方允许的范围内，加载时仍会被判定为缺失。",
                evidence=evidence[:60],
                mods=_dedupe_mod_refs(refs),
                count=n,
                recommendation="把依赖升级/降级到需求方允许的版本，或者更换需求方 MOD。",
            ))
        if parse_issues:
            findings.add(Finding(
                severity="notice", category="metadata",
                title="部分依赖版本约束无法解析",
                summary="这些依赖不会被判定为缺失，但版本匹配结果需要人工确认。",
                evidence=parse_issues[:30],
            ))
        if unknown_versions:
            findings.add(Finding(
                severity="notice", category="dependency",
                title=f"{unknown_versions} 条依赖版本无法校验",
                summary="依赖存在，但其版本未知（如构建占位符），因此只确认了可用性。",
                recommendation="在游戏内再次确认依赖版本，必要时查看启动日志。",
            ))

    def _check_incompatibilities(self, active_mods, providers,
                                 findings: _PendingFindings) -> None:
        conflicts: dict[str, _ConflictGroup] = {}
        parse_issues: list[str] = []
        for mod, jar in active_mods:
            for incompatibility in mod.incompatibilities:
                if incompatibility.side and incompatibility.side != self.options.side:
                    continue
                target_id = canonical_id(incompatibility.id)
                if not target_id or target_id in {"minecraft", "forge", "neoforge",
                                                 "fabricloader", "quilt_loader", "java"}:
                    continue
                if target_id == canonical_id(mod.id):
                    continue
                if self._unless_satisfied(incompatibility.unless, providers):
                    continue
                if incompatibility.spec.parse_error:
                    parse_issues.append(
                        f"{mod.name} 对 {incompatibility.id} 的互斥版本约束无法解析"
                    )
                    continue
                matched: list[_Provider] = []
                for provider in providers.get(target_id, []):
                    if canonical_id(provider.mod.id) == canonical_id(mod.id):
                        continue
                    verdict = _spec_matches(incompatibility.spec, provider.version)
                    if verdict is True or (
                        verdict is None and incompatibility.spec.is_any
                    ):
                        matched.append(provider)
                if not matched:
                    continue
                pair = " <-> ".join(sorted({canonical_id(mod.id), target_id}))
                group = conflicts.get(pair)
                target = matched[0]
                reason_parts = []
                if incompatibility.reason:
                    reason_parts.append(incompatibility.reason)
                reason_parts.append(f"要求 {target_id} {incompatibility.spec.describe()}")
                reason = "；".join(reason_parts)
                if group is None:
                    conflicts[pair] = _ConflictGroup(
                        mod, jar, target, incompatibility.severity,
                        reason, incompatibility.source,
                    )
                else:
                    if incompatibility.severity == "error":
                        group.severity = "error"
                    if reason not in group.reason:
                        group.reason += "；" + reason
        if conflicts:
            error_groups = [group for group in conflicts.values() if group.severity == "error"]
            warning_groups = [group for group in conflicts.values() if group.severity != "error"]
            for groups, severity, label in (
                (error_groups, "error", "已声明的 MOD 互斥"),
                (warning_groups, "warning", "加载器不建议同时安装的 MOD"),
            ):
                if not groups:
                    continue
                evidence = [
                    f"{group.owner.name} ↔ {group.target.mod.name}：{group.reason}"
                    for group in groups[:40]
                ]
                refs = []
                for group in groups:
                    refs.append(_mod_ref(group.owner, group.owner_jar))
                    refs.append(_mod_ref(group.target.mod, group.target.jar))
                findings.add(Finding(
                    severity=severity, category="dependency",
                    title=f"检测到 {len(groups)} 组{label}",
                    summary=("这些 MOD 在元数据中明确声明了互斥关系，同时安装时加载器会拒绝启动。"
                             if severity == "error" else
                             "这些 MOD 在元数据中被标记为不建议同时安装，可能造成行为异常。"),
                    evidence=evidence,
                    mods=_dedupe_mod_refs(refs),
                    count=len(groups),
                    recommendation="按提示移除其中一方；如果确实需要共存，请寻找兼容补丁或替代方案。",
                ))
        if parse_issues:
            findings.add(Finding(
                severity="notice", category="metadata",
                title="部分互斥版本约束无法解析",
                summary="这些声明没有被计入互斥结果，需要人工检查。",
                evidence=parse_issues[:30],
            ))

    def _check_java(self, active_mods, jars, findings: _PendingFindings) -> None:
        selected_java = self.options.java_version
        if selected_java is None:
            return
        java_mismatch: list[tuple[ModRecord, JarRecord, list[str]]] = []
        for mod, jar in active_mods:
            bad = [
                spec.describe() for spec in mod.java_specs
                if spec.parse_error or not spec.matches(str(selected_java))
            ]
            if bad:
                java_mismatch.append((mod, jar, bad))
        too_new: list[tuple[ModRecord, JarRecord, int]] = []
        for mod, jar in active_mods:
            if jar.max_class_major:
                required = class_version_to_java(jar.max_class_major)
                if required and required > selected_java:
                    too_new.append((mod, jar, required))
        if java_mismatch:
            findings.add(Finding(
                severity="error", category="version",
                title=f"{len(java_mismatch)} 个 MOD 与 Java {selected_java} 不匹配",
                summary="MOD 声明的 Java 版本范围不包含当前目标 Java。",
                evidence=[
                    f"{mod.name}：要求 {' / '.join(ranges)}（{jar.display_name}）"
                    for mod, jar, ranges in java_mismatch[:40]
                ],
                mods=_dedupe_mod_refs(_mod_ref(mod, jar) for mod, jar, _ in java_mismatch),
                count=len(java_mismatch),
                recommendation="在启动器中切换 Java 版本，或替换不兼容的 MOD。",
            ))
        if too_new:
            unique = {(mod.id, jar.key): (mod, jar, required) for mod, jar, required in too_new}
            findings.add(Finding(
                severity="warning", category="version",
                title=f"{len(unique)} 个 MOD 的字节码要求更高的 Java 版本",
                summary=f"这些 JAR 中存在高于 Java {selected_java} 的 class 文件版本，游戏会在加载时抛出 UnsupportedClassVersionError。",
                evidence=[
                    f"{mod.name}：至少需要 Java {required}（{jar.display_name}）"
                    for mod, jar, required in list(unique.values())[:40]
                ],
                mods=_dedupe_mod_refs(_mod_ref(mod, jar) for mod, jar, _ in unique.values()),
                count=len(unique),
                recommendation="把实例 Java 升级到这些 MOD 要求的版本。",
                confidence="medium",
            ))

    def _check_classes(self, jars, class_map, findings: _PendingFindings) -> dict[str, int]:
        jar_by_key = {jar.key: jar for jar in jars}
        active_jar_keys = {
            jar.key for jar in jars if not jar.disabled and jar.recognized
        }
        core_conflicts: list[tuple[str, list[tuple[str, int, int]]]] = []
        library_conflicts: list[tuple[str, list[tuple[str, int, int]]]] = []
        identical: list[tuple[str, list[tuple[str, int, int]]]] = []
        for class_name, occurrences in class_map.items():
            occurrences = [occ for occ in occurrences if occ[0] in active_jar_keys]
            if len({occ[0] for occ in occurrences}) < 2:
                continue
            signatures = {(occ[1], occ[2]) for occ in occurrences}
            target = identical if len(signatures) == 1 else (
                library_conflicts if _is_library_class(class_name) else core_conflicts
            )
            target.append((class_name, occurrences))
        if core_conflicts:
            findings.add(self._class_finding(
                core_conflicts, "error", "核心类",
                "来自不同 MOD 的核心类同名但内容不同。类加载器只能采用其中一个实现，可能导致 NoSuchMethodError、ClassCastException 或直接崩溃。",
                jar_by_key,
            ))
        if library_conflicts:
            findings.add(self._class_finding(
                library_conflicts, "warning", "内嵌库",
                "多个 MOD 打包了同名但不同版本的内嵌库。很多情况下能运行，但当其中一方调用另一版本缺失的 API 时会出错。",
                jar_by_key,
            ))
        if identical:
            findings.add(self._class_finding(
                identical, "notice", "完全相同的类",
                "这些类在多个 MOD 中内容完全相同，通常是重复的内嵌库或重复文件，风险较低。",
                jar_by_key,
            ))
        return {
            "duplicate_class_count": len(core_conflicts) + len(library_conflicts) + len(identical),
            "core_conflicts": len(core_conflicts),
            "library_conflicts": len(library_conflicts),
            "identical_classes": len(identical),
        }

    def _class_finding(self, groups, severity, label, summary, jar_by_key) -> Finding:
        evidence: list[str] = []
        refs: list[dict[str, str]] = []
        for class_name, occurrences in groups[:45]:
            owners = []
            seen = set()
            for key, _, _ in occurrences:
                if key in seen:
                    continue
                seen.add(key)
                jar = jar_by_key.get(key)
                if jar:
                    owners.append(jar.display_name)
                    for mod in jar.mods[:2]:
                        refs.append(_mod_ref(mod, jar))
            shown = " ↔ ".join(owners[:3])
            if len(owners) > 3:
                shown += f" 等 {len(owners)} 个 JAR"
            evidence.append(f"{class_name.replace('/', '.')} — {shown}")
        title = f"发现 {len(groups)} 个{label}冲突"
        if severity == "notice":
            title = f"{len(groups)} 个类在多个 MOD 中内容完全相同"
        return Finding(
            severity=severity, category="class",
            title=title,
            summary=summary,
            detail=("仅比较顶层 class 文件，已忽略 META-INF、多版本 class 和 package-info。"
                    "同名类内容是否相同通过 ZIP CRC 与大小判断。"),
            evidence=evidence,
            mods=_dedupe_mod_refs(refs),
            count=len(groups),
            recommendation=("检查相关 MOD 是否重复打包了同一库；优先升级到使用相同库版本的 MOD。"
                            if severity != "notice" else "通常可以忽略；若同时存在大量重复文件，建议清理重复安装。"),
        )

    def _check_mixin_collisions(self, jars, active_mods, findings: _PendingFindings) -> None:
        active_keys = {
            jar.key for jar in jars
            if not jar.disabled and jar.recognized
        }
        config_owners: dict[str, list[JarRecord]] = defaultdict(list)
        refmap_owners: dict[str, list[JarRecord]] = defaultdict(list)
        for jar in jars:
            if jar.key not in active_keys:
                continue
            for path in jar.mixin_configs:
                config_owners[path.lower()].append(jar)
            for path in jar.refmaps:
                refmap_owners[path.lower()].append(jar)
        collisions: list[tuple[str, str, list[JarRecord]]] = []
        for path, owners in config_owners.items():
            distinct = list({jar.key: jar for jar in owners}.values())
            if len(distinct) > 1:
                collisions.append(("配置", path, distinct))
        for path, owners in refmap_owners.items():
            distinct = list({jar.key: jar for jar in owners}.values())
            if len(distinct) > 1:
                collisions.append(("refmap", path, distinct))
        if not collisions:
            return
        evidence = [
            f"[{kind}] {path} — " + " / ".join(jar.display_name for jar in owners[:5])
            for kind, path, owners in collisions
        ]
        findings.add(Finding(
            severity="warning", category="mixin",
            title=f"{len(collisions)} 个 Mixin 配置或 refmap 文件名冲突",
            summary="多个 MOD 声明了同名 Mixin 资源。类路径资源只有一个名称，后加载/错误加载时可能套用另一份配置或映射表。",
            evidence=evidence[:40],
            count=len(collisions),
            recommendation="优先升级 MOD；若确认文件内容冲突，删除或替换其中一个，并向 MOD 作者反馈。",
            confidence="medium",
        ))

    def _check_namespace_collisions(self, jars, active_mods, findings: _PendingFindings) -> None:
        owners: dict[str, list[JarRecord]] = defaultdict(list)
        for jar in jars:
            if jar.disabled or not jar.recognized:
                continue
            for namespace in jar.asset_namespaces:
                if namespace.lower() in COMMON_ASSET_NAMESPACES:
                    continue
                if not re.match(r"^[a-z0-9_.-]+$", namespace.lower()):
                    continue
                owners[namespace].append(jar)
        collisions = {
            namespace: jars_for_ns for namespace, jars_for_ns in owners.items()
            if len({jar.key for jar in jars_for_ns}) > 1
        }
        if not collisions:
            return
        evidence = [
            f"{namespace} — " + " / ".join(jar.display_name for jar in jars_for_ns[:5])
            for namespace, jars_for_ns in sorted(collisions.items())[:30]
        ]
        findings.add(Finding(
            severity="notice", category="resource",
            title=f"{len(collisions)} 个资源命名空间被多个 MOD 使用",
            summary="多个 MOD 在 assets/<namespace> 下放入资源；这可能是兼容补丁，也可能造成贴图、模型或语言文件互相覆盖。",
            evidence=evidence,
            mods=_dedupe_mod_refs(
                _mod_ref(mod, jar)
                for jar in {jar.key: jar for namespace, jars_for_ns in collisions.items()
                           for jar in jars_for_ns}.values()
                for mod in jar.mods[:2]
            ),
            count=len(collisions),
            confidence="low",
            recommendation="如果游戏内出现材质或模型异常，优先检查这些命名空间重叠的 MOD。",
        ))

    def _check_duplicate_files(self, jars, active_mods, findings: _PendingFindings) -> None:
        by_hash: dict[str, list[JarRecord]] = defaultdict(list)
        for jar in jars:
            if jar.valid and not jar.disabled and jar.recognized and jar.sha256:
                by_hash[jar.sha256].append(jar)
        duplicates = [group for group in by_hash.values() if len(group) > 1]
        if not duplicates:
            return
        evidence = []
        refs = []
        for group in duplicates[:30]:
            evidence.append(" = ".join(jar.display_name for jar in group))
            for jar in group:
                for mod in jar.mods[:2]:
                    refs.append(_mod_ref(mod, jar))
        findings.add(Finding(
            severity="warning", category="duplicate",
            title=f"{len(duplicates)} 组完全相同的 JAR 副本",
            summary="这些文件内容完全一致，只是文件名或所在路径不同；会重复占用内存，并让加载器看到重复 MOD。",
            evidence=evidence,
            mods=_dedupe_mod_refs(refs),
            count=len(duplicates),
            recommendation="每组只保留一个文件，建议使用更清晰的版本命名。",
        ))

    # ------------------------------------------------------------------
    # Dependency helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _provided_entries(mod: ModRecord) -> list[tuple[str, str]]:
        entries: list[tuple[str, str]] = []
        seen: set[str] = set()
        own_id = canonical_id(mod.id)
        if own_id:
            entries.append((own_id, mod.version))
            seen.add(own_id)
        for provided in mod.provides:
            pid = canonical_id(provided.id)
            if not pid or pid in seen:
                continue
            entries.append((pid, provided.version or mod.version))
            seen.add(pid)
        return entries

    @staticmethod
    def _requirement_waived(requirement, providers) -> bool:
        if not requirement.unless:
            return False
        return Analyzer._unless_satisfied(requirement.unless, providers)

    @staticmethod
    def _unless_satisfied(alternatives, providers) -> bool:
        for alt in alternatives:
            for provider in providers.get(canonical_id(alt.id), []):
                if _spec_matches(alt.spec, provider.version) is True:
                    return True
        return False

    # ------------------------------------------------------------------
    # Reporting helpers
    # ------------------------------------------------------------------
    def _effective_loader(self, counts: Counter) -> str:
        families = Counter()
        for loader, count in counts.items():
            family = LOADER_FAMILIES.get(loader, loader)
            if family == "unknown":
                continue
            families[family] += count
        if not families:
            return "unknown"
        if len(families) == 1:
            family = next(iter(families))
            return {
                "fabric": "fabric",
                "forge": "forge",
                "neoforge": "neoforge",
                "rift": "rift",
                "liteloader": "liteloader",
            }.get(family, family)
        return "mixed"

    def _serialize_mods(self, active, disabled, nested, jars) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str, str]] = set()
        for group, status in ((active, "active"), (disabled, "disabled"), (nested, "nested")):
            for mod, jar in group:
                key = (status, jar.key, mod.id, mod.version)
                if key in seen:
                    continue
                seen.add(key)
                java_required = class_version_to_java(jar.max_class_major or 0) if jar.max_class_major else None
                requirement_ids = sorted({
                    alt.id for requirement in mod.requirements
                    for alt in requirement.alternatives
                })
                result.append({
                    "key": f"{jar.key}:{mod.id}:{mod.version}:{status}",
                    "id": mod.id,
                    "name": mod.name or mod.id,
                    "version": mod.version,
                    "status": status,
                    "loaders": sorted(mod.loaders),
                    "loader": mod.primary_loader,
                    "loader_label": loader_label(mod.primary_loader),
                    "file": jar.display_name,
                    "parent_file": mod.parent_file,
                    "file_size": jar.size,
                    "metadata_path": mod.metadata_path,
                    "metadata_format": mod.metadata_format,
                    "environment": mod.environment,
                    "description": mod.description,
                    "authors": mod.authors[:8],
                    "license": mod.license,
                    "mc_constraints": [spec.describe() for spec in mod.mc_specs],
                    "java_required": java_required,
                    "class_major": jar.max_class_major,
                    "requires": requirement_ids,
                    "provides": [provided.id for provided in mod.provides],
                    "mixin_configs": mod.mixin_configs[:12],
                    "nested_count": len(jar.nested_mods),
                    "coremod": mod.coremod,
                    "inferred": bool(mod.inference_note),
                    "inference_note": mod.inference_note,
                    "issues": mod.issues[:8],
                })
        return result

    @staticmethod
    def _serialize_jar(jar: JarRecord) -> dict[str, Any]:
        return {
            "file": jar.display_name,
            "size": jar.size,
            "sha256": jar.sha256,
            "disabled": jar.disabled,
            "valid": jar.valid,
            "recognized": jar.recognized,
            "loaders": sorted(jar.loaders),
            "mods": [mod.name or mod.id for mod in jar.mods],
            "nested_mods": [mod.name or mod.id for mod in jar.nested_mods],
            "class_count": len(jar.class_names),
            "entry_count": jar.entry_count,
            "resource_count": jar.resource_count,
            "java_required": (
                class_version_to_java(jar.max_class_major) if jar.max_class_major else None
            ),
            "class_major": jar.max_class_major,
            "mixin_configs": sorted(jar.mixin_configs.keys()),
            "issues": list(dict.fromkeys(jar.issues))[:30],
            "coremod_markers": jar.coremod_markers,
        }

    def _order_findings(self, findings: list[Finding]) -> list[Finding]:
        ordered = sorted(
            findings,
            key=lambda f: (
                SEVERITY_ORDER.get(f.severity, 9),
                CATEGORY_ORDER.get(f.category, 99),
                -f.count,
                f.title,
            ),
        )
        for index, finding in enumerate(ordered, start=1):
            finding.id = f"F{index:03d}"
        return ordered

    @staticmethod
    def _health_score(findings: list[Finding]) -> int:
        errors = sum(1 for f in findings if f.severity == "error")
        warnings = sum(1 for f in findings if f.severity == "warning")
        notices = sum(1 for f in findings if f.severity == "notice")
        score = 100 - errors * 28 - warnings * 8 - notices * 1
        if errors:
            score = min(score, 69)
        return max(0, min(100, score))

    @staticmethod
    def _grade(score: int, counts: Counter) -> tuple[str, str]:
        if counts.get("error"):
            if score < 35:
                return "critical", "建议先修复再启动"
            return "risk", "存在必须处理的问题"
        if counts.get("warning"):
            return "attention", "有几处需要关注"
        if counts.get("notice"):
            return "good", "仅有补充提示"
        return "clear", "没有发现明显冲突"

    @staticmethod
    def _summary_label(findings: list[Finding], counts: Counter) -> str:
        errors = counts.get("error", 0)
        warnings = counts.get("warning", 0)
        notices = counts.get("notice", 0)
        if not findings:
            return "没有发现需要处理的冲突。"
        parts = []
        if errors:
            parts.append(f"{errors} 个必须处理的问题")
        if warnings:
            parts.append(f"{warnings} 个需要注意的问题")
        if notices:
            parts.append(f"{notices} 条补充提示")
        return "发现 " + "、".join(parts) + "。"


def _is_library_class(class_name: str) -> bool:
    lower = class_name.lower()
    return any(lower.startswith(prefix) for prefix in LIBRARY_PREFIXES)


__all__ = [
    "APP_VERSION",
    "Analyzer",
    "ScanError",
    "discover_path_files",
    "is_mod_filename",
    "normalize_mc_version",
    "validate_options",
]
