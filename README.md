# Aris Source

用于 AltStore Classic 的第三方应用源。

常规源收录：

- [Mikan / 蜜柑计划](https://github.com/iota9star/mikan_flutter)
- [MeloX](https://github.com/youshen2/MeloX)
- [Venera Prime](https://github.com/venera-app/venera-prime)

NSFW 源单独收录：

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

GitHub Actions 会每天检查上游 Release，并在有新版本时更新 `apps/` 中的数据并重新发布源。也可以在 Actions 页面手动运行 `Update and publish source`。

修改 `config/` 或 `apps/` 中的文件后，可在本地生成两个源文件：

```bash
python scripts/update_source.py --build-only
```
