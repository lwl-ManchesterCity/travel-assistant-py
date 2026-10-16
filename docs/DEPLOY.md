# 部署（Docker · 单机）

**目标**：把 Python 版部署到 `8.148.254.183`，顶掉现在那里的 Java 版演示。

**这一版不含向量编码层**（sentence-transformers + BGE-M3 权重，约 2.8GB）。
追问里「按标签查景点」会**优雅退化**——模型收到的是「本地景点资料库还没建好，
这次查不了，请只用方案内已有的信息作答」（实测路径：`store.indexed_cities()` 抛
`IndexNotBuilt` → `ask_tools.py:358` 接住）。其余链路（规划、方案内追问、改方案、
长期记忆）全部正常。要补见 §7。

> `chromadb` 本身**已经装**（约 35MB，见 `requirements.txt` 里的说明）——
> 它是那道优雅退化能生效的前提。少了它抛的是 `ModuleNotFoundError`，
> 而调用方只接住了 `IndexNotBuilt`，异常会直接穿透出去。别顺手删掉。

> ## ⚠️ §1 一步都别跳
>
> 你**不确定那台机器上现在跑着什么**。直接 `compose up` 有可能顶掉别人的东西、
> 或者因为端口占用起不来。§1 全是只读命令，跑完把输出留着，再决定怎么动。

---

## §1 勘察（只读，不改任何东西）

```bash
ssh <你的用户>@8.148.254.183

# 1. 谁在占端口？重点看 80 / 443 / 8080
sudo ss -lntp

# 2. 跑着哪些容器？Java 版是不是其中一个、叫什么名字
docker ps --format "table {{.Names}}\t{{.Image}}\t{{.Ports}}\t{{.Status}}"

# 3. 有没有 Nginx 在反代？它把 80 转给了谁
nginx -v 2>&1
sudo nginx -T 2>/dev/null | grep -nE "listen|server_name|proxy_pass|root"

# 4. 有没有 systemd 托管的东西（Java 那套可能是 jar + unit）
systemctl list-units --type=service --state=running | grep -iE "travel|trip|java|nginx|docker"

# 5. 磁盘和内存
df -h /; free -h

# 6. Docker 版本——`docker compose`（v2，带空格）和 `docker-compose`（v1）不是一回事
docker --version; docker compose version 2>&1 | head -1
```

**照输出对号入座**：

| 看到什么 | 说明什么 | 怎么替换 |
|---|---|---|
| `docker ps` 里有个容器占着 `0.0.0.0:80->...` | Java 版是个容器，直接对外 | §5 停它，把 80 让给新服务 |
| 80 上是 **Nginx**，`proxy_pass` 指向某个内部端口 | Java 版在 Nginx 后面 | §5 只改 `proxy_pass`，Nginx 别动 |
| 80 上有 systemd 托管的 jar | Java 版是裸进程 | §5 先 `systemctl stop` 再切 |
| 8080 已被占用 | 我们的默认宿主端口撞了 | §3 把 `docker-compose.yml` 里 `"8080:8080"` 换成别的（如 `"18080:8080"`） |
| `df -h /` 剩余 < 5GB | 装不下检索层 | §7 先别做 |

---

## §2 把代码和密钥传上去

在**本机**：

```bash
cd /Users/lwl/Documents/langchain-旅游智能体/travel-assistant-py

# 只传部署要的东西。.venv / data/chroma / _verify 都不传（见 .dockerignore 同理）。
rsync -av --delete \
  --exclude '.git' --exclude '.venv' --exclude '__pycache__' \
  --exclude 'data/chroma' --exclude 'tests' --exclude 'evals' \
  --exclude 'docs' --exclude 'scripts' --exclude '.env' \
  ./  <你的用户>@8.148.254.183:~/travel-assistant-py/
```

在**服务器**上建 `.env`：

```bash
cd ~/travel-assistant-py
cp .env.example .env
chmod 600 .env          # 里面有真 key，且 /api/map/config 会把它发给前端
vim .env                # 填下面这几项
```

**必须填的**：

| 变量 | 填什么 |
|---|---|
| `TRAVEL_LLM_API_KEY` | 通义（DashScope）的 key |
| `AMAP_API_KEY` | 高德 **Web 服务** key |
| `AMAP_WEB_JS_KEY` | 高德 **Web 端(JS API)** key——前端渲染地图用 |
| `AMAP_SECURITY_JS_CODE` | 与之配套的安全密钥 |
| `POSTGRES_PASSWORD` | **新加一行**，自己定一个强密码（只在 compose 内部用） |

其余保持 `.env.example` 的默认值即可。`TRAVEL_DB_DSN` **不用改** ——
`docker-compose.yml` 里会用 compose 网络里的地址覆盖它。

