# 发布到 GitHub

这份指南按“完全没发布过项目”的初学者视角编写。

先说结论：**源码可以发布，但不要直接把整个文件夹原样拖到网页上传。**  
请使用 Git / GitHub Desktop，让 `.gitignore` 排除本地文件，并把压缩包放到 Releases。

> 当前目录已经初始化本地 Git 仓库，源码已加入暂存区（`git status` 中显示为 `A`）。  
> 现在只差三件事：配置你的 Git 身份、完成第一次提交、创建并推送 GitHub 远程仓库。

---

## 1. 发布前检查清单

- [x] 有 README
- [x] 有自动化测试
- [x] 有 GitHub Actions 测试工作流
- [x] 有 `.gitignore`
- [x] 已添加 MIT 许可证
- [x] 已初始化本地 Git 仓库并暂存源码
- [ ] 配置 Git 用户名和邮箱并提交
- [ ] 确认没有密钥、账号或真实 MOD 文件被提交
- [ ] 在 GitHub 上创建仓库并推送

---

## 2. 哪些文件不应该提交？

这个项目已经在 `.gitignore` 中排除了：

```text
.idea/
.vscode/
__pycache__/
.tmp/
.uploads/
tests/.work/
*.log
ModScope-*.zip
```

特别注意：

- **不要提交你实际使用的 MOD JAR**。它们有各自的授权，而且会让仓库变大。
- **不要提交游戏日志、崩溃报告、账号文件**。
- **不要提交 `ModScope-1.0.0.zip`**。压缩包放在 GitHub Releases。
- `.idea/workspace.xml` 里常有本地 IDE 状态，不应公开。

提交前一定要运行：

```bash
git status
```

确认列表里只有源码、测试、文档和配置。

---

## 3. 方式 A：GitHub Desktop（推荐给初学者）

