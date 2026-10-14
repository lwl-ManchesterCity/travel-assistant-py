-- 用户库的建表语句。由 db.get_pool() 在第一次用到数据库时幂等执行，不上 alembic。
--
-- 第一档（登录）两张表，第二档（记偏好）加第三张，见 docs/ASK_LONGTERM_MEMORY.md §2.2。
--
-- **边界要记牢**：`create table if not exists` 对**已存在的表整条跳过**，
-- 所以改列它一个字都不会做——加个字段、改个类型，这里写了也没用。
-- 哪天真要改列，就得引入 alembic，别指望这个文件。

create table if not exists users (
    id            bigserial   primary key,
    username      text        not null unique,
    -- bcrypt 的完整输出（含算法版本与盐），长度 60 字符左右。
    password_hash text        not null,
    created_at    timestamptz not null default now()
);

create table if not exists sessions (
    -- 随机 token 本身做主键：查会话就是一次主键命中，不需要另建索引。
    token      text        primary key,
    user_id    bigint      not null references users (id) on delete cascade,
    created_at timestamptz not null default now(),
    expires_at timestamptz not null
);

-- 本次用不到（查会话走 token 主键）。留着是给"列出某人的所有登录设备 / 一键登出全部"
-- 那类需求——那时按 user_id 找会话，没这个索引就要全表扫。
create index if not exists sessions_user_id_idx on sessions (user_id);

-- 第二档：这个人的长期偏好（docs/ASK_LONGTERM_MEMORY.md §2.2.1）。
--
-- 去重**交给库**：unique (user_id, content) 配 on conflict do nothing，
-- 一条语句里完成"判重 + 写入"。先查后插中间有窗口，两个请求同时到就会进去两条。
--
-- 故意**不建 updated_at**：偏好没有"修改"这个动作。要改就删掉说条新的（第三档），
-- 留 updated_at 等于鼓励"就地改"，而"就地改"最难的是判断哪条该被改。
create table if not exists preferences (
    id         bigserial   primary key,
    user_id    bigint      not null references users (id) on delete cascade,
    content    text        not null,
    source     text        not null,
    created_at timestamptz not null default now(),
    unique (user_id, content)
);

-- 不另建索引：注入时那句 where user_id = %s 走上面 unique 约束的最左前缀。
