#!/usr/bin/env python3
"""ModScope launcher and command-line interface.

Run ``python app.py`` to start the local web interface.
Run ``python app.py scan ".../mods" --mc 1.20.1`` for a terminal report.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from modscope import APP_VERSION
from modscope.analyzer import Analyzer, ScanError, discover_path_files, validate_options
from modscope.models import ScanOptions
from modscope.report_export import render_markdown
from modscope.server import run_server


def _utf8_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def _serve(args: argparse.Namespace) -> int:
    try:
        run_server(host=args.host, port=args.port, open_browser=not args.no_browser)
    except OSError as exc:
        print(f"无法启动本地服务：{exc}", file=sys.stderr)
        print("请尝试换一个端口，例如：python app.py serve --port 8899", file=sys.stderr)
        return 2
    return 0


def _scan(args: argparse.Namespace) -> int:
    options = ScanOptions(
        mc_version=args.mc or "",
        loader=args.loader,
        side=args.side,
        java_version=args.java,
        deep_scan=not args.no_deep,
        include_disabled=not args.exclude_disabled,
        source_kind="path",
    )
    try:
        options = validate_options(options)
        refs, source_label = discover_path_files(args.path, options)
        report = Analyzer(options).analyze(refs, source_label)
    except ScanError as exc:
        print(f"扫描失败：{exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"扫描时发生内部错误：{exc}", file=sys.stderr)
        return 3

    summary = report["summary"]
    stats = report["stats"]
    print()
    print("ModScope · 我的世界 MOD 冲突扫描")
    print(f"来源   {source_label}")
    print(f"文件   {stats['jar_count']} 个 JAR / {stats['active_mods']} 个启用 MOD")
    print(f"评分   {summary['health_score']}/100  {summary['grade_label']}")
    print(f"结果   {summary['headline']}")
    print()
    for finding in report["findings"]:
        marker = {"error": "严重", "warning": "警告", "notice": "提示"}.get(finding["severity"], "信息")
        print(f"[{marker}] {finding['title']}")
        if finding.get("summary"):
            print(f"       {finding['summary']}")
        for item in finding.get("evidence", [])[:4]:
            print(f"       · {item}")
        if len(finding.get("evidence", [])) > 4:
            print(f"       · 另有 {len(finding['evidence']) - 4} 条证据")
        print()
    print("提示：" + " ".join(report.get("caveats", [])[:2]))
    print()

    json_path = Path(args.json).expanduser() if args.json else None
    md_path = Path(args.markdown).expanduser() if args.markdown else None
    if json_path:
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"JSON 报告已写入：{json_path}")
    if md_path:
        md_path.parent.mkdir(parents=True, exist_ok=True)
        md_path.write_text(render_markdown(report), encoding="utf-8")
        print(f"Markdown 报告已写入：{md_path}")

    if args.fail_on != "none":
        if args.fail_on == "warning" and (summary["errors"] or summary["warnings"]):
            return 1
        if args.fail_on == "error" and summary["errors"]:
            return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ModScope",
        description="我的世界全版本 MOD 冲突查找器（本地、离线、静态扫描）",
    )
    parser.add_argument("--version", action="version", version=f"ModScope {APP_VERSION}")
    subparsers = parser.add_subparsers(dest="command")

    serve = subparsers.add_parser("serve", help="启动本地 Web 界面（默认）")
    serve.add_argument("--host", default="127.0.0.1", help="监听地址，默认仅本机")
    serve.add_argument("--port", type=int, default=8765, help="端口，默认 8765")
    serve.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    serve.set_defaults(func=_serve)

    scan = subparsers.add_parser("scan", help="在终端扫描 mods 文件夹")
    scan.add_argument("path", help="mods 文件夹或单个 MOD 文件路径")
    scan.add_argument("--mc", default="", help="Minecraft 版本，例如 1.20.1")
    scan.add_argument("--loader", default="auto",
                      choices=["auto", "fabric", "quilt", "forge", "neoforge", "liteloader"])
    scan.add_argument("--side", default="client", choices=["client", "server"])
    scan.add_argument("--java", type=int, default=None, help="Java 主版本，例如 17")
    scan.add_argument("--no-deep", action="store_true", help="跳过 class / Mixin 深度扫描")
    scan.add_argument("--exclude-disabled", action="store_true", help="忽略 .disabled 文件")
    scan.add_argument("--json", help="导出 JSON 报告的路径")
    scan.add_argument("--markdown", help="导出 Markdown 报告的路径")
    scan.add_argument("--fail-on", default="none", choices=["none", "error", "warning"],
                      help="出现指定级别问题时返回退出码 1（便于 CI/服务器管理）")
    scan.set_defaults(func=_scan)
    return parser


def main(argv: list[str] | None = None) -> int:
    _utf8_console()
    args_list = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    if not args_list:
        args = parser.parse_args(["serve"])
    else:
        args = parser.parse_args(args_list)
    if not getattr(args, "command", None):
        args = parser.parse_args(["serve"])
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
