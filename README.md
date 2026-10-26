# ModScope · 我的世界 MOD 冲突查找器

一个**本地、离线、不启动游戏**的 Minecraft MOD 静态冲突扫描器。  
把 `mods` 文件夹交给它，先检查依赖是否齐全、版本是否匹配、加载器是否混装，再比对 JAR 内部的重名类、Mixin 配置和资源命名空间。

覆盖 Forge（含 1.12 及更早的 `mcmod.info`）、NeoForge、Fabric、Quilt、LiteLoader、Rift，以及缺失元数据时的文件名/Manifest 推断。

> 这是第三方工具，不是 Minecraft 官方产品，与 Mojang / Microsoft 无关联。它只读扫描，不会修改 MOD 或存档。

---

## 快速开始

### Windows

1. 安装 **Python 3.11 或更高版本**：<https://www.python.org/downloads/>  
   安装时勾选 **Add Python to PATH**。
2. 双击 **`start.bat`**（或 `启动 ModScope.bat`）。
3. 浏览器会自动打开 `http://127.0.0.1:8765`。  
   如果没有自动打开，手动访问终端窗口里显示的地址。
4. 选择 `mods` 文件夹，或者在「或扫描本机路径」中粘贴路径，点击「开始分析」。

关闭启动器窗口即可停止本地服务。

### macOS / Linux

```bash
python3 app.py
```

然后访问终端输出的 `http://127.0.0.1:8765`。

无需安装任何第三方 Python 包，不需要联网。

---

## 两种扫描方式

| 方式 | 适合场景 | 行为 |
| --- | --- | --- |
| 选择文件夹 / 拖入文件 | 大多数玩家，直观 | 文件通过本机回环地址发给分析器，临时存放在系统临时目录；不会上传到互联网 |
| 直接填本机路径 | 大整合包、服务器 | 直接读取原文件夹，**不复制 JAR**，速度更快 |

> 浏览器中的「选择文件夹」只能取得你主动选中的文件；它不会读取其他目录。  
> 子文件夹中的 JAR 默认不会被扫描，因为 Forge/Fabric 也不会加载 `mods/子文件夹` 里的 MOD。  
> 「浏览」按钮会打开 Windows 系统文件夹选择框，把路径填入输入框；无法打开时直接粘贴路径即可。

---

## 扫描前可以填写什么

- **Minecraft 版本**（可选）：例如 `1.20.1`。填写后会逐个比对 MOD 声明的游戏版本范围；留空时仍会检查所有 MOD 的版本约束之间是否有交集。
- **加载器**（可选）：自动检测，或指定 Forge / NeoForge / Fabric / Quilt / LiteLoader。
  - Quilt 与 Fabric 视为同一生态，Quilt Loader 可以加载 Fabric MOD。
  - LiteLoader 可与 Forge 时代实例共存，不会被当成加载器混装。
- **运行端**：客户端或服务端，用来筛选带 `side = CLIENT/SERVER` 的依赖和互斥声明。
- **Java 主版本**（可选）：检查 `depends.java` / `features.java_version`，并根据 class 文件字节码提示 Java 版本过低。
- **深度扫描**：比对 class、Mixin 配置和资源命名空间。关闭后会明显更快，但会跳过类级冲突等深度检查。  
  内置 JAR（Jar-in-Jar）会始终参与依赖可用性解析，以便正确识别 Fabric API 模块等内置依赖。

---

## 能检查什么

### 加载器与版本

- Forge / NeoForge / Fabric / Quilt / LiteLoader 混装
- MOD 与所选加载器不匹配
- MOD 与 Minecraft 目标版本不匹配
- 所有 MOD 的游戏版本约束没有公共交集
- Java 版本约束不满足；JAR 字节码高于所选 Java

### 依赖

- 缺少必需依赖
- 依赖已安装但版本不满足 Fabric 谓词 / Maven 范围 / Quilt 版本规则
- 依赖文件存在但被改名为 `.disabled`，因此没有被加载
- MOD 元数据明确声明的互斥 / 不推荐组合
- Quilt 的 `unless` 例外条件
- Fabric/Quilt 的 `breaks`、`conflicts`
- 内置 JAR（Jar-in-Jar）提供的依赖可用性

### 文件与类

- 重复 MOD ID、重复 JAR 副本
- 同一 JAR 内声明多个不同的 MOD 版本
- 不同 MOD 的同名类：内容不同时高优先级提示；完全相同的内嵌库会单独归为低风险提示
- Mixin 配置 / refmap 文件重名
- 多个 MOD 共用同一 `assets/<namespace>`
- JAR 损坏、元数据损坏、声明的 Mixin 文件缺失
- 缺少标准元数据时使用文件名 / Manifest 推断，并明确标记

### 支持读取的元数据

| 格式 | 覆盖 |
| --- | --- |
| `fabric.mod.json` | Fabric 全系列 |
| `quilt.mod.json` | Quilt Loader |
| `META-INF/mods.toml` | Forge 1.13+ |
| `META-INF/neoforge.mods.toml` | NeoForge 1.20.5+ |
| `mcmod.info` | Forge 1.6–1.12，含可接受的游戏版本与依赖语法 |
| `litemod.json` / `.litemod` | LiteLoader |
| `riftmod.json` | Rift |
| `MANIFEST.MF` / 文件名 | 旧核心模组、缺少元数据的 JAR |

---

## 怎么得到最准确的结果

