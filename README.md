# 点金术选股系统手机版

本项目有两个版本：

- `public/`：外出手机版。部署到公网后，手机不需要和电脑在同一局域网，直接打开固定网址查看。
- `app.py`：本地调试版。只用于在电脑上测试数据接口。

## 外出手机版

外出使用的核心文件在 `public/`：

```text
public/index.html
public/data/latest.json
public/manifest.webmanifest
```

`build_static.py` 会读取 AkShare 数据，生成 `public/data/latest.json`。网页只读取这个 JSON，所以部署后手机端不需要 Python，也不需要电脑开机。

本项目已经加入 GitHub Pages 自动更新配置：

```text
.github/workflows/update-pages.yml
```

把整个文件夹上传到 GitHub 仓库后，启用 GitHub Pages，Actions 会在交易日北京时间 17:30 自动生成数据并发布网页。也可以在 GitHub Actions 页面手动点 `Run workflow` 立即更新。

## 本地测试

生成静态数据：

```powershell
C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe build_static.py
```

本地预览公网版本：

```powershell
cd public
C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe -m http.server 8770
```

然后打开：

```text
http://127.0.0.1:8770
```

## 功能

- ETF 观察：自动监控 562500、512760、563230、159326。
- A股候选：按点金术条件输出候选股。
- 静态发布：手机直接查看结果，不依赖局域网。
- PWA 支持：手机浏览器可以添加到桌面。

## 依赖

```powershell
C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe -m pip install -r requirements.txt
```
