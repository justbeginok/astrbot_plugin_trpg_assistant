# 角色卡采用「一卡一 key + 显式索引 + 活跃指针」的多卡 KV 模型

角色卡功能（v0.17.0）采用多卡模型：一个玩家（会话来源 + 发送者 ID）
可有多张命名卡，一张为活跃卡。KV key 布局：

- `character:{origin}:{sender}:{卡名}` — 卡本体（CharacterSheet.to_dict()）
- `character:index:{origin}:{sender}` — 卡名索引 `{"names": [...]}`
- `character:active:{origin}:{sender}` — 活跃卡指针 `{"name": 卡名}`
- `character:draft:{origin}:{sender}` — 车卡草稿（状态机中间态）

所有卡本体增删改与索引、活跃指针的维护在同一把管理器级 asyncio.Lock
内读-改-写，保证一致性。

## Considered Options

- **单 key 大 dict（一玩家一张卡或一 key 装全部卡）**：实现最简单，但
  一张卡的脏数据/损坏会波及其他卡；且无法表达「同一玩家在群里跑多个团
  /带随从/换卡」的现实，否决。
- **跨会话共享卡（卡只跟 sender 走）**：与既有领域约定「同一玩家在不同
  会话中是不同实体」冲突，且不同团的规则版本/开卡规则不同，否决。
- **裸字符串活跃指针**：与项目其他 KV 值形态（dict）不一致，且无法
  扩展，否决（存 `{"name": 卡名}`）。

## Consequences

- AstrBot KV 无枚举能力，**卡名索引必须显式维护**；索引写入失败会
  产生孤儿卡（卡 key 存在但 list 不可见），KV 无法扫描修复——接受该
  风险并记录告警日志。
- 删活跃卡时活跃指针回退到索引第一张或清空；重命名卡时活跃指针跟随，
  这些都是锁内原子的。
- key 布局一旦发布即产生持久化数据，后续调整需迁移逻辑（同 ADR-0001）。
- 依赖方向：chargen.py → character.py 单向，角色卡模块不感知开卡规则。
