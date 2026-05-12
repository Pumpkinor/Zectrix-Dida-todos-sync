"""
数据模型定义模块

这个文件定义了项目中用到的两个核心数据结构（dataclass）：
  - Todo:  代表一个待办任务
  - SyncLog: 代表一条同步日志记录

【Python 知识点】
  - @dataclass 是 Python 3.7+ 引入的装饰器，用于自动生成 __init__、__repr__ 等方法。
    写 @dataclass 后，只需要声明字段名和类型，Python 会自动帮你生成构造函数。
    等价于 Java 中的 Lombok @Data 或 Kotlin 的 data class。

  - Optional[str] 表示"这个值可以是字符串，也可以是 None（空值）"。
    等价于 Java 的 String? 或 TypeScript 的 string | null。

  - str = "" 是给字段设置默认值。如果创建对象时不传这个参数，就用默认值。
    没有默认值的字段（如 uid、title）在创建对象时必须传值。
"""

# 从 Python 标准库导入 dataclass 装饰器和 field 函数
from dataclasses import dataclass, field
# 从 typing 模块导入 Optional 类型提示，用于标注"可以为 None 的值"
from typing import Optional


@dataclass
class Todo:
    """
    待办任务的数据模型。

    每个 Todo 对象对应数据库 todos 表中的一行记录。
    它既可能来源于滴答清单（Dida365），也可能来源于 Zectrix 设备。

    字段说明：
      - uid:              全局唯一标识符。格式为 "dida-{滴答任务ID}" 或 "zectrix-{Zectrix任务ID}"
      - title:            任务标题
      - description:      任务描述/备注内容
      - due_date:         截止日期，格式 "YYYY-MM-DD"，如 "2026-05-09"
      - due_time:         截止时间，格式 "HH:MM"，如 "16:00"。None 表示全天任务
      - priority:         优先级。0=无, 1=重要, 2=紧急
      - completed:        是否已完成。True=已完成, False=未完成
      - completed_at:     完成时间，如 "2026-05-09 10:30:00"
      - ical_raw:         原始 iCal 格式数据（仅 iCal 模式使用，MCP 模式为空字符串）
      - last_modified:    最后修改时间，用于增量同步判断
      - synced:           是否已同步到 Zectrix。True=已同步, False=未同步（需要转发）
      - synced_at:        最后一次同步完成的时间
      - remote_id:        在 Zectrix 上的任务 ID。None 表示还没同步到 Zectrix
      - created_at:       本地创建时间
      - updated_at:       本地最后更新时间
      - reminders:        提醒设置。JSON 数组格式的字符串，来自滴答清单
                          例如: '[{"trigger":"TRIGGER:P0DT9H0M0S"}]'
      - repeat_flag:      重复规则。滴答清单格式如 "RRULE:FREQ=DAILY"，
                          Zectrix 格式如 "daily"/"weekly"/"monthly"/"yearly"
    """
    uid: str                          # 无默认值 → 创建 Todo 时必须传入
    title: str                        # 无默认值 → 创建 Todo 时必须传入
    description: str = ""             # 默认空字符串
    due_date: Optional[str] = None    # 默认 None（无截止日期）
    due_time: Optional[str] = None    # 默认 None（无具体时间 或 全天任务）
    priority: int = 0                 # 默认 0（无优先级）
    completed: bool = False           # 默认 False（未完成）
    completed_at: Optional[str] = None
    ical_raw: str = ""
    last_modified: Optional[str] = None
    synced: bool = False              # 默认 False（新建的任务还没同步过）
    synced_at: Optional[str] = None
    remote_id: Optional[str] = None   # 默认 None（还没同步到 Zectrix）
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    reminders: str = ""               # JSON array of trigger strings (Dida365 format)
    repeat_flag: str = ""             # RRULE/ERULE string (Dida365) or repeatType (Zectrix)


@dataclass
class SyncLog:
    """
    同步日志的数据模型。

    每次 sync_engine 执行同步操作时，会往数据库 sync_logs 表写入一条或多条记录，
    记录本次同步的动作类型、成功/失败状态、详情和涉及的数据条数。

    字段说明：
      - id:          自增主键，由数据库自动生成
      - action:      动作类型，如 "fetch"（抓取）、"sync"（正向同步）、"reverse_sync"（反向同步）
      - status:      执行结果，如 "success"（成功）、"failed"（失败）、"partial"（部分成功）
      - detail:      详细描述信息
      - count:       涉及的数据条数
      - created_at:  日志创建时间
    """
    id: Optional[int] = None          # 默认 None，数据库自动生成
    action: str = ""
    status: str = ""
    detail: str = ""
    count: int = 0
    created_at: Optional[str] = None