> 高德那两个 key 是**两套不同的东西**，都要申请：Web 服务用于后端查 POI / 路线，
> Web 端用于浏览器里画地图。少一个的表现是「页面能出方案但地图是空白」。

---

## §3 起服务（先只在本机端口验证，不碰线上）

```bash
cd ~/travel-assistant-py
docker compose up -d --build
docker compose ps                 # 两个都该是 running，db 要 healthy
docker compose logs -f app        # Ctrl-C 退出，看有没有报错
```

**验证**（在服务器上，或从你的电脑）：

```bash
# 服务活着
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8080/
# 期望 200

# 建表成功（长期记忆那三张表在不在）
docker compose exec db psql -U travel -d travel_assistant -c "\dt"
# 期望看到 users / preferences / episodes / sessions

# 真跑一次规划——这一步才验证得动高德与模型
# 浏览器打开 http://8.148.254.183:8080/ ，走一遍完整流程
```

**这一步跑通了再往下。** 没跑通就看 `docker compose logs app`，
最常见的两个原因：`.env` 里 key 没填对、或 8080 被占。

---

## §4 让简历上那个地址指过来

按 §1 的勘察结果选一条。**三条都只要一分钟，且都能回退。**

**情况 A：Java 版是个容器占着 80**

```bash
docker stop <那个容器名>
docker update --restart=no <那个容器名>      # 别让它开机自启又抢回 80
# 然后把 docker-compose.yml 的端口从 "8080:8080" 改成 "80:8080"
docker compose up -d app
```

**情况 B：Nginx 反代（最常见）**

不动 Python 服务，只改 Nginx 的转发目标：

```bash
sudo vim /etc/nginx/conf.d/xxx.conf
#   把 location / 里的 proxy_pass 改成 http://127.0.0.1:8080;
sudo nginx -t && sudo systemctl reload nginx
```

**情况 C：systemd 托管的 Java jar**

```bash
sudo systemctl stop <服务名> && sudo systemctl disable <服务名>
# 再按情况 A 或 B 让 8080 对外
```

改完确认：

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://8.148.254.183/
# 期望 200，且浏览器打开是新的界面（助手页 / 明亮度假风，不是旧的票据风）
```

---

## §5 回退（出事就照这个走）

Python 版是**独立容器 + 独立库**，Java 版的东西一个字节都没改，
所以回退就是「把它停掉、把原来的恢复回来」：

```bash
cd ~/travel-assistant-py
docker compose down                     # 停掉 app 和 db（数据卷 pgdata 保留）

# 然后按 §4 的反向操作恢复：
#   A：docker start <那个容器名> && docker update --restart=always <那个容器名>
#   B：把 proxy_pass 改回去，nginx -t && systemctl reload nginx
#   C：systemctl start <服务名>
```

**别跑 `docker compose down -v`** —— 那个 `-v` 会把 `pgdata` 数据卷一起删掉，
用户账号和长期偏好全没。

---

## §6 更新代码（以后每次发版）

```bash
# 本机
git push origin develop
# 服务器
cd ~/travel-assistant-py && git pull        # 若当初用 rsync 传的，就重跑 §2 那条 rsync
docker compose up -d --build
```

改了 `data/attractions.json` 之类的运行时要读的文件，**必须 `--build`** ——
它们是 `COPY` 进镜像的，不重建不会生效。

---

## §7 之后补检索层（这次故意没上）

要装的东西：`chromadb` + `sentence-transformers`（带 torch，约 800MB）
+ BGE-M3 权重（约 2GB）。三处改动：

1. `requirements.txt` 加 `sentence-transformers`（`chromadb` 已经在里面了，
   别重复加、也别删）。
2. 把本机 `~/Documents/bge-m3/snapshots/master` 传到服务器，
   `.env` 里加 `TRAVEL_EMBED_MODEL_PATH=<服务器上的路径>`。
   ⚠️ 必须传**这一份完整权重**，不能用 HuggingFace 缓存那份——
   那份有 7 个断链（原因见 `travel_assistant/kb/encoder.py` 顶部注释）。
3. 在服务器上建索引：`docker compose exec app python -m scripts.kb.build_index`
   （需要 `scripts/` 进镜像，见 §6 对 `--build` 的说明）。

代价：首次编码约 5.9s、常驻内存 +0.87GB。所以它不是免费的，
只有真的要用「按标签语义查景点」才值得加。

---

## 附：为什么镜像里没有 .env

镜像层可以被 `docker save` 导出来、也能被推到 registry，**一旦 COPY 进镜像，
密钥就写进了一个不方便删掉的地方**（哪怕下一层删了，`docker history` 里还在）。
所以 `.dockerignore` 明确排除了 `.env`，运行时靠 `env_file` 注入。
