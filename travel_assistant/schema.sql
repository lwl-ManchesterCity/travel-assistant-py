-- 用户库的建表语句。由 db.get_pool() 在第一次用到数据库时幂等执行，不上 alembic。
--
-- 第一档（登录）两张表，第二档（记偏好）加第三张，第四档（场景）加第四张，
-- 见 docs/ASK_LONGTERM_MEMORY.md §2.2 与 §2.4。
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

-- 第二档：这个人的长期偏好（docs/ASK_LONGTERM_MEMORY.md §2.2.1）；
-- 第五档加 subject 并把去重键改成三列（§2.5.1 / §2.5.2）。
--
-- 去重**交给库**：unique (user_id, subject, content) 配 on conflict do nothing，
-- 一条语句里完成"判重 + 写入"。先查后插中间有窗口，两个请求同时到就会进去两条。
--
-- **去重键为什么是三列**：「本人不能吃辣」和「老婆不能吃辣」是两条不同的信息。
-- 两列键之下它们会撞，后说的被 on conflict do nothing **静默吞掉**，
-- 而界面上看起来"记了"——这是第五档最隐蔽的一处（§2.5.2）。
--
-- subject 是**自由文本**（本人 / 老婆 / 朋友 / 我妈），不做归一化：老婆可能每趟都在、
-- 朋友就那一趟，这个区别本身就是有用的信息（§2.5.1）。
--
-- 故意**不建 updated_at**：偏好没有"修改"这个动作。要改就删掉说条新的（第三档），
-- 留 updated_at 等于鼓励"就地改"，而"就地改"最难的是判断哪条该被改。
create table if not exists preferences (
    id         bigserial   primary key,
    user_id    bigint      not null references users (id) on delete cascade,
    subject    text        not null default '本人',
    content    text        not null,
    source     text        not null,
    created_at timestamptz not null default now(),
    unique (user_id, subject, content)
);

-- 不另建索引：注入时那句 where user_id = %s 走上面 unique 约束的最左前缀。

-- 第五档的改列迁移（docs/ASK_LONGTERM_MEMORY.md §2.5.2）。
--
-- **上面那条 create table 只帮新库**：`create table if not exists` 对已存在的表
-- **整条跳过**，所以老库里的 preferences 没有 subject、去重键还是两列——
-- 下面这两段才是把它们改过来的东西。两段都幂等，每次启动跑一遍都安全。
--
-- **部署注意**：本机跑过、服务器没跑，两边的表就不一样了。改列之后的部署步骤里
-- 必须含这两段 SQL——这是本项目第一次"部署时要做代码之外的事"。
--
-- 约束名依赖 Postgres 的自动命名（`<表>_<列>_key`），脆；所以 drop 用了 if exists，
-- 不存在也不报错。**改约束名之后，别处如果硬编码过约束名要一起改**
-- （postgres_preference_store.py 里没有硬编码，靠的是 on conflict 的列名）。

alter table preferences add column if not exists subject text not null default '本人';

alter table preferences drop constraint if exists preferences_user_id_content_key;
do $$
begin
  if not exists (select 1 from pg_constraint
                 where conname = 'preferences_user_id_subject_content_key') then
    alter table preferences
      add constraint preferences_user_id_subject_content_key
      unique (user_id, subject, content);
  end if;
end $$;

-- 第四档：情节记忆 / 场景（docs/ASK_LONGTERM_MEMORY.md §2.4.1）。
-- 偏好回答"他是谁"，这张表回答"上次发生了什么"。
--
-- **city not null 是这一档的核心约束**，不是顺手加的：取的时候按城市捞（§2.4.3），
-- 没地点就捞不出来，存了等于没存。闸门开在写入端，不在读取端。
--
-- 去重键是**三列**：「上次杭州那家隔音差」与「上次成都那家隔音差」是两条，不能撞。
create table if not exists episodes (
    id         bigserial   primary key,
    user_id    bigint      not null references users (id) on delete cascade,
    city       text        not null,
    content    text        not null,
    source     text        not null,
    created_at timestamptz not null default now(),
    unique (user_id, city, content)
);

-- 不另建索引：注入那句 where user_id = %s and city = %s 走上面 unique 约束的
-- 最左前缀 (user_id, city)。注意**按人计数**（每人 50 条上限）那句是 where user_id = %s，
-- 也走同一个前缀。
