"""Human-readable report exports shared by the CLI and tests."""

from __future__ import annotations

from typing import Any


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


def render_markdown(report: dict[str, Any]) -> str:
    summary = report.get("summary", {})
    target = report.get("target", {})
    stats = report.get("stats", {})
    options = report.get("options", {})
    source = report.get("source", {})
    lines = [
        "# ModScope · 我的世界 MOD 冲突报告",
        "",
        f"> 生成时间：{report.get('generated_at', '')}  ",
        f"> 扫描来源：`{source.get('label', '')}`  ",
        f"> 健康评分：**{summary.get('health_score', 0)} / 100** · {summary.get('grade_label', '')}",
        "",
        "## 扫描概况",
        "",
        f"- MOD 文件：{stats.get('jar_count', 0)} 个（有效 {stats.get('valid_jars', 0)}）",
        f"- 活动 MOD：{stats.get('active_mods', 0)} 个；已禁用：{stats.get('disabled_mods', 0)} 个；内置 JAR：{stats.get('nested_mods', 0)} 个",
        f"- 加载器：{target.get('loader_label', '未知')}；兼容端：{'客户端' if options.get('side') == 'client' else '服务端'}",
        f"- Minecraft：{options.get('mc_version') or '未指定（仅检查约束交集）'}；Java：{options.get('java_version') or '未指定'}",
        f"- 结果：严重 {summary.get('errors', 0)} · 警告 {summary.get('warnings', 0)} · 提示 {summary.get('notices', 0)}",
        "",
        "## 诊断结果",
        "",
    ]
    findings = report.get("findings", [])
    if not findings:
        lines.append("没有发现需要处理的冲突。")
        lines.append("")
    for index, finding in enumerate(findings, start=1):
        severity = SEVERITY_LABEL.get(finding.get("severity"), finding.get("severity", ""))
        category = CATEGORY_LABEL.get(finding.get("category"), finding.get("category", ""))
        lines.append(f"### {index}. [{severity}][{category}] {finding.get('title', '')}")
        lines.append("")
        if finding.get("summary"):
            lines.append(finding["summary"])
            lines.append("")
        if finding.get("detail"):
            lines.append(finding["detail"])
            lines.append("")
        evidence = finding.get("evidence") or []
        if evidence:
            lines.append("**证据**")
            lines.append("")
            for item in evidence[:80]:
                lines.append(f"- {item}")
            lines.append("")
        recommendation = finding.get("recommendation")
        if recommendation:
            lines.append(f"**建议：** {recommendation}")
            lines.append("")

    lines.extend([
        "## 已扫描 MOD",
        "",
        "| 状态 | MOD | ID | 版本 | 加载器 | 文件 |",
        "| --- | --- | --- | --- | --- | --- |",
    ])
    status_label = {"active": "启用", "disabled": "禁用", "nested": "内置"}
    for mod in report.get("mods", []):
        lines.append(
            "| {status} | {name} | {id} | {version} | {loader} | {file} |".format(
                status=status_label.get(mod.get("status"), mod.get("status", "")),
                name=_cell(mod.get("name", "")),
                id=_cell(mod.get("id", "")),
                version=_cell(mod.get("version", "") or "-"),
                loader=_cell(mod.get("loader_label", "")),
                file=_cell(mod.get("file", "")),
            )
        )
    lines.append("")
    lines.append("## 检查边界")
    lines.append("")
    for caveat in report.get("caveats", []):
        lines.append(f"- {caveat}")
    lines.extend([
        "",
        "---",
        "",
        "由 ModScope 本地生成。静态检查不能替代实际启动日志。",
        "",
    ])
    return "\n".join(lines)


def _cell(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


__all__ = ["render_markdown"]
