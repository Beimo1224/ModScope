"""Data structures shared by the metadata reader, analyzer and web server."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .versions import VersionSpec


LOADER_LABELS = {
    "fabric": "Fabric",
    "quilt": "Quilt",
    "forge": "Forge",
    "legacyforge": "Legacy Forge",
    "neoforge": "NeoForge",
    "liteloader": "LiteLoader",
    "rift": "Rift",
    "bukkit": "Bukkit / Paper",
    "unknown": "未知",
}

LOADER_FAMILIES = {
    "fabric": "fabric",
    "quilt": "fabric",
    "forge": "forge",
    "legacyforge": "forge",
    "neoforge": "neoforge",
    "liteloader": "liteloader",
    "rift": "rift",
    "bukkit": "bukkit",
}


@dataclass
class ProvidedMod:
    id: str
    version: str | None = None
    raw: str = ""
    group: str | None = None


@dataclass
class DependencyAlternative:
    id: str
    spec: VersionSpec
    raw: str = ""
    group: str | None = None


@dataclass
class Requirement:
    alternatives: list[DependencyAlternative]
    kind: str = "required"  # required / optional
    reason: str = ""
    side: str | None = None
    unless: list[DependencyAlternative] = field(default_factory=list)
    source: str = ""
    raw: object = None

    def display_ids(self) -> str:
        return " / ".join(alt.id for alt in self.alternatives)


@dataclass
class Incompatibility:
    id: str
    spec: VersionSpec
    severity: str = "error"  # error / warning
    reason: str = ""
    side: str | None = None
    unless: list[DependencyAlternative] = field(default_factory=list)
    source: str = ""
    raw: object = None


@dataclass
class ModRecord:
    id: str
    name: str
    version: str = ""
    loaders: set[str] = field(default_factory=set)
    metadata_path: str = ""
    metadata_format: str = ""
    description: str = ""
    authors: list[str] = field(default_factory=list)
    license: str = ""
    environment: str = "*"
    group: str | None = None
    provides: list[ProvidedMod] = field(default_factory=list)
    requirements: list[Requirement] = field(default_factory=list)
    incompatibilities: list[Incompatibility] = field(default_factory=list)
    mc_specs: list[VersionSpec] = field(default_factory=list)
    java_specs: list[VersionSpec] = field(default_factory=list)
    loader_specs: list[VersionSpec] = field(default_factory=list)
    mixin_configs: list[str] = field(default_factory=list)
    access_wideners: list[str] = field(default_factory=list)
    nested_jars: list[str] = field(default_factory=list)
    nested: bool = False
    parent_file: str | None = None
    coremod: bool = False
    inference_note: str = ""
    issues: list[str] = field(default_factory=list)

    def ensure_loader(self, loader: str) -> None:
        if loader:
            self.loaders.add(loader)

    @property
    def primary_loader(self) -> str:
        order = ["neoforge", "forge", "legacyforge", "quilt", "fabric",
                 "liteloader", "rift", "bukkit", "unknown"]
        for loader in order:
            if loader in self.loaders:
                return loader
        return next(iter(self.loaders), "unknown")

    def add_requirement(self, requirement: Requirement) -> None:
        self.requirements.append(requirement)

    def add_incompatibility(self, incompatibility: Incompatibility) -> None:
        self.incompatibilities.append(incompatibility)


@dataclass
class ClassOccurrence:
    jar_key: str
    crc: int
    size: int


@dataclass
class JarRecord:
    key: str
    path: str
    display_name: str
    size: int = 0
    sha256: str = ""
    disabled: bool = False
    valid: bool = True
    error: str = ""
    loaders: set[str] = field(default_factory=set)
    metadata_formats: set[str] = field(default_factory=set)
    mods: list[ModRecord] = field(default_factory=list)
    nested_mods: list[ModRecord] = field(default_factory=list)
    class_names: set[str] = field(default_factory=set)
    asset_namespaces: set[str] = field(default_factory=set)
    data_namespaces: set[str] = field(default_factory=set)
    mixin_configs: dict[str, int] = field(default_factory=dict)
    mixin_meta: dict[str, dict[str, Any]] = field(default_factory=dict)
    refmaps: dict[str, int] = field(default_factory=dict)
    access_transformers: dict[str, int] = field(default_factory=dict)
    manifest: dict[str, str] = field(default_factory=dict)
    max_class_major: int | None = None
    issues: list[str] = field(default_factory=list)
    coremod_markers: list[str] = field(default_factory=list)
    entry_count: int = 0
    resource_count: int = 0
    recognized: bool = False

    @property
    def active_mods(self) -> list[ModRecord]:
        return [mod for mod in self.mods if not mod.nested]

    def mods_label(self) -> str:
        if not self.mods:
            return "未识别"
        return " / ".join(mod.name or mod.id for mod in self.mods[:3])

    def plus_more_label(self) -> str:
        if len(self.mods) <= 3:
            return self.mods_label()
        return f"{self.mods_label()} +{len(self.mods) - 3}"


@dataclass
class ScanOptions:
    mc_version: str = ""
    loader: str = "auto"
    side: str = "client"
    java_version: int | None = None
    deep_scan: bool = True
    include_disabled: bool = True
    source_kind: str = "path"
    source_label: str = ""
    recursive: bool = False


@dataclass
class FileRef:
    """A file selected for scanning, before its JAR metadata is read."""

    path: str
    display_name: str
    disabled: bool = False
    relative_name: str | None = None


@dataclass
class Finding:
    severity: str
    category: str
    title: str
    summary: str
    detail: str = ""
    recommendation: str = ""
    mods: list[dict[str, str]] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    count: int = 0
    confidence: str = "high"
    id: str = ""

    def to_dict(self) -> dict[str, Any]:
        evidence: list[str] = []
        seen_evidence: set[str] = set()
        for item in self.evidence:
            if item in seen_evidence:
                continue
            seen_evidence.add(item)
            evidence.append(item)
        mods: list[dict[str, str]] = []
        seen_mods: set[tuple[str, str, str]] = set()
        for mod in self.mods:
            key = (str(mod.get("id", "")), str(mod.get("file", "")), str(mod.get("version", "")))
            if key in seen_mods:
                continue
            seen_mods.add(key)
            mods.append(mod)
        return {
            "id": self.id,
            "severity": self.severity,
            "category": self.category,
            "title": self.title,
            "summary": self.summary,
            "detail": self.detail,
            "recommendation": self.recommendation,
            "mods": mods,
            "evidence": evidence,
            "count": self.count,
            "confidence": self.confidence,
        }
