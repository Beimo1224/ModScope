"""Read mod metadata from JARs of every major Minecraft mod-loading era."""

from __future__ import annotations

from copy import deepcopy
import io
import json
import re
import tomllib
import zipfile
from typing import Any, Iterable

from .models import (
    DependencyAlternative,
    Incompatibility,
    JarRecord,
    ModRecord,
    ProvidedMod,
    Requirement,
)
from .versions import (
    VersionSpec,
    parse_fabric_predicate,
    parse_loose_constraint,
    parse_maven_range,
    parse_quilt_spec,
)


MAX_METADATA_BYTES = 4 * 1024 * 1024
MAX_NESTED_JAR_BYTES = 128 * 1024 * 1024

PLATFORM_IDS = {
    "minecraft", "forge", "neoforge", "fabricloader", "fabric_loader",
    "quiltloader", "quilt_loader", "liteloader", "java", "javafml",
    "lowcodefml", "fml", "rift",
}


def canonical_id(value: object) -> str:
    return str(value or "").strip().lower()


def normalize_dep_id(value: object) -> str:
    """Quilt permits ``group:id``; the loader identity is normally the id."""
    text = str(value or "").strip()
    if ":" in text:
        group, _, ident = text.partition(":")
        return ident.strip() or text
    return text


def _short(value: object, limit: int = 700) -> str:
    text = " ".join(str(value or "").split())
    if len(text) > limit:
        return text[: limit - 1].rstrip() + "…"
    return text


def _as_str_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [part.strip() for part in re.split(r"\s*,\s*", value) if part.strip()]
    if isinstance(value, (list, tuple, set)):
        result: list[str] = []
        for item in value:
            if isinstance(item, str):
                if item.strip():
                    result.append(item.strip())
            elif isinstance(item, dict):
                name = item.get("name") or item.get("id")
                if name:
                    result.append(str(name).strip())
        return result
    if isinstance(value, dict):
        return [str(key) for key in value.keys()]
    return [str(value)]


def _side(value: object) -> str | None:
    text = canonical_id(value)
    if text in {"client", "physical_client"}:
        return "client"
    if text in {"server", "dedicated_server", "physical_server"}:
        return "server"
    return None


def _safe_json_loads(text: str) -> Any:
    return json.loads(_strip_json_comments(text))


def _strip_json_comments(text: str) -> str:
    """Allow common JSONC features found in Mixin configs and old metadata."""
    output: list[str] = []
    i = 0
    in_string = False
    escaped = False
    while i < len(text):
        char = text[i]
        if in_string:
            output.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            i += 1
            continue
        if char == '"':
            in_string = True
            output.append(char)
            i += 1
            continue
        if char == "/" and i + 1 < len(text):
            next_char = text[i + 1]
            if next_char == "/":
                i += 2
                while i < len(text) and text[i] not in "\r\n":
                    i += 1
                continue
            if next_char == "*":
                i += 2
                while i + 1 < len(text) and not (text[i] == "*" and text[i + 1] == "/"):
                    if text[i] in "\r\n":
                        output.append(text[i])
                    i += 1
                i += 2
                continue
        output.append(char)
        i += 1
    cleaned = "".join(output)
    # Remove trailing commas before a closing object/array.
    return re.sub(r",(\s*[}\]])", r"\1", cleaned)


def _parse_requirement_entry(
    entry: Any,
    *,
    source: str,
    loader: str,
    kind: str = "required",
) -> Requirement | None:
    """Parse a Fabric/Quilt-style dependency object or an alternative array."""
    if entry is None:
        return None
    if isinstance(entry, list):
        alternatives: list[DependencyAlternative] = []
        for item in entry:
            parsed = _parse_requirement_entry(item, source=source, loader=loader, kind=kind)
            if parsed:
                alternatives.extend(parsed.alternatives)
        if not alternatives:
            return None
        return Requirement(alternatives, kind=kind, source=source, raw=entry)
    if isinstance(entry, str):
        dep_id = normalize_dep_id(entry)
        if not dep_id:
            return None
        return Requirement(
            [DependencyAlternative(dep_id, VersionSpec.any("*"), entry)],
            kind=kind,
            source=source,
            raw=entry,
        )
    if not isinstance(entry, dict):
        return None
    dep_id = normalize_dep_id(entry.get("id") or entry.get("modId") or entry.get("modid"))
    if not dep_id:
        return None
    version_value = entry.get("versions", entry.get("version", "*"))
    if loader == "quilt":
        spec = parse_quilt_spec(version_value)
    else:
        spec = parse_fabric_predicate(version_value if isinstance(version_value, str)
                                      else "*")
    if version_value is None:
        spec = VersionSpec.any("*")
    reason = _short(entry.get("reason") or entry.get("description") or "", 300)
    optional = bool(entry.get("optional", False)) or kind == "optional"
    unless_values: list[DependencyAlternative] = []
    unless_value = entry.get("unless")
    if unless_value is not None:
        unless_req = _parse_requirement_entry(
            unless_value, source=source, loader=loader, kind="optional"
        )
        if unless_req:
            unless_values.extend(unless_req.alternatives)
    return Requirement(
        [DependencyAlternative(dep_id, spec, str(version_value), entry.get("group"))],
        kind="optional" if optional else "required",
        reason=reason,
        side=_side(entry.get("environment")) or _side(entry.get("side")),
        unless=unless_values,
        source=source,
        raw=entry,
    )


