"""
Zectrix 转发器模块 (Zectrix Forwarder Module)
===============================================

本文件实现了 BaseForwarder 的子类——ZectrixForwarder，
用于将 Dida365（滴答清单）的待办事项同步到 Zectrix 云平台。

【什么是 Zectrix？】
Zectrix 是一个物联网/设备管理平台，提供待办事项管理 API。
本模块通过 Zectrix 的开放 API（/open/v1/todos）来管理远程待办事项。

【本模块的主要功能】
1. 在 Zectrix 上创建待办事项（create_todo）
2. 更新 Zectrix 上已有的待办事项（update_todo）
3. 在 Zectrix 上标记待办事项为已完成（complete_todo）
4. 从 Zectrix 上删除待办事项（delete_todo）
5. 从 Zectrix 拉取所有待办事项（fetch_remote_todos）

【Python 概念：模块导入】
- import logging     导入 Python 标准库的日志模块
- from typing import Optional  导入类型提示工具 Optional
- import httpx       导入第三方 HTTP 客户端库 httpx（支持异步请求）
- from app.models import Todo  导入本项目的待办事项数据模型
- from app.services.forwarders.base import BaseForwarder  导入转发器基类

【Python 概念：logging 模块】
logging 是 Python 内置的日志记录模块，用于输出运行信息。
- logger.info("消息")   记录一般信息
- logger.error("消息")  记录错误信息
- logger.warning("消息") 记录警告信息
日志可以帮助开发者了解程序运行情况，排查问题。
"""

# 导入 Python 标准库的日志模块，用于记录程序运行信息
import logging

# 从 typing 模块导入 Optional 类型提示工具
# Optional[dict] 表示"可以是 dict 类型，也可以是 None"
# 例如：Optional[dict] 等价于 dict | None（Python 3.10+ 的写法）
from typing import Optional

# 导入 httpx 库 —— 一个现代化的 HTTP 客户端，支持异步请求
# httpx 是 Python 中常用的 HTTP 请求库，类似于 requests，但支持 async/await
# 如果没有安装，需要运行：pip install httpx
import httpx

# 从本项目的 app.models 模块导入 Todo 数据模型
# Todo 是一个数据类，包含待办事项的所有信息
from app.models import Todo

# 从 base.py 导入 BaseForwarder 抽象基类
# ZectrixForwarder 需要继承它并实现所有抽象方法
from app.services.forwarders.base import BaseForwarder

# 创建一个 logger 实例，__name__ 会自动设为当前模块的名称
# 即 "app.services.forwarders.zectrix"
# 这样在日志输出时可以知道是哪个模块产生的日志
logger = logging.getLogger(__name__)

# ----------------------------------------------------------------------
# Dida365 重复规则到 Zectrix 重复类型的映射字典
# ----------------------------------------------------------------------
# 【Python 概念：字典 (dict)】
# 字典是 Python 中的键值对数据结构，用大括号 {} 创建。
# 例如：{"key1": "value1", "key2": "value2"}
# 通过 key 可以查找对应的 value：my_dict["key1"] 得到 "value1"
#
# 这里的映射关系：
# Dida365 使用标准的日历重复规则格式（RRULE），例如 "FREQ=DAILY" 表示每天重复
# Zectrix 使用自己的简化格式，例如 "daily"
# 这个字典就是两种格式之间的转换表
_REPEAT_MAP = {
    "DAILY": "daily",      # 每天重复
    "WEEKLY": "weekly",    # 每周重复
    "MONTHLY": "monthly",  # 每月重复
    "YEARLY": "yearly",    # 每年重复
}


def _dida_repeat_to_zectrix(repeat_flag: str) -> str:
    """
    将 Dida365 的重复规则字符串转换为 Zectrix 的重复类型。

    【什么是 repeat_flag？】
    Dida365 使用 iCalendar 标准的 RRULE/ERULE 格式来描述重复规则。
    例如："FREQ=WEEKLY;INTERVAL=2" 表示每两周重复一次。
    我们需要从中提取 "FREQ=" 后面的值，转换为 Zectrix 格式。

    【Python 概念：函数定义】
    def 表示定义一个函数。
    函数名前的下划线 _ 前缀是 Python 的命名惯例，表示这是一个"内部函数"，
    不应该被模块外部直接调用（但 Python 不会强制限制）。

    【参数】
    repeat_flag : Dida365 的重复规则字符串，例如 "FREQ=DAILY;INTERVAL=1"
                  如果为空字符串或 None，表示不重复。

    【返回值】
    str : Zectrix 格式的重复类型，值为 "daily"/"weekly"/"monthly"/"yearly"/"none"
    """
    if not repeat_flag:
        # 如果 repeat_flag 为空字符串或 None，说明不重复，返回 "none"
        return "none"
    # 将字符串转为大写，方便统一比较（不区分大小写）
    # 例如 "freq=daily" 也能被正确识别
    upper = repeat_flag.upper()
    # 遍历映射字典中的每一对键值
    # 【Python 概念：dict.items()】
    # .items() 返回字典中所有的 (key, value) 对
    for key, val in _REPEAT_MAP.items():
        # 检查 repeat_flag 中是否包含 "FREQ=XXX"
        # 例如 upper 中包含 "FREQ=DAILY"，而 key 是 "DAILY"，则匹配成功
        if f"FREQ={key}" in upper:
            return val
    # 如果没有匹配到任何已知格式，返回 "none"（不重复）
    return "none"


