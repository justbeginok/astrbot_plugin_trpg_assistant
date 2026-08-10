# 战斗字段 base 层由规则引擎自动计算（双层模型接管）

v0.18.0 起，角色卡战斗字段（HP/AC/法术位/攻击加值）的 **base 层由
`chargen_engine.py` 规则引擎按职业/种族/背景/装备槽自动计算**；`bonus`
层保持 v0.17 的语义——房规调整，`/卡 设 hp/ac/法术位N/攻击` 仍写 bonus，
任何自动重算都不覆盖。v0.17 的「base 恒 0、手动值写 bonus、加值由 LLM
引导 + DM 复核」方案退役。

## Considered Options

- **数据管线独立 JSON**：把职业生命骰/施法进度/加值做成插件自带 JSON。
  否决——要另造一套分发/版本化/在线更新通道，与现有知识库基建
  （schema 版本化、随包打包、kb_update.db 优先级、resolve_db_path 回退）
  重复；且加值/战斗数据本就与知识库同源（5etools-cn），分开两份必然漂移。
  最终选择：**扩展 build_kb.py 到 schema v4 加结构化侧表**（ADR-0002 同款
  理由），kb.py 提供专表专查方法，引擎只消费查询层。
- **法术位表从 classTable 读取**：镜像站 classTable 行被剥空（非空行是
  狂暴次数/已知法术等资源列，不含法术位），否决；按 casterProgression
  硬编码标准表（full/half/third/artificer/pact，模块常量 FULL_SLOTS/
  PACT_SLOTS），2024 圣武士/游侠在源数据中为 artificer（向上取整），
  双轨由字段值驱动、无需特判职业名。
- **加值由 LLM 继续引导（维持 v0.17）**：违背 v0.18「全自动」目标；
  2014 种族平铺加值可直接结构化，2024 背景 weighted choose 是确定性的
  方案选择（+2/+1 或 +1/+1/+1），插件列出方案让玩家选即可，无需 LLM
  计算。最终选择：状态机插入「加值选择」步，落库时自动叠加（clamp 1-30）。
- **HP 掷骰法**：升阶时让玩家掷 HP 骰。否决——引擎要确定性输出，
  采用固定期望值 ⌊faces/2⌋+1（首职首级满骰），房规可经 bonus 层调整。

## Consequences

- **只动 base 不动 bonus**：引擎任何重算都不得覆盖 bonus，房规特许
  永不与自动重算冲突；攻击加值采用「生成集内重算 base 保 bonus、生成集外
  整体保留」写回策略，玩家自建条目不丢。
- **触发时机**：车卡落库前（_finalize）、`/卡 升级`、`/卡 设` 改装备槽后
  （update_fields 先落库、重算后再 save 一次，防止 base 变化丢失）。
- **依赖方向**：engine → character/kb 单向，character.py 不反向依赖引擎
  （level_up 的重算经 recalc_fn 注入）；引擎为纯函数模块，无管理器实例。
- **已知缺口**：PHB 人类（2014）+1 全属性在源数据无结构化字段
  （ability: null），不自动叠加；攻击条目按「生成集外保留」兜底，改名
  武器后旧条目残留为手动条目（报告中不删除，避免误伤房规数据）。
- **知识库升级成本**：schema v4 需重跑 build_kb.py；旧 v3 库经
  resolve_db_path 自动回退内置库，部署时须随包替换 dnd_kb.db。
