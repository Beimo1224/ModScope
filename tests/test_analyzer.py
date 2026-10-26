from __future__ import annotations

import io
import json
import os
from pathlib import Path
import shutil
import threading
import time
import unittest
import urllib.error
import urllib.request
import uuid
import zipfile

from modscope.analyzer import Analyzer, discover_path_files
from modscope.models import ScanOptions
from modscope.server import create_server


WORK_ROOT = Path(__file__).resolve().parent / ".work"


def _class_bytes(major: int, payload: bytes = b"") -> bytes:
    header = b"\xca\xfe\xba\xbe" + b"\x00\x00" + int(major).to_bytes(2, "big")
    return header + payload


def _zip_bytes(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return buffer.getvalue()


class PackCase(unittest.TestCase):
    def setUp(self):
        WORK_ROOT.mkdir(parents=True, exist_ok=True)
        self.root = WORK_ROOT / f"pack-{uuid.uuid4().hex}"
        self.root.mkdir()

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def write_jar(self, name: str, entries: dict[str, object]):
        path = self.root / name
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
            for entry_name, data in entries.items():
                if isinstance(data, (dict, list)):
                    data = json.dumps(data, ensure_ascii=False).encode("utf-8")
                elif isinstance(data, str):
                    data = data.encode("utf-8")
                archive.writestr(entry_name, data)
        return path

    def scan(self, **overrides):
        options = ScanOptions(
            mc_version=overrides.pop("mc_version", "1.20.1"),
            loader=overrides.pop("loader", "auto"),
            side=overrides.pop("side", "client"),
            java_version=overrides.pop("java_version", 17),
            deep_scan=overrides.pop("deep_scan", True),
            include_disabled=overrides.pop("include_disabled", True),
        )
        refs, label = discover_path_files(str(self.root), options)
        return Analyzer(options).analyze(refs, label)

    @staticmethod
    def findings(report, category=None, severity=None):
        result = report["findings"]
        if category:
            result = [finding for finding in result if finding["category"] == category]
        if severity:
            result = [finding for finding in result if finding["severity"] == severity]
        return result


class FabricAnalyzerTests(PackCase):
    def _basic_pack(self):
        alpha = self.write_jar("alpha.jar", {
            "fabric.mod.json": {
                "schemaVersion": 1,
                "id": "alpha",
                "name": "Alpha",
                "version": "1.0.0",
                "depends": {
                    "fabricloader": ">=0.14.0",
                    "minecraft": "1.20.x",
                    "java": ">=17",
                    "beta": ">=1.0.0",
                },
                "breaks": {"gamma": ">=2.0.0"},
                "mixins": ["alpha.mixins.json"],
            },
            "alpha.mixins.json": {
                "required": True,
                "package": "com.test.mixin",
                "refmap": "alpha.refmap.json",
                "mixins": ["ExampleMixin"],
            },
            "alpha.refmap.json": {"mappings": {}},
            "com/shared/Used.class": _class_bytes(61, b"alpha"),
            "assets/alpha/lang/en_us.json": "{}",
        })
        beta = self.write_jar("beta.jar", {
            "fabric.mod.json": {
                "schemaVersion": 1,
                "id": "beta",
                "name": "Beta",
                "version": "0.9.0",
                "depends": {"minecraft": "1.20.1"},
            },
            "com/shared/Used.class": _class_bytes(61, b"beta"),
            "assets/beta/lang/en_us.json": "{}",
        })
        gamma = self.write_jar("gamma.jar", {
            "fabric.mod.json": {
                "schemaVersion": 1,
                "id": "gamma",
                "name": "Gamma",
                "version": "2.1.0",
                "depends": {"minecraft": "1.20.1"},
            },
            "assets/alpha/lang/en_us.json": "{}",
        })
        return alpha, beta, gamma

    def test_dependency_version_incompatibility_and_class_collision(self):
        self._basic_pack()
        report = self.scan()
        self.assertEqual(report["stats"]["active_mods"], 3)
        titles = [finding["title"] for finding in report["findings"]]
        self.assertTrue(any("依赖版本不满足要求" in title for title in titles), titles)
        self.assertTrue(any("MOD 互斥" in title for title in titles), titles)
        self.assertTrue(any("核心类冲突" in title for title in titles), titles)
        self.assertIn("Fabric", [loader["label"] for loader in report["target"]["loaders"]])
        class_findings = self.findings(report, "class", "error")
        self.assertEqual(len(class_findings), 1)
        self.assertIn("com.shared.Used", class_findings[0]["evidence"][0])

    def test_minecraft_version_mismatch(self):
        self._basic_pack()
        report = self.scan(mc_version="1.21")
        version_findings = self.findings(report, "version", "error")
        self.assertTrue(any("Minecraft 1.21" in finding["title"] for finding in version_findings))

    def test_loader_mismatch(self):
        self._basic_pack()
        report = self.scan(loader="forge")
        loader_findings = self.findings(report, "loader", "error")
        self.assertTrue(loader_findings)
        self.assertIn("Fabric", loader_findings[0]["evidence"][0])

    def test_missing_and_disabled_dependencies(self):
        self.write_jar("needs.jar", {
            "fabric.mod.json": {
                "schemaVersion": 1,
                "id": "needs",
                "name": "Needs",
                "version": "1.0.0",
                "depends": {
                    "minecraft": "1.20.1",
                    "missing_lib": ">=1.0.0",
                    "disabled_lib": ">=1.0.0",
                },
            },
        })
        self.write_jar("disabled-lib.jar.disabled", {
            "fabric.mod.json": {
                "schemaVersion": 1,
                "id": "disabled_lib",
                "name": "Disabled Lib",
                "version": "1.0.0",
            },
        })
        report = self.scan()
        missing = self.findings(report, "dependency", "error")
        self.assertTrue(any("missing_lib" in item for finding in missing for item in finding["evidence"]))
        disabled = self.findings(report, "dependency", "warning")
        self.assertTrue(any("disabled_lib" in item for finding in disabled for item in finding["evidence"]))
        self.assertEqual(report["stats"]["disabled_mods"], 1)

    def test_duplicate_id_copy_and_mixin_collisions(self):
        self._basic_pack()
        # A complete duplicate of alpha, renamed.
        shutil.copy(self.root / "alpha.jar", self.root / "alpha-copy.jar")
        report = self.scan()
        duplicate = self.findings(report, "duplicate", "error")
        self.assertTrue(any("重复安装" in finding["title"] for finding in duplicate))
        warning_titles = [finding["title"] for finding in self.findings(report, severity="warning")]
        self.assertTrue(any("完全相同的 JAR" in title for title in warning_titles), warning_titles)
        self.assertTrue(any("Mixin" in title for title in warning_titles), warning_titles)

    def test_nested_jar_satisfies_dependency(self):
        nested_jar = _zip_bytes({
            "fabric.mod.json": json.dumps({
                "schemaVersion": 1,
                "id": "bundledlib",
                "name": "Bundled Library",
                "version": "1.2.0",
            }).encode("utf-8"),
        })
        self.write_jar("outer.jar", {
            "fabric.mod.json": {
                "schemaVersion": 1,
                "id": "outer",
                "name": "Outer",
                "version": "1.0.0",
                "depends": {
                    "minecraft": "1.20.1",
                    "bundledlib": ">=1.0.0",
                },
            },
            "META-INF/jars/bundledlib-1.2.0.jar": nested_jar,
        })
        report = self.scan()
        self.assertEqual(report["stats"]["nested_mods"], 1)
        self.assertFalse(self.findings(report, "dependency", "error"))
        nested = [mod for mod in report["mods"] if mod["status"] == "nested"]
        self.assertEqual(len(nested), 1)
        self.assertEqual(nested[0]["id"], "bundledlib")


class OtherLoaderTests(PackCase):
    def test_forge_toml(self):
        toml = """
modLoader="javafml"
loaderVersion="[47,)"
license="MIT"

[[mods]]
modId="forge_a"
version="1.0.0"
displayName="Forge A"

[[dependencies.forge_a]]
modId="minecraft"
mandatory=true
versionRange="[1.20.1,1.21)"

[[dependencies.forge_a]]
modId="missing_forge_lib"
mandatory=true
versionRange="[2.0,3.0)"
"""
        self.write_jar("forge-a.jar", {"META-INF/mods.toml": toml})
        report = self.scan()
        self.assertEqual(report["target"]["loaders"][0]["id"], "forge")
        self.assertTrue(any("missing_forge_lib" in item for finding in report["findings"] for item in finding["evidence"]))
        self.assertEqual(report["mods"][0]["loader_label"], "Forge")

    def test_neoforge_incompatible_dependency(self):
        self.write_jar("neo-a.jar", {
            "META-INF/neoforge.mods.toml": """
modLoader="javafml"
loaderVersion="[1,)"
license="MIT"
[[mods]]
modId="neo_a"
version="1.0.0"
displayName="Neo A"
[[dependencies.neo_a]]
modId="neo_b"
type="incompatible"
versionRange="[1.0,2.0)"
""",
        })
        self.write_jar("neo-b.jar", {
            "META-INF/neoforge.mods.toml": """
modLoader="javafml"
loaderVersion="[1,)"
license="MIT"
[[mods]]
modId="neo_b"
version="1.5.0"
displayName="Neo B"
""",
        })
        report = self.scan()
        self.assertEqual(report["target"]["loaders"][0]["id"], "neoforge")
        self.assertTrue(any("互斥" in finding["title"] for finding in report["findings"]))

    def test_quilt_and_fabric_family(self):
        self.write_jar("quilt-a.jar", {
            "quilt.mod.json": {
                "schema_version": 1,
                "quilt_loader": {
                    "group": "com.example",
                    "id": "quilt_a",
                    "version": "1.0.0",
                    "metadata": {"name": "Quilt A"},
                    "depends": [
                        {"id": "minecraft", "versions": ">=1.20"},
                        {"id": "fabric-api", "versions": ">=0.80"},
                    ],
                },
            },
        })
        self.write_jar("fabric-api.jar", {
            "fabric.mod.json": {
                "schemaVersion": 1,
                "id": "fabric-api",
                "name": "Fabric API",
                "version": "0.90.0",
            },
        })
        report = self.scan(mc_version="1.20.1")
        self.assertEqual(report["stats"]["active_mods"], 2)
        self.assertFalse(self.findings(report, "dependency", "error"))
        loaders = {loader["id"] for loader in report["target"]["loaders"]}
        self.assertEqual(loaders, {"quilt", "fabric"})
        self.assertFalse(self.findings(report, "loader", "error"))

    def test_legacy_mcmod_and_liteloader(self):
        self.write_jar("legacy.jar", {
            "mcmod.info": [{
                "modid": "legacy_mod",
                "name": "Legacy Mod",
                "version": "1.0",
                "mcversion": "1.12.2",
                "dependencies": ["forge@[14.23,)"],
            }],
        })
        self.write_jar("liteloader.litemod", {
            "litemod.json": {
                "name": "lite_mod",
                "displayName": "Lite Mod",
                "version": "1.0",
                "mcversion": "1.12.2",
            },
        })
        report = self.scan(mc_version="1.12.2")
        loaders = {mod["id"]: mod["loader"] for mod in report["mods"]}
        self.assertEqual(loaders["legacy_mod"], "legacyforge")
        self.assertEqual(loaders["lite_mod"], "liteloader")
        self.assertEqual(report["stats"]["active_mods"], 2)

    def test_java_version_checks(self):
        self.write_jar("java-mod.jar", {
            "fabric.mod.json": {
                "schemaVersion": 1,
                "id": "java_mod",
                "name": "Java Mod",
                "version": "1.0.0",
                "depends": {"minecraft": "1.20.1", "java": ">=21"},
            },
            "com/example/Mod.class": _class_bytes(65),
        })
        report = self.scan(java_version=17)
        java_findings = self.findings(report, "version")
        self.assertTrue(java_findings)
        self.assertTrue(any("Java 17" in finding["title"] for finding in java_findings))


class ServerApiTests(PackCase):
    def test_health_upload_scan_and_delete(self):
        self.write_jar("api-mod.jar", {
            "fabric.mod.json": {
                "schemaVersion": 1,
                "id": "api_mod",
                "name": "API Mod",
                "version": "1.0.0",
                "depends": {"minecraft": "1.20.1"},
            },
        })
        server, context, port = create_server(port=0, web_root=Path(__file__).resolve().parents[1] / "modscope" / "web")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{port}"
        headers = {"X-ModScope-Request": "1"}

        def request(path, method="GET", data=None, extra_headers=None, expect=200):
            all_headers = dict(headers if method != "GET" else {})
            all_headers.update(extra_headers or {})
            body = None
            if isinstance(data, (dict, list)):
                body = json.dumps(data).encode("utf-8")
                all_headers["Content-Type"] = "application/json"
            elif isinstance(data, bytes):
                body = data
            req = urllib.request.Request(base + path, data=body, method=method, headers=all_headers)
            try:
                with urllib.request.urlopen(req, timeout=15) as response:
                    raw = response.read()
                    content_type = response.headers.get("Content-Type", "")
                    if "application/json" in content_type:
                        payload = json.loads(raw.decode("utf-8"))
                    else:
                        payload = raw.decode("utf-8", errors="replace")
                    self.assertEqual(response.status, expect)
                    return payload
            except urllib.error.HTTPError as exc:
                payload = json.loads(exc.read().decode("utf-8"))
                self.assertEqual(exc.code, expect)
                return payload

        try:
            health = request("/api/health")
            self.assertTrue(health["ok"])
            self.assertIn("ModScope", request("/"))
            denied = request("/api/uploads", method="POST", data={}, extra_headers={"X-ModScope-Request": "0"}, expect=403)
            self.assertFalse(denied["ok"])
            session = request("/api/uploads", method="POST", data={})
            sid = session["session_id"]
            jar_bytes = (self.root / "api-mod.jar").read_bytes()
            upload = request(
                f"/api/upload/{sid}?name=api-mod.jar",
                method="POST",
                data=jar_bytes,
                extra_headers={"Content-Type": "application/octet-stream"},
            )
            self.assertTrue(upload["ok"])
            scan = request("/api/scan", method="POST", data={
                "upload_session": sid,
                "mc_version": "1.20.1",
                "loader": "fabric",
                "side": "client",
                "java_version": 17,
                "deep_scan": True,
                "include_disabled": True,
            })
            self.assertTrue(scan["ok"])
            self.assertEqual(scan["report"]["stats"]["active_mods"], 1)
            dropped = request(f"/api/uploads/{sid}", method="DELETE")
            self.assertTrue(dropped["removed"])
        finally:
            server.shutdown()
            server.server_close()
            context.close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
