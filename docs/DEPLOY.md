# 部署（阿里云轻量 · systemd）

**目标机**：`8.148.254.183`（阿里云轻量应用服务器，华南3·广州，实例 `Ubuntu-bnfy`）。
**上线时间**：2026-10-16，顶掉原来的 Java 版演示。

> **这份文档是记录「实际是怎么做的」，不是「计划怎么做」。**
> 原来那版写的是 Docker 方案（`Dockerfile` + `docker-compose.yml`），
> 第一次上服务器勘察后发现这台机器**根本没装 Docker**，且内存只有 1.6 GiB，
> 于是改成跟原 Java 版同构的 **systemd + venv** 路子。Docker 那几个文件仍留在仓库里
> （别的机器上还能用），但**这台机器不用它们**，见 §7。

---

## §0 现状速查

| 项 | 值 |
|---|---|
| 代码 | `/opt/travel-assistant-py/` |
| 解释器 | `/opt/travel-assistant-py/.venv/bin/python`（Python 3.10.12） |
| 服务 | `systemd` 单元 `travel-assistant-py.service`，**监听 8082**，已 `enable` |
| 对外 | nginx 占 80，`proxy_pass → 127.0.0.1:8082` |
| 数据库 | PostgreSQL 14.24，`127.0.0.1:5432`，库 `travel_assistant`，角色 `travel` |
| 密钥 | `/opt/travel-assistant-py/.env`（`chmod 600`，**不在仓库、不在镜像**） |
| 日志 | `/opt/travel-assistant-py/logs/app.log`、`logs/app-error.log` |
| 备份 | `/root/nginx-travel-assistant.conf.bak`（改 Nginx 前的原件） |

```bash
# 日常三连
systemctl status travel-assistant-py
tail -f /opt/travel-assistant-py/logs/app-error.log
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8082/
```

---

## §1 这台机器的约束（决定了方案长什么样）

| 约束 | 数值 | 影响 |
|---|---|---|
| 内存 | **1.6 GiB**（可用约 690 MiB） | 卡脖子的一条。**不能上向量检索层**（见 §6） |
| Swap | 原为 **0**，现已加 `/swapfile` 2 GiB | 没它的话 `pip install` 会被 OOM 杀掉 |
| 磁盘 | 40 G，已用 9.2 G | 够 |
| Python | 系统只有 **3.10.12** | 依赖都声明 `>=3.10`，实测可用 |
| Docker | **没装** | 所以换了 systemd 方案 |
| 另一个项目 | `interview-coach`（Java + MySQL），MySQL 常驻占 ~360 MiB | **没动它**。内存真不够时它是最大的杠杆 |

**同机还有别的项目**，任何操作前先 `ss -lntp` + `docker ps` 看一眼，别顶掉别人的东西。

---

## §2 首次部署（已执行完毕，此节留作重装参考）

### 2.1 加 swap（换机器必做，否则装依赖会被 OOM 杀）

```bash
fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
grep -q "^/swapfile" /etc/fstab || echo "/swapfile none swap sw 0 0" >> /etc/fstab
sysctl -w vm.swappiness=10
grep -q "^vm.swappiness" /etc/sysctl.conf || echo "vm.swappiness=10" >> /etc/sysctl.conf
```

`swappiness=10`：swap 只当兜底，别让常驻内存被换出去拖慢响应。

### 2.2 传代码

在**本机**仓库根目录：

```bash
rsync -az --delete \
  --exclude '.git/' --exclude '.venv/' --exclude '__pycache__/' \
  --exclude 'data/chroma/' --exclude 'tests/' --exclude 'evals/' \
  --exclude 'scripts/' --exclude '.env' --exclude '_verify/' \
  --exclude '.pytest_cache/' --exclude '.idea/' --exclude '*.log' \
  ./ root@8.148.254.183:/opt/travel-assistant-py/
```

`.env` 单独传（它被上面排除掉了），传完立刻收权限：

```bash
scp .env root@8.148.254.183:/opt/travel-assistant-py/.env
ssh root@8.148.254.183 'chmod 600 /opt/travel-assistant-py/.env'
```