1. 安装 [GitHub Desktop](https://desktop.github.com/)。
2. 打开 GitHub Desktop，登录你的 GitHub 账号。
3. 选择 **File → Add Local Repository**。
4. 选择 `H:\Minecraft Mod Conflict Finder`。
5. 如果它提示 “this directory does not appear to be a Git repository”，点击 **create a repository**。
6. 在左侧 **Changes** 中检查文件列表。
   - 不应该看到 `.idea`、`.tmp`、`.uploads`、ZIP。
7. 在左下角填写提交信息：

   ```text
   Initial commit: ModScope v1.0.0
   ```

8. 点击 **Commit to main**。
9. 点击 **Publish repository**。
   - Name：`modscope` 或 `minecraft-mod-conflict-finder`
   - Description：`Minecraft MOD 冲突查找器：本地静态分析依赖、版本、加载器、类与 Mixin 冲突`
   - 先选 **Private** 也可以。
10. 发布后到 GitHub 网页查看 README 是否正常显示。

---

## 4. 方式 B：命令行

如果本机已经安装 Git：

```bash
cd "H:\Minecraft Mod Conflict Finder"

# 如果还没有 .git，先执行；当前目录已经初始化过，这一步可跳过
git init -b main

git status
```

由于这里已经把文件加入过暂存区，`git status` 中应该看到许多 `A` 开头的文件，代表 “Added / 已暂存”。  
**先仔细观察列表。** 只应该看到项目源码和文档。确认没有 `.idea`、`.tmp`、`.uploads`、ZIP，然后：

```bash
git config user.name "你的名字"
git config user.email "你的GitHub邮箱"
git commit -m "Initial commit: ModScope v1.0.0"
```

如果你修改了文件，想重新暂存：

```bash
git add .
git status
```

到 GitHub 网页新建一个空仓库（不要勾选自动生成 README），然后：

```bash
git remote add origin https://github.com/你的用户名/你的仓库名.git
git push -u origin main
```

第一次推送会要求登录。GitHub 不接受账号密码，推荐：

- 使用 Git for Windows 自带的 Git Credential Manager，会弹出浏览器登录；
- 或创建 Personal Access Token；
- 或使用 GitHub Desktop 完成认证。

> 这个目录已经初始化过本地 Git 仓库，但还没有提交。如果你不想配置 Git 身份，就用 GitHub Desktop 完成提交和发布。

---

## 5. 把 ZIP 放到 Releases

源码仓库里不需要 `ModScope-1.0.0.zip`，因为它已经被 `.gitignore` 排除。  
把压缩包作为 Release 附件：

1. 打开 GitHub 仓库。
2. 右侧点击 **Releases**。
3. 点击 **Draft a new release**。
4. Tag 填：

   ```text
   v1.0.0
   ```

5. Release title 填：

   ```text
   ModScope v1.0.0
   ```

6. 上传本地打包好的 `ModScope-1.0.0.zip`（这个文件不应提交进源码仓库，但可以放在 Release 附件中）。
7. 写变更说明，例如：

   ```markdown
   首个公开版本。

   - 支持 Forge / NeoForge / Fabric / Quilt / LiteLoader / Rift 元数据
   - 检查依赖、版本、加载器、重复 ID、同名类与 Mixin 冲突
   - 本地离线扫描，不上传 JAR 到互联网
   - 支持 JSON / Markdown 报告与打印为 PDF
   ```

8. 点击 **Publish release**。

---

## 6. 仓库设置建议

在仓库顶部 About 区域：

**Description：**

```text
Minecraft 全版本 MOD 冲突查找器：本地检查依赖、版本、加载器、重复 ID、同名类与 Mixin 冲突
```

**Topics：**

```text
minecraft
minecraft-mod
fabric
forge
neoforge
quilt
modding
python
tool
```

这些设置能让别人更容易搜索到项目，也能让仓库看起来更完整。

---

## 7. 不要用 GitHub Pages

这个项目不是纯静态网页。  
它需要 Python 本地服务来处理扫描请求，所以 **GitHub Pages 不能直接运行它**。

正确的发布方式是：

- 源码放在 GitHub 仓库；
- 用户下载源码或 Release ZIP，在本机运行 `start.bat`；
- 如果以后想做“免安装版”，再考虑 PyInstaller 打包 Windows EXE。

---

## 8. 许可证

项目已经附带 [MIT License](../LICENSE)。

这意味着别人可以：

- 使用
- 复制
- 修改
- 合并
- 发布
- 再授权
- 销售

只要保留版权和许可证声明即可。

当前版权署名是 **ModScope contributors**。如果你想改成本人的名字或 GitHub ID，请编辑 [LICENSE](../LICENSE) 第一行的版权声明，然后重新提交。

如果你之后想改用其他许可证，必须在发布前替换 `LICENSE`，并注意已经发布的版本无法“收回”别人获得的授权。

---

## 9. GitHub Actions

`.github/workflows/tests.yml` 会在每次 push 和 Pull Request 时自动运行测试：

```bash
python -m unittest discover -s tests -v
```

你可以在仓库的 **Actions** 页面查看绿色勾或失败日志。  
如果第一次 push 后 Actions 显示失败，先点进日志看是哪个测试失败，不要把失败状态留在主页上。

---

## 10. 常见问题

**Q：直接拖文件夹到 GitHub 网页可以吗？**  
可以上传文件，但不推荐。网页上传不方便检查忽略文件，也容易把 `.idea` 或临时文件带上去。使用 Git / GitHub Desktop 更可靠。

**Q：ZIP 放在哪里？**  
GitHub Releases，不要放在源码目录。

**Q：需要把 `mods` 文件夹也上传吗？**  
绝对不要。真实 MOD 有版权，也会让仓库巨大。

**Q：Private 仓库能发布 Release 吗？**  
可以。Release 的可见性跟随仓库设置。

**Q：源码公开后，别人能看到我的本地路径吗？**  
代码中没有硬编码你的用户目录；README 只是示例路径。不要把 `.idea/`、日志、截图里的个人信息提交即可。

**Q：以后改了代码怎么更新？**

```bash
git add .
git commit -m "描述这次修改"
git push
```

然后重新生成 ZIP，在 Releases 中创建 `v1.0.1`。

---

## 11. 发布前最后一条命令

```bash
python -m unittest discover -s tests -v
```

确认最后一行是：

```text
OK
```

再推送。

---

返回 [README](../README.md) · 学习源码见 [Python 初学者源码导读](代码导读.md)
