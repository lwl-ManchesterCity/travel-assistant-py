# 旅行助手 · 运行镜像。构建上下文是仓库根目录（见 docker-compose.yml）。
#
# **检索层不在这个镜像里**：chromadb / sentence-transformers / BGE-M3 走的是函数内 import
# （见 travel_assistant/kb/store.py 顶部），缺了不影响主链路、追问与长期记忆，
# 只是「按标签查景点」会返回索引未建。要启用见 docs/DEPLOY.md §7。
# 这么切是因为那三样加起来约 2.8GB（模型 2GB + torch 800MB），而它们只服务一小撮问题。

FROM python:3.12-slim

# 本机直连 pypi 会超时（见 requirements.txt 顶部注释），构建时统一走阿里云镜像。
# 用 ARG 而不是写死：换构建机（比如 GitHub Actions 的海外 runner）时可以覆盖。
ARG PIP_INDEX=https://mirrors.aliyun.com/pypi/simple/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# 先只拷 requirements 再装：改代码不会让这一层缓存失效，重建快得多。
COPY requirements.txt ./
RUN pip install --no-cache-dir -i "${PIP_INDEX}" -r requirements.txt

# 运行时真正要读的只有这两处：
#   travel_assistant/  服务本体 + static/ 下的前端
#   data/              tags.json（受控词表）与 attractions.json（景点库）
# kb/paths.py 里 REPO_ROOT = Path(__file__).parents[2]，两者**必须是兄弟目录**，
# 所以 /app 下的布局不能改，不能让 travel_assistant/ 单独挪到别处。
COPY travel_assistant/ ./travel_assistant/
COPY data/tags.json data/attractions.json ./data/

# 不用 root 跑。容器里没有写盘点（Postgres 在另一个容器），
# 但仍要给 /app 一个属主，否则 Chroma 之类将来想落盘会失败。
RUN useradd --create-home --uid 10001 app && chown -R app:app /app
USER app

EXPOSE 8080

# 端口从 SERVER_PORT 读（config/settings.py），compose 里会设。
CMD ["python", "-m", "travel_assistant.main"]