> `chmod 600` 不是可选项：`/api/map/config` 会把高德前端 JS key 发给浏览器，
> 这个 key 就在 `.env` 里。

### 2.3 建 venv 装依赖

```bash
cd /opt/travel-assistant-py
apt-get install -y python3.10-venv          # Ubuntu 22.04 默认没装
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip -i https://mirrors.aliyun.com/pypi/simple/
.venv/bin/pip install -i https://mirrors.aliyun.com/pypi/simple/ -r requirements.txt
```

⚠️ 用**阿里云镜像**。海外源在这台机器上很慢。

### 2.4 Postgres

```bash
apt-get install -y postgresql                # 装到 14.24
CONF=/etc/postgresql/14/main/postgresql.conf
sed -i "s/^#*shared_buffers *=.*/shared_buffers = 64MB/"        $CONF
sed -i "s/^#*effective_cache_size *=.*/effective_cache_size = 256MB/" $CONF
sed -i "s/^#*maintenance_work_mem *=.*/maintenance_work_mem = 32MB/" $CONF
sed -i "s/^#*work_mem *=.*/work_mem = 2MB/"                     $CONF
sed -i "s/^#*max_connections *=.*/max_connections = 20/"        $CONF
systemctl restart postgresql
```

**默认参数是按大内存机器定的**（`shared_buffers=128MB` 起），1.6 GiB 的机器上必须压下来。

建角色与库，密码**服务端生成、不回显**，直接写进 `.env`：

```bash
PGPASS=$(openssl rand -hex 16)
su postgres -c "psql -c \"CREATE ROLE travel LOGIN PASSWORD '$PGPASS';\""
su postgres -c "createdb -O travel travel_assistant"
echo "TRAVEL_DB_DSN=postgresql://travel:${PGPASS}@127.0.0.1:5432/travel_assistant" >> /opt/travel-assistant-py/.env
```

> `settings.py` 里 `TRAVEL_DB_DSN` 的默认值是 `postgresql:///travel_assistant`（走 unix socket），
> 容器里/换机器上都不适用，**必须显式覆盖**。

### 2.5 systemd 单元

`/etc/systemd/system/travel-assistant-py.service`：

```ini
[Unit]
Description=Travel Assistant (Python/FastAPI)
After=network.target postgresql.service
Wants=postgresql.service

[Service]
Type=simple
User=root
WorkingDirectory=/opt/travel-assistant-py
EnvironmentFile=/opt/travel-assistant-py/.env
ExecStart=/opt/travel-assistant-py/.venv/bin/python -m travel_assistant.main
Restart=always
RestartSec=5
StandardOutput=append:/opt/travel-assistant-py/logs/app.log
StandardError=append:/opt/travel-assistant-py/logs/app-error.log

[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload && systemctl enable --now travel-assistant-py
```

> `WorkingDirectory` 必须是仓库根：`travel_assistant/kb/paths.py` 里
> `REPO_ROOT = Path(__file__).parents[2]`，`travel_assistant/` 与 `data/` 必须是兄弟目录。

### 2.6 切 Nginx

```bash
CONF=$(readlink -f /etc/nginx/sites-enabled/travel-assistant.conf)   # 实为 sites-available 下的
cp -a "$CONF" /root/nginx-travel-assistant.conf.bak
sed -i 's|127\.0\.0\.1:8081|127.0.0.1:8082|g' "$CONF"
nginx -t && systemctl reload nginx
```

**顺序很重要**：先把 nginx 指过来、验证通过，**再**停旧服务。
反过来做的话中间那段时间线上是 502。

### 2.7 停掉 Java 版

```bash
systemctl stop travel-assistant && systemctl disable travel-assistant
```

**只停不禁删**：jar、`/opt/travel-assistant/`、`travel-assistant.env` 原封不动，
`systemctl start travel-assistant` 就能起回来（起回来后要把 nginx 改回 8081）。

---

## §3 以后每次发版