def _parse_incompatibility_entry(
    entry: Any,
    *,
    source: str,
    loader: str,
    severity: str = "error",
    array_semantics_all: bool = False,
) -> list[Incompatibility]:
    results: list[Incompatibility] = []
    if entry is None:
        return results
    if isinstance(entry, list):
        if array_semantics_all:
            for item in entry:
                results.extend(_parse_incompatibility_entry(
                    item, source=source, loader=loader, severity=severity,
                    array_semantics_all=False,
                ))
            return results
        # Fabric-style arrays are not common, but treat them as alternatives
        # by preserving all entries; each is still an incompatibility.
        for item in entry:
            results.extend(_parse_incompatibility_entry(
                item, source=source, loader=loader, severity=severity,
                array_semantics_all=False,
            ))
        return results
    if isinstance(entry, str):
        dep_id = normalize_dep_id(entry)
        if dep_id:
            results.append(Incompatibility(dep_id, VersionSpec.any("*"), severity, source=source))
        return results
    if not isinstance(entry, dict):
        return results
    dep_id = normalize_dep_id(entry.get("id") or entry.get("modId") or entry.get("modid"))
    if not dep_id:
        return results
    version_value = entry.get("versions", entry.get("version", "*"))
    if loader == "quilt":
        spec = parse_quilt_spec(version_value)
    else:
        spec = parse_fabric_predicate(version_value if isinstance(version_value, str) else "*")
    unless: list[DependencyAlternative] = []
    unless_value = entry.get("unless")
    if unless_value is not None:
        parsed = _parse_requirement_entry(unless_value, source=source, loader=loader)
        if parsed:
            unless.extend(parsed.alternatives)
    results.append(Incompatibility(
        dep_id,
        spec,
        severity=severity,
        reason=_short(entry.get("reason") or "", 300),
        side=_side(entry.get("environment")) or _side(entry.get("side")),
        unless=unless,
        source=source,
        raw=entry,
    ))
    return results


def _new_mod(
    mod_id: object,
    name: object,
    version: object,
    *,
    loader: str,
    metadata_path: str,
    metadata_format: str,
) -> ModRecord:
    identifier = canonical_id(mod_id)
    return ModRecord(
        id=identifier,
        name=_short(name or identifier, 120),
        version=_short(version or "", 100),
        loaders={loader} if loader else set(),
        metadata_path=metadata_path,
        metadata_format=metadata_format,
    )


def _parse_mixin_paths(value: Any) -> list[str]:
    paths: list[str] = []
    if value is None:
        return paths
    if isinstance(value, str):
        return [value.strip()]
    if isinstance(value, dict):
        config = value.get("config") or value.get("file")
        if config:
            paths.append(str(config).strip())
        return paths
    if isinstance(value, (list, tuple)):
        for item in value:
            paths.extend(_parse_mixin_paths(item))
    return paths


def _parse_access_widener_paths(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()]
    if isinstance(value, (list, tuple)):
        result: list[str] = []
        for item in value:
            result.extend(_parse_access_widener_paths(item))
        return result
    if isinstance(value, dict):
        path = value.get("file") or value.get("path")
        return [str(path)] if path else []
    return []


def _parse_provides(value: Any, *, loader: str) -> list[ProvidedMod]:
    result: list[ProvidedMod] = []
    if value is None:
        return result
    if isinstance(value, str):
        value = [value]
    if isinstance(value, dict):
        value = [value]
    if not isinstance(value, (list, tuple)):
        return result
    for item in value:
        if isinstance(item, str):
            raw_id = normalize_dep_id(item)
            if raw_id:
                result.append(ProvidedMod(raw_id, None, item))
        elif isinstance(item, dict):
            raw_id = normalize_dep_id(item.get("id") or item.get("modId"))
            if raw_id:
                version = item.get("version")
                group = item.get("group")
                result.append(ProvidedMod(raw_id, str(version) if version else None,
                                          str(item), group))
    return result


