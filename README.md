# Aris Source

用于 AltStore Classic 的第三方应用源。

常规源收录：

- [Mikan / 蜜柑计划](https://github.com/iota9star/mikan_flutter)
- [MeloX](https://github.com/youshen2/MeloX)
- [Venera Prime](https://github.com/venera-app/venera-prime)

NSFW 源收录：

- [Love Iwara](https://github.com/FoxSensei001/LoveIwara)

## 添加源

常规源：

```text
https://lyaiya.github.io/altstore-aris/source.json
```

NSFW 源：

```text
https://lyaiya.github.io/altstore-aris/source-nsfw.json
```

## 更新

GitHub Actions 会每天检查上游 Release，并在有新版本时更新 `data/apps/` 中的数据并重新发布源。

每个应用保存在 `data/apps/<slug>/` 目录中：`app.json` 存放 AltStore 元数据，`upstream.toml` 存放上游 Release 的更新规则。源级元数据保存在 `data/source/` 中。

修改 `data/source/` 或 `data/apps/` 中的文件后，可在本地生成两个源文件：

```bash
python scripts/update_source.py --build-only
```