```bash
# 本机
git push origin develop

# 服务器：重跑 §2.2 那条 rsync（或用 git pull），然后
cd /opt/travel-assistant-py && .venv/bin/pip install -q -r requirements.txt
systemctl restart travel-assistant-py
```

改了 `data/attractions.json` 之类的运行时文件**也要重跑 rsync** —— 它们不在代码里自更新。
改了 `requirements.txt` 才需要重跑 pip。

---

## §4 回退

**回退到 Java 版**（因为 nginx 一改就回去了，是最快的一条）：

```bash
cp -a /root/nginx-travel-assistant.conf.bak $(readlink -f /etc/nginx/sites-enabled/travel-assistant.conf)
nginx -t && systemctl reload nginx
systemctl start travel-assistant        # 它的单元还在，enabled 关掉了而已
```

**Python 版本身出问题**：

```bash
systemctl restart travel-assistant-py
journalctl -u travel-assistant-py -n 50      # 单元日志
tail -50 /opt/travel-assistant-py/logs/app-error.log
```

数据库数据在 `/var/lib/postgresql/14/main`，**别删** —— 用户账号与长期偏好都在那儿。

---

## §5 验证清单（部署/回退后照这个走一遍）

```bash
# 1. 服务活着
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8082/          # 期望 200

# 2. 走公网（最终判据）
curl -s http://8.148.254.183/ | grep -oE '<title>[^<]*</title>'          # 期望 <title>智能旅行助手</title>

# 3. 建表成功
psql "$(grep '^TRAVEL_DB_DSN=' /opt/travel-assistant-py/.env | cut -d= -f2-)" -c "\dt"
# 期望 users / preferences / episodes / sessions 四张表

# 4. 真跑一次完整流程——浏览器打开 http://8.148.254.183/ ，注册→出方案→追问
```

第 4 条**只能人工验**：它真调高德、真调模型，不是桩能替的。

---

## §6 已知限制：这台机器上检索层没上

「追问里按标签查景点」这个工具**优雅退化**了 —— 模型收到的是
「本地景点资料库还没建好（索引还没建：/opt/travel-assistant-py/data/chroma），
这次查不了，请只用方案内已有的信息作答」，然后它会退回去联网查高德。
**实测（2026-10-16）走通了这条路径，没有异常穿透。**

不用它是**内存算不过来**：`sentence-transformers`（带 torch，约 800 MB）
+ BGE-M3 权重（约 2 GB），而本机可用内存不到 700 MiB。
`chromadb` 本身**已经装了**（约 35 MB）——它是上面那条优雅退化能生效的前提：
少了它抛的是 `ModuleNotFoundError`，而 `ask_tools.py:358` 只接住了 `IndexNotBuilt`，
异常会直接穿透到工具外。**别顺手删掉 requirements.txt 里那行 chromadb。**

要真上检索层，得先升配到 4 GiB 以上，步骤见仓库根 `requirements.txt` 顶部注释
与 `travel_assistant/kb/encoder.py` 顶部注释（权重那份有 7 个断链，必须传完整权重）。

---

## §7 附：为什么这次没用 Docker

仓库里有 `Dockerfile` / `docker-compose.yml` / `.github/workflows/ci.yml`，
本机 `docker build` 与容器内 138 条景点库都验过。但这台服务器：

1. **没装 Docker** —— 为它装一套 dockerd 要多占约 100 MiB 内存，而可用内存不到 700 MiB；
2. 现有 Java 版就是 **jar + systemd + nginx** 的路子，跟着它走最省事、也最容易回退；
3. 单机单服务，Docker 的隔离与编排收益在这里体现不出来。

**Docker 那套没有删**，换机器（内存宽裕、要跑多服务）时仍然可用。

---

## 附：为什么服务器上没有把 `.env` 放进镜像/仓库

`.env` 里是真 key，且 `/api/map/config` 会把它发给前端。
`.gitignore` 挡住了入库，`.dockerignore` 挡住了进镜像，
服务器上靠 systemd 的 `EnvironmentFile=` 在运行时注入，权限收到 600。
