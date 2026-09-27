# Aris Source

用于 AltStore Classic 的第三方应用源，目前收录：

- [Mikan / 蜜柑计划](https://github.com/iota9star/mikan_flutter)
- [MeloX](https://github.com/youshen2/MeloX)

## 使用

将 [`source.json`](./source.json) 部署到可公开访问的 HTTPS 地址，然后在 AltStore 中添加该地址。

仓库内置的 GitHub Actions 会在以下情况发布 GitHub Pages：

- 推送到 `main` 分支；
- 每天北京时间 11:17 自动检查上游 Release；
- 在 Actions 页面手动运行 `Update and publish source`。

首次使用时，需要在仓库的 **Settings → Pages → Build and deployment → Source** 中选择 **GitHub Actions**。发布后的源地址为：

```text
https://<GitHub 用户名>.github.io/<仓库名>/source.json
```

定时或手动运行时，工作流会下载两个项目的最新 IPA，从包内 `Info.plist` 提取版本、构建号、Bundle ID、最低系统版本和隐私权限；有变化时由 `github-actions[bot]` 自动提交应用文件和 `source.json`。

每个应用分别维护在 `apps/` 目录：

- `apps/mikan.json`
- `apps/melox.json`

`source.json` 是供 AltStore 使用的合并产物。手动修改应用文件后，可以运行以下命令重新生成：

```bash
python scripts/update_source.py --build-only
```

自动提交需要仓库允许 GitHub Actions 写入内容。如果分支保护禁止机器人直接推送，可仅使用 Pages 发布，或将工作流的提交步骤改为创建 Pull Request。

## 实现参考

- [AltSource CLI（Beta）](https://faq.altstore.io/developers/altsource-cli-beta)：官方的 IPA 元数据提取方案，但目前只随 macOS 版 AltServer 提供；
- [LiveContainer 的 Update AltStore Source 工作流](https://github.com/LiveContainer/LiveContainer/blob/main/.github/workflows/update_source.yml)：社区常用的 Actions + Python + 自动提交模式；
- [GithubStore](https://github.com/yazdipour/GithubStore)：可从多个 GitHub 仓库动态生成源，但需要持续运行 Docker 服务，不适合本仓库的纯静态 Pages 部署。

因此本仓库采用与社区项目相同的 Actions 更新模式，同时用仅依赖 Python 标准库的脚本直接校验 IPA。当前两个上游发布的主应用均未签名；如果以后出现已签名 IPA，工作流会停止并要求人工核对 entitlements，避免发布与实际权限不一致的源。

## 数据说明

版本号、构建号、Bundle ID、最低 iOS 版本、权限声明和文件大小均以 GitHub Release 中实际下载的 IPA 为准。

MeloX 的 `v1.2.1` Release 页面标注构建号为 `104`，但其中 `MeloX-iOS-unsigned.ipa` 的主应用 `Info.plist` 实际为版本 `2.0`、构建号 `9110201`。AltStore 要求源数据与 IPA 完全一致，因此 `source.json` 使用 IPA 内的值。
