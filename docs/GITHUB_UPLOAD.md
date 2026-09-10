# 上传 GitHub

建议先创建**私有空仓库**。此处只准备文件，不代为创建或推送。
GitHub 网页拖拽上传不读取 `.gitignore`；请按下列范围选择文件，或使用 Git 暂存后审阅。

## 应上传

- README.md、AGENTS.md、.gitignore、pyproject.toml、uv.lock。
- travel_record/（排除 __pycache__）、tests/、docs/、example_*.txt。
- 生成器/index.html、app.js、style.css、data.js、README.md、scripts/、data/source/。

生成器的 data.js 和 SQLite 及衍生映射在**公开发布前**应确认来源与再分发许可证。
目前不能替原作者选择许可证。私有仓库也不代表获得向其他人再分发的权利。
当前 README 已记录构建脚本的外部模块缺失；不要把“静态页面可用”写成“数据构建可复现”。

## 不上传

`.venv/`、`.cache/`、output/、*.egg-info/、__pycache__/、.pytest_cache/、node_modules/、
生成器/data/cache/、.DS_Store、.env*、record.txt、record.cleaned.txt。
个人记录可在新电脑另行复制；默认忽略的仅是这两个固定文件名，其他自定义行程文件仍须人工检查。
地图缓存不随 Git 分发，新电脑首次联网较慢；需要离线迁移时另行传输缓存并保留来源/许可证。

## 推荐本地上传方式

创建 GitHub 空仓库后，在项目根目录执行：

```sh
git init
git add README.md AGENTS.md .gitignore pyproject.toml uv.lock travel_record tests docs example_*.txt 生成器
git status --short
git diff --cached --stat
```

确认没有个人行程、密钥、缓存、大视频，确认数据授权和拟定的仓库可见性后：

```sh
git commit -m "Add travel itinerary generator and map video renderer"
git branch -M main
git remote add origin <你创建的仓库URL>
git push -u origin main
```

把 URL 占位符换成真实地址。若目录已经是仓库或已有 origin，先查看现有配置再调整。
`.gitignore` 不会自动移除已跟踪文件；提交前仍需审阅暂存内容。