1. **选择真正的 `mods` 文件夹**，不要选 `.minecraft` 根目录。
2. **填入实际 Minecraft 版本**。留空只能检查「约束之间有无交集」，不能逐个判断。
3. **选择实际加载器**。自动检测以数量最多的加载器生态为准。
4. **开启深度扫描**，尤其是排查启动崩溃时。
5. 把扫描结果与 `logs/latest.log`、`crash-reports/` 对照确认。

---

## 命令行用法

```bash
# 扫描并打印摘要
python app.py scan "C:\Users\you\.minecraft\mods" --mc 1.20.1

# 指定加载器、Java 与运行端
python app.py scan "./mods" --mc 1.20.1 --loader forge --java 17 --side client

# 导出报告
python app.py scan "./mods" --mc 1.20.1 --json report.json --markdown report.md

# 有错误时退出码为 1（适合服务器/CI 预检）
python app.py scan "./mods" --mc 1.20.1 --fail-on error

# 只启动服务，不自动打开浏览器
python app.py serve --port 8899 --no-browser
```

`--loader` 可取 `auto`、`fabric`、`quilt`、`forge`、`neoforge`、`liteloader`。  
`--fail-on` 可取 `none`、`error`、`warning`。

---

## 项目结构

```text
app.py                      启动器 + CLI
modscope/
  analyzer.py               扫描编排、冲突规则、报告评分
  metadata.py               所有加载器元数据解析器
  versions.py               Version / Maven range / Fabric & Quilt 谓词
  server.py                 本地 HTTP 服务与上传会话
  folder_picker.py          Windows/Linux/macOS 可选文件夹选择器
  report_export.py          CLI Markdown 报告
  web/                      离线界面（HTML/CSS/JS，无外部 CDN）
tests/
  test_versions.py          版本语法单元测试
  test_analyzer.py          各类 JAR、依赖、类冲突和 HTTP API 集成测试
docs/
  代码导读.md               面向 Python 初学者的阅读与调试路线
  发布到GitHub.md           从本地项目到 GitHub Release 的完整步骤
.github/workflows/tests.yml  GitHub Actions 自动测试
start.bat                   Windows 一键启动
LICENSE                     MIT License
```

## 运行测试

```bash
python -m unittest discover -s tests -v
```

测试覆盖 Fabric、Forge TOML、NeoForge、Quilt、旧版 `mcmod.info`、LiteLoader、Jar-in-Jar、依赖版本不匹配、Minecraft/Java 不匹配、重复 ID、类冲突、Mixin 重名以及上传 API。

仓库还包含 [GitHub Actions 测试工作流](.github/workflows/tests.yml)，推送后会自动在两个 Python 版本上运行同一套测试。

## 源代码导读

如果你是 Python 初学者，建议先读：

- [Python 初学者源码导读](docs/代码导读.md)：文件职责、推荐阅读顺序、Python 语法对照、断点调试方法
- [发布到 GitHub](docs/发布到GitHub.md)：提交清单、GitHub Desktop / 命令行推送、Releases 发布方式

核心阅读路线：

```text
app.py → models.py → 跟一条“缺失依赖” → tests/test_analyzer.py → versions.py
```

不需要先读完整的 `metadata.py`、`analyzer.py` 或 `styles.css`。

---

## 隐私与临时文件

- 服务只监听 `127.0.0.1`，不会把扫描报告或 JAR 发往互联网。
- 使用「选择文件/文件夹」时，JAR 会临时复制到系统临时目录下的 `modscope-uploads/`，用于本地分析；会话默认 3 小时后过期，服务会在后续创建会话时清理过期目录。
- 使用「扫描本机路径」不会复制 JAR。
- MOD 元数据里的项目描述、作者信息只会在本机页面和导出报告中呈现。
- 页面不需要联网，也没有第三方分析脚本。

---

## 已知边界

静态扫描不是启动游戏的替代品。以下问题无法可靠地仅靠 JAR 元数据判断：

- 两个 Mixin 注入到同一个目标方法时是否真正兼容
- 事件优先级、注册顺序、运行期配置冲突
- 数据包/配方/标签的语义覆盖
- MOD 作者写错的依赖元数据
- 需要启动器实际版本才能校验的 Forge/NeoForge 加载器版本范围
- 内置 JAR 内部的递归类冲突
- Bukkit/Spigot/Paper 服务端插件（当前只面向 Minecraft MOD）

如果工具报告「同名类不同实现」「重复 ID」「缺少依赖」或「游戏版本不匹配」，请优先处理，它们通常是启动失败的直接原因。

---

## 常见问题

**双击 `start.bat` 提示找不到 Python？**  
重新安装 Python 并在安装器中勾选 “Add Python to PATH”，或使用 `py -3 app.py` 手动启动。

**端口 8765 被占用？**  
使用 `python app.py serve --port 8899`，然后打开 `http://127.0.0.1:8899`。

**「浏览」按钮没有反应？**  
系统文件夹对话框可能被其他窗口挡住，或在受限环境下不可用。直接复制文件夹路径粘贴到输入框即可。

**文件会被上传到哪里？**  
只通过 `127.0.0.1` 送到同一台电脑上由本工具启动的 Python 服务；不会离开本机。追求零复制可使用路径扫描。

**为什么有提示但不是崩溃？**  
提示（notice）通常是可兼容信息，例如多个 MOD 内嵌了完全相同的库、资源命名空间重叠；严重（error）才是高概率启动失败项。

---

## 许可证

本项目使用 [MIT License](LICENSE)。第三方工具声明：ModScope 与 Mojang / Microsoft 无关联。

---

ModScope v1.0 · 为整合包做一次启动前体检。
