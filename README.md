# Aris Source

用于 AltStore Classic 的第三方应用源，收录：

- [Mikan / 蜜柑计划](https://github.com/iota9star/mikan_flutter)
- [MeloX](https://github.com/youshen2/MeloX)

## 添加源

```text
https://lyaiya.github.io/altstore-aris/source.json
```

## 更新

GitHub Actions 会每天检查上游 Release，并在有新版本时更新 `apps/` 中的数据并重新发布源。也可以在 Actions 页面手动运行 `Update and publish source`。

修改 `config/source.json` 或 `apps/` 中的文件后，可在本地生成 `dist/source.json`：

```bash
python scripts/update_source.py --build-only
```