def _parse_authors(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return _as_str_list(value)
    if isinstance(value, (list, tuple)):
        names: list[str] = []
        for item in value:
            if isinstance(item, str):
                names.append(item)
            elif isinstance(item, dict):
                name = item.get("name") or item.get("id")
                if name:
                    names.append(str(name))
        return names
    if isinstance(value, dict):
        return [str(key) for key in value]
    return [str(value)]


def _add_platform_spec(mod: ModRecord, dep_id: str, spec: VersionSpec) -> None:
    key = canonical_id(dep_id)
    if key == "minecraft":
        mod.mc_specs.append(spec)
    elif key == "java":
        mod.java_specs.append(spec)
    elif key in {"forge", "neoforge", "fabricloader", "fabric_loader", "quiltloader",
                 "quilt_loader", "liteloader", "rift", "fml", "javafml", "lowcodefml"}:
        mod.loader_specs.append(spec)


# ---------------------------------------------------------------------------
# Fabric
# ---------------------------------------------------------------------------


def parse_fabric_metadata(data: dict[str, Any], metadata_path: str) -> list[ModRecord]:
    mod = _new_mod(
        data.get("id"), data.get("name") or data.get("id"), data.get("version"),
        loader="fabric", metadata_path=metadata_path, metadata_format="fabric.mod.json",
    )
    if not mod.id:
        return []
    schema = data.get("schemaVersion")
    if schema not in (None, 1, "1"):
        mod.issues.append(f"未知的 fabric.mod.json schemaVersion：{schema}")
    mod.description = _short(data.get("description"), 700)
    mod.authors = _parse_authors(data.get("authors"))
    mod.license = _short(data.get("license"), 120)
    environment = str(data.get("environment") or "*").strip()
    mod.environment = "server" if environment == "server" else ("client" if environment == "client" else "*")
    mod.mixin_configs.extend(_parse_mixin_paths(data.get("mixins")))
    mod.access_wideners.extend(_parse_access_widener_paths(data.get("accessWidener")))
    declared_jars = data.get("jars")
    if isinstance(declared_jars, list):
        for item in declared_jars:
            if isinstance(item, dict):
                path = item.get("file") or item.get("path")
                if path:
                    mod.nested_jars.append(str(path))
            elif isinstance(item, str):
                mod.nested_jars.append(item)

    depends = data.get("depends")
    if isinstance(depends, dict):
        for dep_id, version_spec in depends.items():
            spec = _fabric_dep_spec(version_spec)
            _add_platform_spec(mod, dep_id, spec)
            if canonical_id(dep_id) in {"minecraft", "java"} or canonical_id(dep_id) in PLATFORM_IDS:
                continue
            mod.add_requirement(Requirement(
                [DependencyAlternative(str(dep_id), spec, str(version_spec))],
                source="fabric.mod.json#depends",
                raw=depends,
            ))
    for key, severity in (("breaks", "error"), ("conflicts", "error")):
        mapping = data.get(key)
        if isinstance(mapping, dict):
            for dep_id, version_spec in mapping.items():
                spec = _fabric_dep_spec(version_spec)
                mod.add_incompatibility(Incompatibility(
                    str(dep_id), spec, severity=severity,
                    source=f"fabric.mod.json#{key}", raw=mapping,
                ))
    mod.provides.extend(_parse_provides(data.get("provides"), loader="fabric"))
    return [mod]


def _fabric_dep_spec(value: Any) -> VersionSpec:
    if value is None:
        return VersionSpec.any("*")
    if isinstance(value, list):
        result = VersionSpec([], " | ".join(map(str, value)))
        for item in value:
            parsed = parse_fabric_predicate(str(item))
            if parsed.parse_error:
                return parsed
            result.intervals.extend(parsed.intervals)
        return result if result.intervals else VersionSpec.any("*")
    return parse_fabric_predicate(str(value))


# ---------------------------------------------------------------------------
# Quilt
# ---------------------------------------------------------------------------


def parse_quilt_metadata(data: dict[str, Any], metadata_path: str) -> list[ModRecord]:
    loader_data = data.get("quilt_loader")
    if not isinstance(loader_data, dict):
        return []
    mod = _new_mod(
        loader_data.get("id"), loader_data.get("metadata", {}).get("name")
        if isinstance(loader_data.get("metadata"), dict) else loader_data.get("id"),
        loader_data.get("version"),
        loader="quilt", metadata_path=metadata_path, metadata_format="quilt.mod.json",
    )
    if not mod.id:
        return []
    mod.group = canonical_id(loader_data.get("group")) or None
    schema = data.get("schema_version")
    if schema not in (None, 1, "1"):
        mod.issues.append(f"未知的 quilt.mod.json schema_version：{schema}")
    metadata = loader_data.get("metadata") if isinstance(loader_data.get("metadata"), dict) else {}
    mod.description = _short(metadata.get("description"), 700)
    contributors = metadata.get("contributors")
    if isinstance(contributors, dict):
        mod.authors = [str(name) for name in contributors.keys()]
    elif isinstance(contributors, list):
        mod.authors = _parse_authors(contributors)
    license_value = metadata.get("license")
    if isinstance(license_value, list):
        mod.license = ", ".join(str(v) for v in license_value)
    elif isinstance(license_value, dict):
        mod.license = _short(license_value.get("id") or license_value.get("name"), 120)
    else:
        mod.license = _short(license_value, 120)
    minecraft_data = data.get("minecraft")
    if isinstance(minecraft_data, dict):
        mod.environment = "server" if minecraft_data.get("environment") == "dedicated_server" else (
            "client" if minecraft_data.get("environment") == "client" else "*"
        )
    mod.mixin_configs.extend(_parse_mixin_paths(data.get("mixin")))
    mod.access_wideners.extend(_parse_access_widener_paths(data.get("access_widener")))
    jars = loader_data.get("jars")
    if isinstance(jars, list):
        for item in jars:
            if isinstance(item, str):
                mod.nested_jars.append(item)
            elif isinstance(item, dict):
                path = item.get("path") or item.get("file")
                if path:
                    mod.nested_jars.append(str(path))
    depends = loader_data.get("depends")
    if isinstance(depends, list):
        for entry in depends:
            req = _parse_requirement_entry(entry, source="quilt.mod.json#depends", loader="quilt")
            if req:
                for alt in req.alternatives:
                    _add_platform_spec(mod, alt.id, alt.spec)
                if all(canonical_id(alt.id) in PLATFORM_IDS for alt in req.alternatives):
                    continue
                mod.add_requirement(req)
    breaks = loader_data.get("breaks")
    if isinstance(breaks, list):
        for entry in breaks:
            for incompatibility in _parse_incompatibility_entry(
                entry, source="quilt.mod.json#breaks", loader="quilt",
                array_semantics_all=True,
            ):
                mod.add_incompatibility(incompatibility)
    mod.provides.extend(_parse_provides(loader_data.get("provides"), loader="quilt"))
    return [mod]


# ---------------------------------------------------------------------------
# Forge / NeoForge mods.toml
# ---------------------------------------------------------------------------


def _replace_toml_placeholders(value: Any, manifest: dict[str, str],
                               root_properties: dict[str, Any]) -> Any:
    if not isinstance(value, str):
        return value
    replacements = {
        "file.jarVersion": manifest.get("implementation-version", ""),
    }
    for key, prop in root_properties.items():
        replacements[f"file.{key}"] = str(prop)
    result = value
    for placeholder, replacement in replacements.items():
        result = result.replace("${" + placeholder + "}", replacement or "")
    return result


def parse_forge_toml(data: dict[str, Any], metadata_path: str, manifest: dict[str, str],
                     *, assume_loader: str | None = None) -> list[ModRecord]:
    raw_mods = data.get("mods")
    if isinstance(raw_mods, dict):
        raw_mods = [raw_mods]
    if not isinstance(raw_mods, list):
        return []
    dependencies_root = data.get("dependencies")
    if not isinstance(dependencies_root, dict):
        dependencies_root = {}
    root_properties = data.get("properties") if isinstance(data.get("properties"), dict) else {}
    license_value = data.get("license")
    is_neoforge = metadata_path.lower().endswith("neoforge.mods.toml")
    if not is_neoforge:
        for dep_list in dependencies_root.values():
            if isinstance(dep_list, list) and any(
                canonical_id(dep.get("modId")) == "neoforge"
                for dep in dep_list if isinstance(dep, dict)
            ):
                is_neoforge = True
                break
    loader = assume_loader or ("neoforge" if is_neoforge else "forge")
    mixin_paths: list[str] = []
    raw_mixins = data.get("mixins")
    if isinstance(raw_mixins, list):
        mixin_paths.extend(_parse_mixin_paths(raw_mixins))
    elif isinstance(raw_mixins, dict):
        mixin_paths.extend(_parse_mixin_paths(raw_mixins))
    at_paths: list[str] = []
    raw_ats = data.get("accessTransformers")
    if isinstance(raw_ats, list):
        for item in raw_ats:
            if isinstance(item, dict):
                path = item.get("file") or item.get("path")
                if path:
                    at_paths.append(str(path))
            elif isinstance(item, str):
                at_paths.append(item)
    elif isinstance(raw_ats, dict):
        path = raw_ats.get("file") or raw_ats.get("path")
        if path:
            at_paths.append(str(path))

    results: list[ModRecord] = []
    for raw_mod in raw_mods:
        if not isinstance(raw_mod, dict):
            continue
        mod_id = canonical_id(raw_mod.get("modId") or raw_mod.get("modid") or raw_mod.get("id"))
        if not mod_id:
            continue
        version = _replace_toml_placeholders(
            raw_mod.get("version", ""), manifest, root_properties
        )
        display = raw_mod.get("displayName") or raw_mod.get("name") or mod_id
        mod = _new_mod(
            mod_id, display, version, loader=loader,
            metadata_path=metadata_path,
            metadata_format="neoforge.mods.toml" if is_neoforge else "mods.toml",
        )
        mod.description = _short(raw_mod.get("description"), 700)
        mod.authors = _parse_authors(raw_mod.get("authors"))
        mod.license = _short(license_value, 120)
        if data.get("clientSideOnly") is True:
            mod.environment = "client"
        if data.get("loaderVersion"):
            mod.loader_specs.append(parse_maven_range(str(data.get("loaderVersion"))))
        root_mc = (
            data.get("acceptedMinecraftVersions")
            or data.get("acceptableMinecraftVersions")
            or data.get("mcversion")
        )
        if root_mc:
            values = root_mc if isinstance(root_mc, (list, tuple)) else [root_mc]
            for value in values:
                mod.mc_specs.append(parse_loose_constraint(value, loader="forge"))
        for path in mixin_paths:
            if path not in mod.mixin_configs:
                mod.mixin_configs.append(path)
        mod.access_wideners.extend(at_paths)
        if raw_mod.get("features") and isinstance(raw_mod.get("features"), dict):
            java_spec = raw_mod["features"].get("java_version")
            if java_spec:
                mod.java_specs.append(parse_maven_range(str(java_spec)))

        dep_lists: list[dict[str, Any]] = []
        for key, value in dependencies_root.items():
            if canonical_id(key) == mod_id and isinstance(value, list):
                dep_lists.extend(item for item in value if isinstance(item, dict))
        for dep in dep_lists:
            dep_id = canonical_id(dep.get("modId") or dep.get("modid") or dep.get("id"))
            if not dep_id:
                continue
            spec = parse_maven_range(str(dep.get("versionRange") or "*"))
            dep_type = canonical_id(dep.get("type"))
            mandatory = dep.get("mandatory")
            if dep_type:
                kind = {
                    "required": "required",
                    "optional": "optional",
                    "incompatible": "incompatible",
                    "discouraged": "discouraged",
                }.get(dep_type, "required" if mandatory is not False else "optional")
            else:
                kind = "required" if mandatory is not False else "optional"
            side = _side(dep.get("side"))
            if dep_id == "minecraft":
                mod.mc_specs.append(spec)
                continue
            if dep_id == "java":
                mod.java_specs.append(spec)
                continue
            if dep_id in PLATFORM_IDS:
                mod.loader_specs.append(spec)
                continue
            if kind in {"incompatible", "discouraged"}:
                mod.add_incompatibility(Incompatibility(
                    dep_id, spec,
                    severity="error" if kind == "incompatible" else "warning",
                    reason=_short(dep.get("reason") or "", 300),
                    side=side,
                    source=f"{metadata_path}#dependencies.{mod_id}",
                    raw=dep,
                ))
            else:
                mod.add_requirement(Requirement(
                    [DependencyAlternative(dep_id, spec, str(dep.get("versionRange") or "*"))],
                    kind=kind,
                    side=side,
                    source=f"{metadata_path}#dependencies.{mod_id}",
                    raw=dep,
                ))
        results.append(mod)
    return results


# ---------------------------------------------------------------------------
# Legacy Forge mcmod.info
# ---------------------------------------------------------------------------


def parse_mcmod_info(data: Any, metadata_path: str, manifest: dict[str, str]) -> list[ModRecord]:
    entries: list[dict[str, Any]] = []
    if isinstance(data, list):
        entries = [entry for entry in data if isinstance(entry, dict)]
    elif isinstance(data, dict):
        nested = data.get("modList") or data.get("mods")
        if isinstance(nested, list):
            entries = [entry for entry in nested if isinstance(entry, dict)]
        elif any(key in data for key in ("modid", "modId", "name")):
            entries = [data]
    results: list[ModRecord] = []
    for entry in entries:
        mod_id = canonical_id(entry.get("modid") or entry.get("modId") or entry.get("id"))
        if not mod_id:
            continue
        mod = _new_mod(
            mod_id, entry.get("name") or mod_id, entry.get("version", ""),
            loader="legacyforge", metadata_path=metadata_path,
            metadata_format="mcmod.info",
        )
        mod.description = _short(entry.get("description"), 700)
        mod.authors = _parse_authors(entry.get("authorList") or entry.get("authors"))
        mod.license = _short(entry.get("license"), 120)
        if entry.get("clientSideOnly") is True:
            mod.environment = "client"
        mcversion = entry.get("mcversion")
        if mcversion:
            for value in (mcversion if isinstance(mcversion, list) else [mcversion]):
                mod.mc_specs.append(parse_loose_constraint(value, loader="forge"))
        acceptable = entry.get("acceptableMinecraftVersions") or entry.get("acceptableMinecraftVersion")
        if acceptable:
            mod.mc_specs.extend(_parse_legacy_mc_constraints(acceptable))
        dependencies = entry.get("dependencies")
        if isinstance(dependencies, str):
            dependencies = [dependencies]
        if isinstance(dependencies, list):
            for dep in dependencies:
                parsed = _parse_legacy_dependency(dep, "mcmod.info#dependencies")
                if parsed:
                    for alternative in parsed.alternatives:
                        if canonical_id(alternative.id) in PLATFORM_IDS:
                            _add_platform_spec(mod, alternative.id, alternative.spec)
                    if all(canonical_id(alt.id) in PLATFORM_IDS for alt in parsed.alternatives):
                        continue
                    mod.add_requirement(parsed)
        parent = entry.get("parent")
        if isinstance(parent, str) and parent.strip() and canonical_id(parent) != mod.id:
            mod.add_requirement(Requirement(
                [DependencyAlternative(parent.strip(), VersionSpec.any("*"), parent)],
                source="mcmod.info#parent",
                raw=entry,
            ))
        mod.mixin_configs.extend(_manifest_mixin_configs(manifest))
        results.append(mod)
    return results


def _parse_legacy_mc_constraints(value: Any) -> list[VersionSpec]:
    result: list[VersionSpec] = []
    if isinstance(value, (list, tuple)):
        for item in value:
            result.extend(_parse_legacy_mc_constraints(item))
        return result
    text = str(value).strip()
    if not text:
        return result
    if text.startswith("[") or text.startswith("("):
        result.append(parse_maven_range(text))
        return result
    # A common legacy spelling is "1.12,1.12.1,1.12.2".
    if "," in text:
        specs = []
        for part in text.split(","):
            part = part.strip()
            if part:
                specs.append(VersionSpec.exact(part))
        if specs:
            combined = VersionSpec([], text)
            for spec in specs:
                combined.intervals.extend(spec.intervals)
            return [combined]
    result.append(VersionSpec.exact(text, text))
    return result


def _parse_legacy_dependency(value: Any, source: str) -> Requirement | None:
    if not isinstance(value, (str, dict)):
        return None
    if isinstance(value, dict):
        dep_id = normalize_dep_id(value.get("modid") or value.get("modId") or value.get("id"))
        version = value.get("version") or value.get("versionRange") or "*"
        if not dep_id:
            return None
        spec = parse_loose_constraint(version, loader="forge")
        return Requirement([DependencyAlternative(dep_id, spec, str(value))],
                           source=source, raw=value)
    text = str(value).strip()
    if not text:
        return None
    # Forge @Mod(dependencies=...) style strings occasionally leak into
    # documentation; accept them defensively.
    text = re.sub(r"^(required-after|after|required-before|before):", "", text)
    if "@" in text:
        dep_id, _, version = text.partition("@")
        dep_id = normalize_dep_id(dep_id)
        spec = parse_loose_constraint(version, loader="forge")
    elif ":" in text and not text.split(":", 1)[0].startswith("["):
        prefix, _, rest = text.partition(":")
        if prefix.lower() in {"required", "optional", "after", "before", "required-after"}:
            dep_id = normalize_dep_id(rest)
            spec = VersionSpec.any("*")
        else:
            dep_id = normalize_dep_id(text)
            spec = VersionSpec.any("*")
    else:
        dep_id = normalize_dep_id(text)
        spec = VersionSpec.any("*")
    if not dep_id:
        return None
    return Requirement([DependencyAlternative(dep_id, spec, text)], source=source, raw=value)


# ---------------------------------------------------------------------------
# LiteLoader / Rift
# ---------------------------------------------------------------------------


def parse_litemod_metadata(data: dict[str, Any], metadata_path: str) -> list[ModRecord]:
    mod_id = canonical_id(data.get("modid") or data.get("name"))
    display = data.get("displayName") or data.get("name") or mod_id
    mod = _new_mod(
        mod_id, display, data.get("version") or data.get("revision") or "",
        loader="liteloader", metadata_path=metadata_path,
        metadata_format="litemod.json",
    )
    if not mod.id:
        return []
    mod.description = _short(data.get("description"), 700)
    mod.authors = _parse_authors(data.get("author") or data.get("authors"))
    mcversion = data.get("mcversion")
    if mcversion:
        for value in (mcversion if isinstance(mcversion, list) else [mcversion]):
            mod.mc_specs.append(parse_loose_constraint(value, loader="forge"))
    if data.get("tweakClass") or data.get("classTransformer"):
        mod.coremod = True
    deps = data.get("dependencies")
    if isinstance(deps, list):
        for dep in deps:
            parsed = _parse_legacy_dependency(dep, "litemod.json#dependencies")
            if parsed:
                mod.add_requirement(parsed)
    elif isinstance(deps, dict):
        for dep_id, version in deps.items():
            dep_id = normalize_dep_id(dep_id)
            if not dep_id:
                continue
            mod.add_requirement(Requirement(
                [DependencyAlternative(
                    dep_id,
                    parse_loose_constraint(version, loader="forge"),
                    str(version),
                )],
                source="litemod.json#dependencies",
                raw=deps,
            ))
    return [mod]


def parse_rift_metadata(data: dict[str, Any], metadata_path: str) -> list[ModRecord]:
    mod_id = canonical_id(data.get("id") or data.get("modid") or data.get("name"))
    mod = _new_mod(
        mod_id, data.get("name") or mod_id, data.get("version", ""),
        loader="rift", metadata_path=metadata_path, metadata_format="riftmod.json",
    )
    if not mod.id:
        return []
    mod.description = _short(data.get("description"), 700)
    mod.authors = _parse_authors(data.get("authors"))
    deps = data.get("dependencies")
    if isinstance(deps, list):
        for dep in deps:
            parsed = _parse_legacy_dependency(dep, "riftmod.json#dependencies")
            if parsed:
                mod.add_requirement(parsed)
    return [mod]


# ---------------------------------------------------------------------------
# Manifest / fallback
# ---------------------------------------------------------------------------


def parse_manifest(raw: bytes) -> dict[str, str]:
    try:
        text = raw.decode("utf-8", errors="replace")
    except Exception:
        return {}
    lines = re.split(r"\r?\n", text)
    result: dict[str, str] = {}
    current_key: str | None = None
    current_value: list[str] = []
    for line in lines:
        if line.startswith(" ") and current_key:
            current_value.append(line[1:])
            continue
        if current_key:
            result[current_key.lower()] = "".join(current_value).strip()
        current_key = None
        current_value = []
        if not line.strip() or ":" not in line:
            continue
        key, _, value = line.partition(":")
        current_key = key.strip()
        current_value = [value.lstrip()]
    if current_key:
        result[current_key.lower()] = "".join(current_value).strip()
    return result


def _manifest_mixin_configs(manifest: dict[str, str]) -> list[str]:
    raw = manifest.get("mixinconfigs") or manifest.get("mixin-configs") or ""
    return [part.strip() for part in re.split(r"[,;]", raw) if part.strip()]


def infer_mod_id_from_filename(filename: str) -> str:
    stem = re.sub(r"(?i)\.(jar|litemod|zip)(\.disabled)?$", "", filename)
    stem = re.sub(r"(?i)[-_.](universal|all|client|server|dev|sources|api)$", "", stem)
    # Cut at the first explicitly version-looking segment.
    candidate = re.split(r"[-_](?=\d+(?:[._-]|$))", stem, maxsplit=1)[0]
    if not candidate.strip():
        candidate = stem
    candidate = candidate.lower()
    candidate = re.sub(r"[^a-z0-9_.-]+", "_", candidate).strip("._-")
    if not candidate:
        candidate = "unknown_mod"
    if not candidate[0].isalpha():
        candidate = "mod_" + candidate
    return candidate[:64]


def infer_loader(manifest: dict[str, str], class_names: set[str], filename: str) -> str | None:
    keys = set(manifest.keys())
    if "fmlcoreplugin" in keys or "fmlcoreplugincontainsfmlmod" in keys or "fmlat" in keys:
        return "legacyforge"
    if "fabric-loom-version" in keys or "fabric-generator-version" in keys:
        return "fabric"
    if any(name.startswith("net/minecraftforge/") for name in class_names):
        return "forge"
    if any(name.startswith("org/quiltmc/") for name in class_names):
        return "quilt"
    if any(name.startswith("net/fabricmc/") for name in class_names):
        return "fabric"
    if any(name.startswith("net/minecraft/launchwrapper/") for name in class_names):
        return "legacyforge"
    if "mixinconfigs" in keys:
        return None
    if "tweakclass" in keys:
        value = manifest.get("tweakclass", "").lower()
        if "liteloader" in value:
            return "liteloader"
        return "legacyforge"
    return None


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


STANDARD_METADATA = {
    "fabric.mod.json": "fabric",
    "quilt.mod.json": "quilt",
    "META-INF/mods.toml": "forge",
    "META-INF/neoforge.mods.toml": "neoforge",
    "mcmod.info": "legacyforge",
    "META-INF/mcmod.info": "legacyforge",
    "litemod.json": "liteloader",
    "riftmod.json": "rift",
}


def _lower_entry_map(zf: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    mapping: dict[str, zipfile.ZipInfo] = {}
    for info in zf.infolist():
        if info.is_dir():
            continue
        mapping.setdefault(info.filename.lower(), info)
    return mapping


def _read_entry(zf: zipfile.ZipFile, info: zipfile.ZipInfo, limit: int) -> bytes:
    if info.file_size > limit:
        raise ValueError(f"元数据文件过大（{info.file_size} 字节）")
    with zf.open(info, "r") as handle:
        return handle.read(limit + 1)


def read_jar_metadata(zf: zipfile.ZipFile, jar: JarRecord) -> tuple[list[ModRecord], list[str]]:
    entries = _lower_entry_map(zf)
    issues: list[str] = []
    manifest_info = entries.get("meta-inf/manifest.mf")
    manifest: dict[str, str] = {}
    if manifest_info:
        try:
            manifest = parse_manifest(_read_entry(zf, manifest_info, MAX_METADATA_BYTES))
            jar.manifest = manifest
        except Exception as exc:
            issues.append(f"读取 MANIFEST.MF 失败：{exc}")

    records: list[ModRecord] = []
    found_metadata = False
    detected_loaders: list[str] = []
    if "fabric.mod.json" in entries:
        detected_loaders.append("fabric")
    if "quilt.mod.json" in entries:
        detected_loaders.append("quilt")
    if "meta-inf/neoforge.mods.toml" in entries:
        detected_loaders.append("neoforge")
    elif "meta-inf/mods.toml" in entries:
        detected_loaders.append("forge")
    if any(path in entries for path in ("mcmod.info", "meta-inf/mcmod.info")):
        detected_loaders.append("legacyforge")
    if "litemod.json" in entries:
        detected_loaders.append("liteloader")
    if "riftmod.json" in entries:
        detected_loaders.append("rift")

    def load_json(path: str) -> Any:
        info = entries.get(path.lower())
        if not info:
            return None
        raw = _read_entry(zf, info, MAX_METADATA_BYTES)
        return _safe_json_loads(raw.decode("utf-8-sig", errors="replace"))

    fabric_path = "fabric.mod.json"
    if fabric_path.lower() in entries:
        found_metadata = True
        try:
            data = load_json(fabric_path)
            if isinstance(data, dict):
                records.extend(parse_fabric_metadata(data, fabric_path))
            else:
                issues.append("fabric.mod.json 不是 JSON 对象")
        except Exception as exc:
            issues.append(f"fabric.mod.json 解析失败：{exc}")

    quilt_path = "quilt.mod.json"
    if quilt_path.lower() in entries:
        found_metadata = True
        try:
            data = load_json(quilt_path)
            if isinstance(data, dict):
                records.extend(parse_quilt_metadata(data, quilt_path))
            else:
                issues.append("quilt.mod.json 不是 JSON 对象")
        except Exception as exc:
            issues.append(f"quilt.mod.json 解析失败：{exc}")

    for toml_path in ("META-INF/neoforge.mods.toml", "META-INF/mods.toml"):
        if toml_path.lower() not in entries:
            continue
        found_metadata = True
        try:
            raw = _read_entry(zf, entries[toml_path.lower()], MAX_METADATA_BYTES)
            data = tomllib.loads(raw.decode("utf-8-sig", errors="replace"))
            if isinstance(data, dict):
                records.extend(parse_forge_toml(data, toml_path, manifest))
            else:
                issues.append(f"{toml_path} 不是 TOML 对象")
        except Exception as exc:
            issues.append(f"{toml_path} 解析失败：{exc}")

    for mcmod_path in ("mcmod.info", "META-INF/mcmod.info"):
        if mcmod_path.lower() not in entries:
            continue
        found_metadata = True
        try:
            data = load_json(mcmod_path)
            records.extend(parse_mcmod_info(data, mcmod_path, manifest))
        except Exception as exc:
            issues.append(f"{mcmod_path} 解析失败：{exc}")

    if "litemod.json" in entries:
        found_metadata = True
        try:
            data = load_json("litemod.json")
            if isinstance(data, dict):
                records.extend(parse_litemod_metadata(data, "litemod.json"))
            else:
                issues.append("litemod.json 不是 JSON 对象")
        except Exception as exc:
            issues.append(f"litemod.json 解析失败：{exc}")

    if "riftmod.json" in entries:
        found_metadata = True
        try:
            data = load_json("riftmod.json")
            if isinstance(data, dict):
                records.extend(parse_rift_metadata(data, "riftmod.json"))
            else:
                issues.append("riftmod.json 不是 JSON 对象")
        except Exception as exc:
            issues.append(f"riftmod.json 解析失败：{exc}")

    jar.metadata_formats = {record.metadata_format for record in records if record.metadata_format}

    # Some very old coremods only ship a manifest and an access transformer.
    if not records:
        inferred_loader = infer_loader(manifest, jar.class_names, jar.display_name)
        has_class_files = bool(jar.class_names)
        if inferred_loader or found_metadata or (manifest.get("fmlcoreplugin") or
                                                manifest.get("tweakclass") or
                                                manifest.get("fmlat") or
                                                manifest.get("mixinconfigs")):
            loader = inferred_loader or (detected_loaders[0] if detected_loaders else "forge")
            mod_id = infer_mod_id_from_filename(jar.display_name)
            mod = _new_mod(
                mod_id, jar.display_name.rsplit(".", 1)[0], manifest.get("implementation-version", ""),
                loader=loader, metadata_path="MANIFEST.MF",
                metadata_format="manifest-fallback",
            )
            mod.inference_note = "缺少标准 MOD 元数据，名称与 ID 由文件名 / Manifest 推断。"
            mod.coremod = bool(manifest.get("fmlcoreplugin") or manifest.get("tweakclass"))
            mod.mixin_configs.extend(_manifest_mixin_configs(manifest))
            for at in re.split(r"[,;]", manifest.get("fmlat", "")):
                if at.strip():
                    mod.access_wideners.append(at.strip())
            records.append(mod)
        elif has_class_files:
            # A class-containing JAR in a mods folder is suspicious even
            # without metadata.  Keep it visible with an inferred ID, but do
            # not pretend its dependencies are known.
            loader = inferred_loader or "unknown"
            mod_id = infer_mod_id_from_filename(jar.display_name)
            mod = _new_mod(
                mod_id, jar.display_name.rsplit(".", 1)[0],
                manifest.get("implementation-version", ""),
                loader=loader, metadata_path="MANIFEST.MF",
                metadata_format="unknown-jar",
            )
            mod.inference_note = "该 JAR 含 Java 类但缺少可识别的 MOD 元数据，已按文件名推断。"
            mod.coremod = bool(manifest.get("fmlcoreplugin") or manifest.get("tweakclass"))
            records.append(mod)
        else:
            issues.append("未发现 MOD 元数据（可能是资源包、库或安装器）。")

    # Merge multiple metadata formats that describe the same mod/version in
    # one JAR.  This avoids a false duplicate-ID finding for hybrid JARs.
    merged: dict[tuple[str, str], ModRecord] = {}
    for record in records:
        if not record.id:
            issues.append("发现缺少 MOD ID 的元数据，已忽略。")
            continue
        key = (record.id.lower(), record.version)
        existing = merged.get(key)
        if not existing:
            merged[key] = record
            continue
        existing.loaders.update(record.loaders)
        existing.requirements.extend(record.requirements)
        existing.incompatibilities.extend(record.incompatibilities)
        existing.provides.extend(record.provides)
        existing.mc_specs.extend(record.mc_specs)
        existing.java_specs.extend(record.java_specs)
        existing.loader_specs.extend(record.loader_specs)
        for path in record.mixin_configs:
            if path not in existing.mixin_configs:
                existing.mixin_configs.append(path)
        for path in record.access_wideners:
            if path not in existing.access_wideners:
                existing.access_wideners.append(path)
        if not existing.description:
            existing.description = record.description
        if not existing.authors:
            existing.authors = record.authors
        if not existing.license:
            existing.license = record.license
        if existing.environment == "*":
            existing.environment = record.environment
        existing.issues.extend(record.issues)

    final_records = list(merged.values())
    jar.mods = final_records
    jar.loaders = {loader for record in final_records for loader in record.loaders}
    jar.recognized = bool(final_records)
    jar.issues.extend(issues)
    for record in final_records:
        if record.issues:
            jar.issues.extend(record.issues)

    _resolve_mixins(zf, jar, entries, final_records, issues)
    _collect_manifest_metadata(jar, manifest)
    return final_records, issues


def _resolve_mixins(
    zf: zipfile.ZipFile,
    jar: JarRecord,
    entries: dict[str, zipfile.ZipInfo],
    mods: list[ModRecord],
    issues: list[str],
) -> None:
    config_paths: list[str] = []
    for mod in mods:
        for path in mod.mixin_configs:
            normalized = path.strip().replace("\\", "/").lstrip("/")
            if normalized not in config_paths:
                config_paths.append(normalized)
    for path in _manifest_mixin_configs(jar.manifest):
        if path not in config_paths:
            config_paths.append(path)
    for path in config_paths:
        info = entries.get(path.lower())
        if not info:
            issues.append(f"Mixin 配置声明了 {path}，但 JAR 内不存在该文件")
            continue
        jar.mixin_configs[path] = info.CRC
        meta: dict[str, Any] = {}
        try:
            raw = _read_entry(zf, info, MAX_METADATA_BYTES)
            data = _safe_json_loads(raw.decode("utf-8-sig", errors="replace"))
            if isinstance(data, dict):
                for key in ("package", "refmap", "minVersion", "compatibilityLevel",
                            "required", "target", "plugin"):
                    if key in data:
                        value = data[key]
                        if isinstance(value, (str, int, float, bool)):
                            meta[key] = value
                        elif isinstance(value, list):
                            meta[key] = [str(item) for item in value[:40]]
                refmap = data.get("refmap")
                if isinstance(refmap, str) and refmap.strip():
                    refmap_path = refmap.strip().lstrip("/")
                    refmap_info = entries.get(refmap_path.lower())
                    if refmap_info:
                        jar.refmaps[refmap_path] = refmap_info.CRC
                    else:
                        issues.append(f"Mixin 配置引用的 refmap {refmap_path} 不存在")
        except Exception as exc:
            issues.append(f"Mixin 配置 {path} 解析失败：{exc}")
        jar.mixin_meta[path] = meta


def _collect_manifest_metadata(jar: JarRecord, manifest: dict[str, str]) -> None:
    markers: list[str] = []
    for key in ("fmlcoreplugin", "fmlcoreplugincontainsfmlmod", "tweakclass",
                "main-class", "fmlat", "mixinconfigs"):
        if key in manifest:
            markers.append(key)
    jar.coremod_markers = markers
    for key, value in manifest.items():
        if key in {"fmlat"}:
            for part in re.split(r"[,;]", value):
                part = part.strip().lstrip("/")
                if part:
                    jar.access_transformers.setdefault(part, 0)


def parse_nested_mods(
    data: bytes,
    nest_display_name: str,
    parent_display_name: str,
) -> list[ModRecord]:
    """Read only MOD metadata from a nested JAR (jar-in-jar)."""
    if len(data) > MAX_NESTED_JAR_BYTES:
        return []
    try:
        with zipfile.ZipFile(io.BytesIO(data), "r") as zf:
            class_names = {
                info.filename for info in zf.infolist()
                if info.filename.lower().endswith(".class")
                and not info.filename.lower().startswith("meta-inf/")
            }
            nested = JarRecord(
                key=f"{parent_display_name}!/{nest_display_name}",
                path="",
                display_name=nest_display_name,
                valid=True,
                class_names=class_names,
            )
            records, _ = read_jar_metadata(zf, nested)
            result: list[ModRecord] = []
            for record in records:
                # Plain shaded libraries inside a JAR are not loadable MODs.
                # Only count nested jars that actually declare MOD metadata.
                if record.metadata_format == "unknown-jar":
                    continue
                if record.metadata_format == "manifest-fallback" and not record.coremod:
                    continue
                record.nested = True
                record.parent_file = parent_display_name
                result.append(record)
            return result
    except Exception:
        return []


def nested_jar_paths(zf: zipfile.ZipFile, mods: Iterable[ModRecord]) -> list[str]:
    paths: list[str] = []
    for info in zf.infolist():
        lower = info.filename.lower()
        if info.is_dir() or not lower.endswith(".jar"):
            continue
        if lower.startswith(("meta-inf/jars/", "meta-inf/jarjar/", "meta-inf/libraries/")):
            paths.append(info.filename)
    for mod in mods:
        for declared in mod.nested_jars:
            raw = str(declared).replace("\\", "/").lstrip("/")
            if raw and raw.lower().endswith(".jar") and raw not in paths:
                # Verify it is actually a JAR entry before trying to read it.
                lookup = {info.filename.lower(): info.filename for info in zf.infolist()}
                actual = lookup.get(raw.lower())
                if actual:
                    paths.append(actual)
    return list(dict.fromkeys(paths))


def read_nested_mods(zf: zipfile.ZipFile, jar: JarRecord) -> list[ModRecord]:
    result: list[ModRecord] = []
    for path in nested_jar_paths(zf, jar.mods):
        try:
            info = zf.getinfo(path)
            if info.file_size > MAX_NESTED_JAR_BYTES:
                continue
            with zf.open(info, "r") as handle:
                data = handle.read(MAX_NESTED_JAR_BYTES + 1)
            if len(data) > MAX_NESTED_JAR_BYTES:
                continue
            result.extend(parse_nested_mods(data, path.rsplit("/", 1)[-1], jar.display_name))
        except Exception:
            continue
    return result


__all__ = [
    "canonical_id",
    "infer_mod_id_from_filename",
    "normalize_dep_id",
    "parse_fabric_metadata",
    "parse_forge_toml",
    "parse_litemod_metadata",
    "parse_manifest",
    "parse_mcmod_info",
    "parse_nested_mods",
    "parse_quilt_metadata",
    "parse_rift_metadata",
    "read_jar_metadata",
    "read_nested_mods",
]