class ZectrixForwarder(BaseForwarder):
    """
    Zectrix 平台的转发器实现。

    继承自 BaseForwarder，实现了所有抽象方法，
    通过 Zectrix 的开放 API 与 Zectrix 云平台交互。

    【Python 概念：继承】
    class ZectrixForwarder(BaseForwarder) 表示 ZectrixForwarder 继承自 BaseForwarder。
    这意味着 ZectrixForwarder 必须实现 BaseForwarder 中定义的所有 @abstractmethod 方法。
    """

    def __init__(self, api_key: str, device_id: str, base_url: str = "https://cloud.zectrix.com"):
        """
        初始化 Zectrix 转发器。

        【Python 概念：__init__ 方法】
        __init__ 是 Python 类的"构造方法"（初始化方法）。
        当你创建类的实例时（例如 forwarder = ZectrixForwarder(api_key, device_id)），
        Python 会自动调用 __init__ 方法来初始化对象的属性。

        【Python 概念：默认参数】
        base_url: str = "https://cloud.zectrix.com" 是一个带默认值的参数。
        如果调用时不传 base_url，就使用默认值；
        如果传了新值，就用新值覆盖默认值。

        【参数】
        api_key   : Zectrix 平台的 API 密钥，用于身份验证
        device_id : Zectrix 平台上的设备 ID，标识是哪个设备的待办事项
        base_url  : Zectrix API 的基础 URL 地址（默认为官方云平台地址）
        """
        # 将参数保存为对象的属性（实例变量）
        # self.xxx 表示"这个对象自己的 xxx 属性"
        self.api_key = api_key
        self.device_id = device_id
        # base_url 末尾可能带有斜杠 "/"，例如 "https://cloud.zectrix.com/"
        # .rstrip("/") 会去掉末尾的斜杠，统一格式，避免拼接 URL 时出现双斜杠
        self.base_url = base_url.rstrip("/")

    def _headers(self) -> dict:
        """
        生成发送给 Zectrix API 的 HTTP 请求头。

        【返回值】
        dict : 包含认证信息和内容类型的请求头字典。
               例如：{"X-API-Key": "xxx", "Content-Type": "application/json"}

        【请求头说明】
        - X-API-Key : API 密钥，Zectrix 用它来验证你的身份和权限
        - Content-Type : 告诉服务器我们发送的数据格式是 JSON
        """
        return {
            "X-API-Key": self.api_key,        # API 认证密钥
            "Content-Type": "application/json",  # 请求体的数据格式为 JSON
        }

    async def _request(self, method: str, path: str, json_data: Optional[dict] = None) -> dict:
        """
        向 Zectrix API 发送 HTTP 请求的通用方法。

        这是所有 API 调用的底层方法，负责实际的网络通信。

        【Python 概念：async with（异步上下文管理器）】
        async with ... as client: 是异步版本的 with 语句。
        with 语句用于自动管理资源的获取和释放（如打开文件、建立网络连接）。
        当 with 代码块结束时，资源会自动释放，即使发生错误也不例外。
        这里 httpx.AsyncClient 是一个 HTTP 客户端，async with 确保请求完成后连接被正确关闭。

        【Python 概念：await】
        await 表示"等待异步操作完成"。
        例如 await client.request(...) 表示发送 HTTP 请求并等待响应返回。
        在等待期间，Python 可以去执行其他任务，不会阻塞。

        【参数】
        self      : 当前对象实例
        method    : HTTP 请求方法，例如 "GET"、"POST"、"PUT"、"DELETE"
        path      : API 路径，例如 "/open/v1/todos"（会拼接到 base_url 后面）
        json_data : 要发送的 JSON 数据（可选）。如果为 None，则不发送请求体。

        【返回值】
        dict : Zectrix API 返回的 JSON 响应数据，已解析为 Python 字典。

        【异常】
        如果 API 返回错误状态码（如 404、500），raise_for_status() 会抛出异常。
        """
        # 拼接完整的请求 URL
        # 例如 base_url="https://cloud.zectrix.com"，path="/open/v1/todos"
        # 拼接后 url="https://cloud.zectrix.com/open/v1/todos"
        url = f"{self.base_url}{path}"

        # 创建一个异步 HTTP 客户端，设置超时时间为 30 秒
        # async with 确保请求完成后客户端被正确关闭
        # timeout=30 表示如果 30 秒内没有收到响应，就放弃请求
        async with httpx.AsyncClient(timeout=30) as client:
            # 发送 HTTP 请求并等待响应
            # - method: HTTP 方法（GET/POST/PUT/DELETE）
            # - url: 完整的请求地址
            # - headers=self._headers(): 请求头（包含 API 密钥）
            # - json=json_data: 请求体的 JSON 数据（会自动序列化为 JSON 字符串）
            response = await client.request(
                method, url, headers=self._headers(), json=json_data
            )
            # 检查响应状态码，如果不是 2xx（成功），则抛出异常
            # 例如：404 會抛出 httpx.NotFoundError，500 会抛出 httpx.ServerError
            response.raise_for_status()
            # 将响应体中的 JSON 数据解析为 Python 字典并返回
            # 例如响应 '{"data": {"id": 123}}' 会被解析为 {"data": {"id": 123}}
            return response.json()

    async def create_todo(self, todo: Todo) -> str:
        """
        在 Zectrix 平台上创建一个新的待办事项。

        【业务流程】
        1. 将 Dida365 的待办事项数据转换为 Zectrix 的格式
        2. 通过 Zectrix API 创建待办事项
        3. 返回 Zectrix 分配的待办事项 ID，用于后续同步

        【参数】
        self : 当前对象实例
        todo : Todo 对象，包含待办事项的所有信息

        【返回值】
        str : Zectrix 平台上该待办事项的唯一 ID（远程 ID）
        """
        # 将 Dida365 的重复规则转换为 Zectrix 的重复类型
        repeat_type = _dida_repeat_to_zectrix(todo.repeat_flag)

        # 构建发送给 Zectrix API 的请求体（JSON 数据）
        # 【Python 概念：字典字面量】
        # body 是一个字典，包含了创建待办事项所需的各个字段
        body = {
            "title": todo.title,                    # 待办事项的标题
            "description": todo.description or "",  # 描述，如果为 None 则用空字符串代替
            # 【Python 概念：or 运算符的短路特性】
            # todo.description or "" 的含义：
            # 如果 todo.description 有值（非空非 None），就用它的值；
            # 如果 todo.description 为 None 或空字符串，就用 ""（空字符串）。
            "priority": todo.priority,              # 优先级（数值）
            "deviceId": self.device_id,             # 关联的设备 ID
            "repeatType": repeat_type,              # 重复类型（daily/weekly/monthly/yearly/none）
        }

        # 如果有待办事项的截止日期，添加到请求体中
        # 【Python 概念：条件添加字典键】
        # 只有在 todo.due_date 有值时才添加 "dueDate" 字段
        if todo.due_date:
            body["dueDate"] = todo.due_date
        # 如果有待办事项的截止时间，添加到请求体中
        if todo.due_time:
            body["dueTime"] = todo.due_time

        # 向 Zectrix API 发送 POST 请求，创建待办事项
        # API 端点：POST /open/v1/todos
        result = await self._request("POST", "/open/v1/todos", body)

        # 从响应中提取新创建的待办事项 ID
        # result 的结构通常是：{"data": {"id": 12345}}
        # result.get("data", {}) 安全地获取 "data" 字段，如果不存在则返回空字典 {}
        # .get("id", "") 安全地获取 "id" 字段，如果不存在则返回空字符串 ""
        # str() 确保将 ID 转换为字符串类型（即使它是数字）
        remote_id = str(result.get("data", {}).get("id", ""))

        # 记录日志：创建成功，包含远程 ID 和待办事项标题
        # f"..." 是 Python 的 f-string（格式化字符串），可以嵌入变量
        logger.info(f"Created Zectrix todo: {remote_id} ({todo.title})")

        # 返回远程 ID，后续更新和删除操作需要用到
        return remote_id

    async def update_todo(self, remote_id: str, todo: Todo):
        """
        更新 Zectrix 平台上已存在的待办事项。

        【参数】
        self      : 当前对象实例
        remote_id : Zectrix 平台上该待办事项的唯一 ID
        todo      : 包含最新数据的 Todo 对象

        【返回值】
        无
        """
        # 将 Dida365 的重复规则转换为 Zectrix 的重复类型
        repeat_type = _dida_repeat_to_zectrix(todo.repeat_flag)

        # 构建更新请求体（与创建类似，但不需要 deviceId）
        body = {
            "title": todo.title,                    # 更新后的标题
            "description": todo.description or "",  # 更新后的描述
            "priority": todo.priority,              # 更新后的优先级
            "repeatType": repeat_type,              # 更新后的重复类型
        }

        # 如果有截止日期，添加到请求体
        if todo.due_date:
            body["dueDate"] = todo.due_date
        # 如果有截止时间，添加到请求体
        if todo.due_time:
            body["dueTime"] = todo.due_time

        # 向 Zectrix API 发送 PUT 请求，更新指定的待办事项
        # API 端点：PUT /open/v1/todos/{remote_id}
        # f"/open/v1/todos/{remote_id}" 中的 {remote_id} 会被替换为实际的 ID
        # 例如：PUT /open/v1/todos/12345
        await self._request("PUT", f"/open/v1/todos/{remote_id}", body)

        # 记录日志：更新成功
        logger.info(f"Updated Zectrix todo: {remote_id}")

    async def complete_todo(self, remote_id: str):
        """
        在 Zectrix 平台上将待办事项标记为已完成。

        【参数】
        self      : 当前对象实例
        remote_id : Zectrix 平台上该待办事项的唯一 ID

        【返回值】
        无

        【注意】
        与 update_todo 不同，完成操作不需要发送待办事项的详细数据，
        只需要知道是哪个待办事项（通过 remote_id），
        Zectrix API 会自动将其状态设为"已完成"。
        """
        # 向 Zectrix API 发送 PUT 请求，标记待办事项为已完成
        # API 端点：PUT /open/v1/todos/{remote_id}/complete
        # 注意：这里没有传 json_data 参数（即 json_data=None），因为不需要发送请求体
        await self._request("PUT", f"/open/v1/todos/{remote_id}/complete")

        # 记录日志：完成成功
        logger.info(f"Completed Zectrix todo: {remote_id}")

    async def delete_todo(self, remote_id: str):
        """
        从 Zectrix 平台上删除一个待办事项。

        【参数】
        self      : 当前对象实例
        remote_id : Zectrix 平台上该待办事项的唯一 ID

        【返回值】
        无

        【警告】
        删除操作是不可逆的！删除后 Zectrix 平台上该待办事项将永久消失。
        """
        # 向 Zectrix API 发送 DELETE 请求，删除指定的待办事项
        # API 端点：DELETE /open/v1/todos/{remote_id}
        # 同样不需要请求体，只需要告诉 API 要删除哪个待办事项
        await self._request("DELETE", f"/open/v1/todos/{remote_id}")

        # 记录日志：删除成功
        logger.info(f"Deleted Zectrix todo: {remote_id}")

    async def fetch_remote_todos(self) -> list[dict]:
        """
        从 Zectrix 平台拉取所有待办事项。

        【用途】
        在同步过程中，需要获取 Zectrix 上当前所有的待办事项，
        与 Dida365 的数据进行对比，判断哪些需要创建、更新或删除。

        【返回值】
        list[dict] : 待办事项字典列表。每个字典代表一个待办事项，
                     包含 id、title、description、priority 等字段。
                     这些数据来自 Zectrix API 的原始响应。

        【Python 概念：len() 函数】
        len(todos) 返回列表中元素的个数（即待办事项的数量）。
        """
        # 向 Zectrix API 发送 GET 请求，获取指定设备的所有待办事项
        # API 端点：GET /open/v1/todos?deviceId={device_id}
        # 通过 URL 查询参数（?deviceId=xxx）指定要获取哪个设备的待办事项
        result = await self._request("GET", f"/open/v1/todos?deviceId={self.device_id}")

        # 从响应中提取待办事项列表
        # result 的结构通常是：{"data": [{...}, {...}, ...]}
        # result.get("data", []) 安全地获取 "data" 字段，如果不存在则返回空列表 []
        todos = result.get("data", [])

        # 记录日志：拉取成功，包含待办事项数量
        logger.info(f"Fetched {len(todos)} todos from Zectrix")

        # 返回待办事项列表
        return todos
