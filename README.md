# 网盘与电子书站点爬虫

个人研究用的搜索站抓取与分享链接核验脚本。Cookie、会话和 API Key 从环境变量或 `~/.secrets/` 读取，不进入本仓库。

## 目录

- 根目录 `*_search.py`、`aipanso_*.py`：网盘搜索站与电子书站爬虫脚本。
- `pan_list.py`：多网盘分享链接递归目录核验工具（夸克、百度、阿里、UC、迅雷、移动、天翼、123、蓝奏等）。
- `hubs/`：英文电子书与开源软件站点抓取脚本（Z-Library、OpenLibrary、Internet Archive、Gutenberg、FMHY 等）。
- `netcap/`：网页网络请求捕获辅助脚本。
- `网盘搜索站汇总.xlsx`：网盘搜索站与电子书网站评测汇总表。

同目录还有爱盘搜公共模块 `aipanso_common.py`、地瓜盘接口试探 `digua_eval_proto.py`，以及用 `pan_list` 批量核验分享链接的示例 `run_宫崎骏.py`。

脚本在已安装对应依赖（Scrapling、Playwright，以及 `hubs` 所用的 OpenCC / pypinyin）的 Python 环境中直接运行，结果默认写到 `out/`。
