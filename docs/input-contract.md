# 以旧换新净新增回收证据与补助范围核定资料契约

以下为项目本地合成资料，不代表真实居民、机构、金额或政策。所有数据为 UTF-8 JSON Lines，每行是一个完整对象；金额采用整数分，null 表示未知或不适用，不能自动等同于零。时间戳含 +08:00 偏移，日期为当地业务日。known_at 或 reported_at 控制资料何时可得，业务发生时间不意味着当时已经获知。

## 业务含义

申报单位为一台旧机，购新、交旧、处理均需要confirmed事实；pending和cancelled不合格，退款需核对是否取消对应购新资格，部分退款本身不自动取消。身份合并只有confirmed审核生效，保留所有原申报来历；不能按先到者直接裁定补助权益。效果目标为来源人群在约定观察期内所有渠道的规范处理净新增，不以商户所在地替代居民来源。各渠道量只可在相同人群和统计期比较，complete=false不给确定零；处理成熟等待grace_days以接收发生日起算。补助以合格台数×上限单价、整体预算和效果证据支持的联合范围共同限制，未知效果允许暂缓，不能被随意补为确定商户份额。制度不要求固定因果算法，但所用识别假设、不可识别范围和敏感性须公开可查。

## 原始记录

### submissions

| 字段 | 含义 |
| --- | --- |
| id | 申报身份 |
| merchant | 商户 |
| old_serial | 已报旧机序列号 |
| region | 商户片区 |
| resident_region | 居民来源片区 |
| category | 品类 |
| new_order | 购新事实 |
| old_receipt | 交旧事实 |
| processing | 处理事实 |
| submitted_at | 提交时点 |
| period | 补助期间 |

### transactions

| 字段 | 含义 |
| --- | --- |
| id | 事实身份 |
| kind | 购新交旧或规范处理 |
| occurred_at | 实际发生 |
| known_at | 资料到达 |
| status | pending未决、cancelled取消、confirmed确认 |
| units | 涉及台数 |
| refund_cents | 确认退款额 |
| old_serial | 旧机序列号 |

### rollout

| 字段 | 含义 |
| --- | --- |
| id | 政策片区记录 |
| region | 片区 |
| planned_start | 原定启用日 |
| actual_start | 实际启用日 |
| neighbor_regions | 可能受外溢邻区 |
| eligible_categories | 资格品类 |
| known_at | 获知时点 |

### channel_totals

| 字段 | 含义 |
| --- | --- |
| id | 渠道月记录 |
| region | 居民来源片区 |
| month | 月份 |
| channel | 回收渠道 |
| received_units | 实际接收 |
| processed_units | 最终处理 |
| population | 对应人群规模 |
| complete | 资料是否齐全 |
| known_at | 可获知时点 |

### rules

| 字段 | 含义 |
| --- | --- |
| id | 制度身份 |
| period | 补助期 |
| per_verified_unit_cents | 每合格旧机上限 |
| program_budget_cents | 整体预算 |
| effect_link | 净新增约束解释 |
| grace_days | 处理成熟等待日数 |
| unknown_effect_policy | 效果未知时的业务后果 |

### identity_reviews

| 字段 | 含义 |
| --- | --- |
| id | 审核证据 |
| left_application | 申报一 |
| right_application | 申报二 |
| same_device | 是否已确认同物 |
| known_at | 获知时间 |
| status | 证据当前是否已确认 |

