# 点金术选股系统手机版

这是外出时用手机查看的公网页面，不要求手机和电脑在同一个局域网。

线上地址：

```text
https://diwuhai77-maker.github.io/dianjinshu-mobile/
```

## 工作方式

- `public/` 是手机访问的静态网页。
- `build_static.py` 负责抓取 AkShare 数据并生成 `public/data/latest.json`。
- GitHub Actions 每个交易日自动运行一次，生成新数据并发布到 GitHub Pages。
- 手机只访问 GitHub Pages，不需要电脑开机。

## 本地测试

```powershell
C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe build_static.py
cd public
C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe -m http.server 8770
```

然后打开：

```text
http://127.0.0.1:8770
```

## 数据策略

优先使用东方财富实时源，严格按 PE、股息率、ROE、市值、利润增长、MA120 进行筛选。若实时源不稳定，页面会把“严格候选”和“备用观察候选”分开显示：严格候选只来自完整条件，备用观察只用于外出时参考，避免和严格结果混淆。
