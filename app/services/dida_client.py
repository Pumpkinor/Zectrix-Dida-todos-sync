"""
滴答清单（Dida365）MCP 客户端模块

本模块封装了与滴答清单（Dida365）服务端交互的全部逻辑。
它通过 MCP（Model Context Protocol，模型上下文协议）与滴答清单通信，
使用 Bearer Token 进行身份认证。

【什么是 MCP？】
MCP 是一种基于 JSON-RPC 2.0 的远程调用协议。
客户端向服务端发送一个 JSON 请求（包含方法名和参数），
服务端返回一个 JSON 响应（包含结果或错误信息）。
协议规范要求每个请求带一个唯一 id，用于匹配请求和响应。

【模块功能概览】
- DidaMCPClient 类：核心客户端，负责发送 MCP 请求、调用远程工具
- get_dida_mcp_client()：工厂函数，从数据库读取 token 并创建客户端实例
- _local_priority_to_dida()：将本地优先级映射为滴答清单的优先级
- _format_dida_datetime()：将日期和时间字符串格式化为滴答清单所需的日期时间格式

【Python 知识点：import 导入语句】
Python 使用 import 关键字来引入其他模块中的功能。
- import logging：导入 Python 标准库的日志模块
- from datetime import datetime, time：从 datetime 模块中只导入 datetime 和 time 两个类
- from zoneinfo import ZoneInfo：从 zoneinfo 模块导入时区类
"""
import logging  # 日志模块，用于记录程序运行时的信息（调试信息、错误信息等）
import json  # JSON 处理模块，用于解析和生成 JSON 格式的数据
from datetime import datetime, time  # datetime: 日期时间类；time: 时间类（只含时分秒）
from zoneinfo import ZoneInfo  # 时区信息类，用于给日期时间附加时区

# httpx 是一个第三方 HTTP 客户端库，类似于 requests，但支持异步请求（async/await）
import httpx

# 【Python 知识点：logging.getLogger】
# __name__ 是一个特殊变量，值为当前模块的名称（即 "app.services.dida_client"）。
# 这行代码创建了一个以模块名命名的日志记录器，方便按模块筛选日志。
logger = logging.getLogger(__name__)

# 【Python 知识点：模块级常量】
# 在模块顶层用大写字母命名变量，表示这是一个常量（约定俗成，Python 没有真正的常量机制）。
# MCP_URL：滴答清单 MCP 服务的地址
MCP_URL = "https://mcp.dida365.com"
# DEFAULT_TIMEZONE：默认时区，中国标准时间（北京时间）
DEFAULT_TIMEZONE = "Asia/Shanghai"


class DidaMCPClient:
    """
    滴答清单 MCP 客户端

    通过 MCP（Model Context Protocol）协议与滴答清单服务端通信，
    使用 Bearer Token 进行身份认证。

    【什么是 Bearer Token？】
    Bearer Token 是一种访问令牌，格式通常是一串随机字符串。
    客户端在每次请求时，在 HTTP 头部附带 "Authorization: Bearer <token>"，
    服务端通过验证这个 token 来确认调用者的身份。
    类似于：你拿着一张通行证（token），每进一扇门（请求）都出示一下。

    【Python 知识点：类（class）】
    class 关键字用于定义一个类。类是创建对象的蓝图/模板。
    对象是类的具体实例。比如 "汽车" 是类，"我的那辆红色丰田" 是对象。
    类中可以定义属性（数据）和方法（函数）。

    用法示例：
        client = DidaMCPClient(token="abc123")  # 创建一个客户端实例
        await client.list_projects()              # 调用它的方法

    【Python 知识点：self】
    self 代表类的当前实例本身。在类的方法中，self 是第一个参数，
    用于访问该实例的属性和其他方法。类似于 Java 中的 this。
    调用方法时不需要手动传 self，Python 会自动传入。
    例如：self.token 表示"这个实例的 token"。
    """

    def __init__(self, token: str):
        """
        初始化方法（构造函数）

        创建客户端实例时自动调用，用于设置初始状态。

        【Python 知识点：__init__ 方法】
        __init__ 是 Python 的特殊方法名（双下划线开头和结尾，称为"魔术方法"），
        在创建类的实例时会自动执行。类似于其他语言的"构造函数"。
        用法：client = DidaMCPClient(token="xxx") 时，__init__ 被自动调用。

        【Python 知识点：类型提示（Type Hints）】
        参数名后面的 ": str" 是类型提示，表示这个参数应该是字符串类型。
        这只是给人和开发工具看的提示，Python 运行时不会强制检查类型。
        例如：token: str 表示 token 参数期望传入字符串。

        参数：
            token (str): 滴答清单的 Bearer Token 认证令牌
        """
        # 将传入的 token 保存为实例属性，后续发送请求时要用
        self.token = token
        # _req_id 是请求计数器，每次发送 MCP 请求时递增，用于生成唯一的请求 ID
        # 下划线前缀（_req_id）是 Python 约定，表示这是一个"私有"属性，外部不应直接访问
        # （但 Python 不会真正阻止访问，这只是命名约定）
        self._req_id = 0

    def _next_id(self) -> int:
        """
        生成下一个请求 ID

        每次调用时将内部计数器加 1，并返回新值。
        MCP 协议要求每个请求有唯一 id，用于匹配请求和响应。

        【Python 知识点：-> int】
        箭头后面的类型表示函数的返回值类型提示，表示这个方法返回一个整数。

        返回：
            int: 新的请求 ID（从 1 开始递增）
        """
        self._req_id += 1  # 将计数器加 1（+= 是 self._req_id = self._req_id + 1 的简写）
        return self._req_id  # 返回新的值

    async def _call(self, method: str, params: dict = None) -> dict:
        """
        发送一个 MCP JSON-RPC 请求到滴答清单服务端

        这是底层的通信方法，所有与滴答清单的交互最终都通过此方法完成。
        它构造符合 JSON-RPC 2.0 规范的请求体，通过 HTTP POST 发送，
        然后解析响应并返回结果。

        【Python 知识点：async】
        async 关键字标记这是一个"异步函数"（也叫"协程"）。
        异步函数可以在等待网络响应时不阻塞整个程序，让程序同时处理其他任务。
        调用异步函数时必须使用 await 关键字。

        【Python 知识点：参数默认值】
        params: dict = None 表示 params 参数可以不传，不传时默认值为 None。
        这样调用时可以只传 method，不必每次都传 params。

        参数：
            method (str): MCP 方法名，例如 "initialize"、"tools/call"
            params (dict | None): 方法的参数字典，可选

        返回：
            dict: MCP 响应中的 result 字段（一个字典/映射）

        异常：
            Exception: 当服务端返回错误时抛出异常
        """
        # 构造 JSON-RPC 2.0 请求体
        payload = {
            "jsonrpc": "2.0",  # 协议版本，固定为 "2.0"
            "id": self._next_id(),  # 请求的唯一 ID，用于匹配响应
            "method": method,  # 要调用的远程方法名
        }
        # 如果传了参数，就加入请求体
        if params is not None:
            payload["params"] = params

        # 【Python 知识点：f-string（格式化字符串）】
        # f"..." 中的大括号 {} 内可以放 Python 表达式，运行时会被替换为对应的值。
        # 例如 f"method={method}" 中，method 会被替换为实际的方法名字符串。
        # list(params.keys()) 获取字典的所有键，转为列表。
        logger.debug(f"MCP request: method={method}, params_keys={list(params.keys()) if params else []}")

        # 【Python 知识点：async with ... as ...】
        # async with 是异步上下文管理器，类似于普通的 with 语句。
        # httpx.AsyncClient 是一个异步 HTTP 客户端。
        # with 语句确保使用完毕后客户端会被正确关闭（释放网络连接等资源）。
        # timeout=30 表示请求超时时间为 30 秒（超过 30 秒没响应就报错）。
        async with httpx.AsyncClient(timeout=30) as client:
            # await 表示"等待异步操作完成"。
            # client.post() 发送 HTTP POST 请求。
            resp = await client.post(
                MCP_URL,  # 请求地址：滴答清单 MCP 服务器
                json=payload,  # 请求体：httpx 会自动将字典序列化为 JSON 字符串
                headers={  # HTTP 请求头
                    "Content-Type": "application/json",  # 告诉服务器：我发的是 JSON 数据
                    "Accept": "application/json",  # 告诉服务器：我期望收到 JSON 响应
                    # Authorization 头携带 Bearer Token，用于身份认证
                    # f-string 中 self.token 会被替换为实际的 token 值
                    "Authorization": f"Bearer {self.token}",
                },
            )
            # 【Python 知识点：raise_for_status()】
            # 检查 HTTP 响应状态码。如果状态码是 4xx（客户端错误）或 5xx（服务端错误），
            # 则抛出异常。状态码 200-299 表示成功，不会抛异常。
            resp.raise_for_status()
            # resp.json() 将响应体中的 JSON 字符串解析为 Python 字典
            data = resp.json()

        # 检查 MCP 协议层面的错误（即使 HTTP 状态码正常，MCP 协议可能返回业务错误）
        if "error" in data:
            # 【Python 知识点：dict.get(key, default)】
            # data['error'].get('message', str(data['error'])) 的含义是：
            # 尝试从 error 对象中获取 'message' 字段，如果不存在则使用 str(data['error']) 作为默认值。
            # str() 将任意对象转换为字符串。
            err_msg = data['error'].get('message', str(data['error']))
            logger.error(f"MCP error: method={method}, error={err_msg}")
            # raise 抛出异常，中断当前函数的执行
            raise Exception(f"MCP error: {err_msg}")

        # 取出响应中的 result 字段，如果不存在则返回空字典 {}
        result = data.get("result", {})
        # isinstance(result, dict) 检查 result 是否是字典类型
        logger.debug(f"MCP response: method={method}, result_keys={list(result.keys()) if isinstance(result, dict) else type(result).__name__}")
        return result

    async def _call_tool(self, name: str, arguments: dict) -> str:
        """
        调用一个 MCP 工具

        MCP 协议中的"工具"（tool）是服务端提供的可调用的功能单元。
        本方法是对 _call 的高层封装，专门用于调用 "tools/call" 方法，
        并将返回的内容拼接为纯文本字符串。

        【为什么需要这个方法？】
        MCP 的 tools/call 返回的结果格式比较特殊（包含 content 数组），
        本方法负责解析这种格式，提取其中的文本内容，简化上层调用。

        参数：
            name (str): 工具名称，例如 "list_projects"、"create_task"
            arguments (dict): 传给工具的参数字典

        返回：
            str: 工具返回的文本内容（多个文本内容项会用换行符拼接）

        【Python 知识点：json.dumps()】
        json.dumps() 将 Python 对象（如字典、列表）转换为 JSON 格式的字符串。
        ensure_ascii=False 表示允许输出非 ASCII 字符（如中文），
        否则中文会被转义成类似 中文 的形式。
        [:200] 是字符串切片，只取前 200 个字符（防止日志过长）。
        """
        logger.info(f"MCP tool call: {name}, args={json.dumps(arguments, ensure_ascii=False)[:200]}")
        # 调用底层的 _call 方法，发送 "tools/call" 请求
        result = await self._call("tools/call", {"name": name, "arguments": arguments})
        # MCP 工具的返回结果中，content 是一个数组，每个元素是一块内容
        content = result.get("content", [])
        # MCP 返回多个 content 内容项，需要将所有文本项拼接起来
        texts = []  # 创建一个空列表，用于收集文本内容
        for c in content:  # 遍历 content 数组中的每个元素
            if c.get("type") == "text":  # 只处理类型为 "text" 的内容项
                texts.append(c["text"])  # 将文本添加到列表中
        # "\n".join(texts) 将列表中的多个字符串用换行符连接成一个字符串
        # 例如 ["hello", "world"] 会变成 "hello\nworld"
        combined = "\n".join(texts)
        logger.info(f"MCP tool result: {name}, content_items={len(content)}, total_text_len={len(combined)}")
        return combined

    def _parse_ndjson(self, text: str) -> list[dict]:
        """
        解析 NDJSON（Newline-Delimited JSON）格式的文本

        NDJSON 是一种数据格式：多个 JSON 对象用换行符分隔，写在一串文本中。
        例如：
            {"id":1,"name":"项目A"}\n{"id":2,"name":"项目B"}\n

        本方法通过大括号的嵌套深度来逐个提取完整的 JSON 对象，
        然后用 json.loads() 将每个 JSON 字符串解析为 Python 字典。

        【为什么不直接按换行符分割？】
        因为 JSON 对象内部也可能包含换行符（比如字符串值中有换行），
        简单按换行分割可能导致一个 JSON 对象被错误地切断。
        通过跟踪大括号的嵌套层级，可以准确找到每个 JSON 对象的边界。

        参数：
            text (str): 包含一个或多个 JSON 对象的文本

        返回：
            list[dict]: 解析出的字典列表。如果文本为空或格式错误，返回空列表 []
        """
        objects = []  # 存放解析出的所有 JSON 对象
        current = ""  # 当前正在拼接的 JSON 字符串
        depth = 0  # 大括号的嵌套深度（遇到 { 加 1，遇到 } 减 1）
        for char in text:  # 逐个字符遍历文本
            if char == '{':
                depth += 1  # 遇到左大括号，深度加 1（进入新的一层嵌套）
            if depth > 0:  # 只在至少有一层嵌套时才收集字符（跳过 JSON 对象外的空白和换行）
                current += char  # 将当前字符拼接到正在构建的 JSON 字符串中
            if char == '}':
                depth -= 1  # 遇到右大括号，深度减 1（退出一层嵌套）
                if depth == 0 and current.strip():  # 深度回到 0，说明一个完整的 JSON 对象结束了
                    # .strip() 去除字符串首尾的空白字符，如果有内容才处理
                    try:
                        # json.loads() 将 JSON 字符串解析为 Python 字典
                        objects.append(json.loads(current))
                    except json.JSONDecodeError:
                        # 如果解析失败（JSON 格式不正确），忽略这个对象，继续处理下一个
                        # 【Python 知识点：try/except】
                        # try 块中放可能出错的代码，except 块中放出错后的处理逻辑。
                        # pass 表示什么都不做，直接跳过。
                        pass
                    current = ""  # 重置，准备拼接下一个 JSON 对象
        return objects

    async def initialize(self) -> dict:
        """
        初始化 MCP 连接

        向服务端发送 initialize 请求，声明客户端支持的协议版本和基本信息。
        这通常是建立 MCP 连接后的第一个调用。

        返回：
            dict: 服务端返回的初始化信息（包含服务端支持的能力等）
        """
        return await self._call("initialize", {
            "protocolVersion": "2024-11-05",  # MCP 协议版本号
            "capabilities": {},  # 客户端的能力声明（空字典表示暂不声明特殊能力）
            "clientInfo": {"name": "todo-sync", "version": "1.0"},  # 客户端标识信息
        })

    async def list_projects(self) -> list[dict]:
        """
        获取滴答清单中的所有项目（清单）列表

        项目（Project）是滴答清单中的概念，类似于"文件夹"或"清单"，
        每个项目下可以包含多个任务（Task）。

        返回：
            list[dict]: 项目列表，每个元素是一个字典，包含项目的 id、name 等信息
        """
        text = await self._call_tool("list_projects", {})  # 调用 list_projects 工具
        # 如果返回了文本，用 NDJSON 解析器解析为字典列表；否则返回空列表
        projects = self._parse_ndjson(text) if text else []
        logger.info(f"MCP list_projects: got {len(projects)} projects")
        for p in projects:  # 遍历每个项目，记录日志
            logger.info(f"  Project: id={p.get('id')}, name={p.get('name')}")
        return projects

    async def get_undone_tasks(self, project_id: str) -> list[dict]:
        """
        获取指定项目中未完成的任务列表

        参数：
            project_id (str): 项目的 ID（滴答清单中每个项目有唯一标识符）

        返回：
            list[dict]: 未完成任务列表，每个元素是一个任务字典
        """
        # 调用 get_project_with_undone_tasks 工具，传入项目 ID
        text = await self._call_tool("get_project_with_undone_tasks", {"project_id": project_id})
        if not text:  # 如果没有返回内容
            return []  # 返回空列表
        # 判断返回文本是否以 { 开头（是标准 JSON 格式）
        # .strip() 去除首尾空白，.startswith('{') 检查是否以 { 开头
        data = json.loads(text) if text.strip().startswith('{') else {}
        # 从返回数据中取出 "tasks" 字段（任务列表），如果不存在则返回空列表
        tasks = data.get("tasks", [])
        logger.info(f"MCP get_undone_tasks: project={project_id}, count={len(tasks)}")
        return tasks

    async def get_completed_tasks(self, project_ids: list[str], start_date: str, end_date: str) -> list[dict]:
        """
        获取指定项目和日期范围内已完成的任务列表

        参数：
            project_ids (list[str]): 项目 ID 列表（可以同时查询多个项目）
                【Python 知识点：list[str]】
                这是类型提示，表示参数应该是一个字符串列表，如 ["proj1", "proj2"]。
            start_date (str): 开始日期，格式为 "YYYY-MM-DD"（如 "2024-01-01"）
            end_date (str): 结束日期，格式为 "YYYY-MM-DD"

        返回：
            list[dict]: 已完成任务列表
        """
        text = await self._call_tool("list_completed_tasks_by_date", {
            "search": {},  # 搜索条件（空字典表示无额外筛选）
            "project_ids": project_ids,  # 要查询的项目 ID 列表
            "start_date": start_date,  # 开始日期
            "end_date": end_date,  # 结束日期
        })
        # 返回的数据可能是 NDJSON 格式（多个 JSON 对象），需要用解析器处理
        tasks = self._parse_ndjson(text) if text else []
        logger.info(f"MCP get_completed_tasks: projects={project_ids}, range={start_date}~{end_date}, count={len(tasks)}")
        return tasks

    async def complete_task(self, project_id: str, task_id: str) -> str:
        """
        将指定任务标记为已完成

        参数：
            project_id (str): 任务所属项目的 ID
            task_id (str): 要完成的任务的 ID

        返回：
            str: 操作结果文本
        """
        logger.info(f"MCP complete_task: project={project_id}, task={task_id}")
        return await self._call_tool("complete_task", {
            "project_id": project_id,
            "task_id": task_id,
        })

    def _build_task_payload(self, title: str = None, project_id: str = None,
                            content: str = "", due_date: str = None,
                            due_time: str = None, priority: int = 0,
                            reminders: str = "", repeat_flag: str = "") -> dict:
        """
        构建任务数据字典（内部辅助方法）

        根据传入的参数，构建符合滴答清单 API 要求的任务数据结构。
        只包含非空字段，避免发送不必要的数据。

        【Python 知识点：大量参数的方法】
        这个方法有很多参数，大多有默认值（如 = None、= ""、= 0），
        调用时可以只传需要的参数，其余使用默认值。

        参数：
            title (str | None): 任务标题
            project_id (str | None): 所属项目 ID
            content (str): 任务详情/备注内容
            due_date (str | None): 截止日期，格式 "YYYY-MM-DD"
            due_time (str | None): 截止时间，格式 "HH:MM"（如 "14:30"），为 None 表示全天任务
            priority (int): 优先级（0=无，1=低，3=中，5=高）
            reminders (str): 提醒设置的 JSON 字符串
            repeat_flag (str): 重复规则标识

        返回：
            dict: 构建好的任务数据字典
        """
        task = {}  # 创建空字典，逐步添加字段
        if title is not None:  # 只有 title 不为 None 时才添加（空字符串 "" 也允许）
            task["title"] = title
        if project_id:  # 只有 project_id 有值（非空字符串）时才添加
            task["projectId"] = project_id
        if content:  # 只有 content 非空时才添加
            task["content"] = content
        # 调用模块级函数 _format_dida_datetime 来格式化日期时间
        due_dt = _format_dida_datetime(due_date, due_time)
        if due_dt:  # 如果有截止日期时间
            task["startDate"] = due_dt  # 开始日期（滴答清单中设为与截止日期相同）
            task["dueDate"] = due_dt  # 截止日期
            task["timeZone"] = DEFAULT_TIMEZONE  # 时区
            # 【Python 知识点：is None 判断】
            # due_time is None 检查 due_time 是否为 None（即未传入时间）。
            # 用 is 而不是 == 来判断 None 是 Python 的推荐写法。
            task["isAllDay"] = due_time is None  # 如果没传时间，标记为全天任务
        # 将本地优先级转换为滴答清单的优先级格式
        dida_priority = _local_priority_to_dida(priority)
        if dida_priority:  # 如果优先级不为 0（0 表示无优先级）
            task["priority"] = dida_priority
        if reminders:  # 如果有提醒设置
            try:
                # reminders 是 JSON 字符串，需要解析为 Python 对象（通常是列表）
                task["reminders"] = json.loads(reminders)
            except (json.JSONDecodeError, ValueError):
                # 如果 JSON 格式无效，忽略提醒设置
                # 【Python 知识点：except 多个异常类型】
                # 可以用元组指定多种异常类型，任何一种发生都会执行 except 块
                pass
        # 如果有重复规则且不是 "none"（"none" 表示不重复）
        if repeat_flag and repeat_flag != "none":
            task["repeatFlag"] = repeat_flag
        return task

    async def create_task(self, title: str, project_id: str = None,
                          content: str = "", due_date: str = None,
                          due_time: str = None, priority: int = 0, reminders: str = "",
                          repeat_flag: str = "") -> str:
        """
        在滴答清单中创建一个新任务

        参数：
            title (str): 任务标题（必填）
            project_id (str | None): 所属项目 ID
            content (str): 任务详情
            due_date (str | None): 截止日期 "YYYY-MM-DD"
            due_time (str | None): 截止时间 "HH:MM"
            priority (int): 优先级
            reminders (str): 提醒 JSON 字符串
            repeat_flag (str): 重复规则

        返回：
            str: 创建结果文本（通常包含新任务的 ID 等信息）
        """
        # 使用 _build_task_payload 构建任务数据字典
        task = self._build_task_payload(
            title=title,
            project_id=project_id,
            content=content,
            due_date=due_date,
            due_time=due_time,
            priority=priority,
            reminders=reminders,
            repeat_flag=repeat_flag,
        )
        # 调用 MCP 工具创建任务，将任务数据包装在 {"task": ...} 中
        return await self._call_tool("create_task", {"task": task})

    async def update_task(self, task_id: str, title: str = None, project_id: str = None,
                          content: str = "", due_date: str = None,
                          due_time: str = None, priority: int = 0,
                          reminders: str = "", repeat_flag: str = "") -> str:
        """
        更新滴答清单中的一个已有任务

        与 create_task 类似，但需要额外传入 task_id 来指定要更新哪个任务。

        参数：
            task_id (str): 要更新的任务 ID（必填）
            其余参数同 create_task

        返回：
            str: 更新结果文本
        """
        # 构建更新数据（只包含需要修改的字段）
        task = self._build_task_payload(
            title=title,
            project_id=project_id,
            content=content,
            due_date=due_date,
            due_time=due_time,
            priority=priority,
            reminders=reminders,
            repeat_flag=repeat_flag,
        )
        # list(task.keys()) 获取字典的所有键（字段名），用于日志记录
        logger.info(f"MCP update_task: task={task_id}, keys={list(task.keys())}")
        # 调用 MCP 工具更新任务，需要传 task_id 和 task 两个参数
        return await self._call_tool("update_task", {"task_id": task_id, "task": task})

    async def get_task(self, task_id: str) -> dict:
        """
        获取单个任务的详细信息

        参数：
            task_id (str): 任务 ID

        返回：
            dict: 任务详情字典。如果任务不存在或获取失败，返回空字典 {}
        """
        # 通过任务 ID 查询任务详情
        text = await self._call_tool("get_task_by_id", {"task_id": task_id})
        if text:  # 如果返回了文本
            try:
                # 尝试将文本解析为 JSON 字典
                return json.loads(text)
            except json.JSONDecodeError:
                # JSON 解析失败，返回空字典
                return {}
        return {}  # 没有返回文本，返回空字典


# 【Python 知识点：模块级函数 vs 类方法】
# 下面定义的函数不属于任何类，是模块级别的独立函数。
# 它们用下划线开头（如 _local_priority_to_dida），表示是模块内部的辅助函数。


async def get_dida_mcp_client() -> DidaMCPClient | None:
    """
    工厂函数：创建并返回滴答清单 MCP 客户端实例

    从数据库中读取保存的滴答清单 token，如果存在则创建客户端实例。

    【Python 知识点：-> DidaMCPClient | None】
    返回值类型提示：可能返回 DidaMCPClient 实例，也可能返回 None。
    "|" 是 Python 3.10+ 的联合类型语法，等价于 Optional[DidaMCPClient]。

    【Python 知识点：延迟导入（lazy import）】
    from app.database import get_config 写在函数内部而非文件顶部，
    这是为了避免循环导入问题（dida_client 和 database 模块可能互相引用）。
    延迟导入只在函数被调用时才执行，避免了模块加载时的循环依赖。

    返回：
        DidaMCPClient | None: 如果数据库中有 token，返回客户端实例；否则返回 None
    """
    # 延迟导入：在函数内部导入，避免模块加载时的循环依赖
    from app.database import get_config
    # 从数据库配置表中获取滴答清单的 MCP token
    token = await get_config("dida_mcp_token")
    if not token:  # 如果没有配置 token
        return None  # 返回 None，表示无法创建客户端
    # 有 token，创建并返回客户端实例
    return DidaMCPClient(token)


def _local_priority_to_dida(priority: int) -> int:
    """
    将本地系统的优先级数值映射为滴答清单的优先级数值

    优先级映射规则：
        本地 0 → 滴答 0（无优先级）
        本地 1 → 滴答 3（低优先级）
        本地 2 → 滴答 5（中优先级）
        本地 3 → 滴答 5（中优先级）
        本地 5 → 滴答 5（中优先级）
        其他值 → 滴答 0（无优先级）

    【Python 知识点：字典的 .get() 方法】
    {0: 0, 1: 3, ...}.get(key, default) 的含义是：
    在字典中查找 key 对应的值。如果 key 不存在，返回 default。
    (priority or 0) 的含义是：如果 priority 为 0 或 None 等假值，则使用 0。

    参数：
        priority (int): 本地优先级数值

    返回：
        int: 滴答清单对应的优先级数值（0/3/5）
    """
    return {0: 0, 1: 3, 2: 5, 3: 5, 5: 5}.get(priority or 0, 0)


def _format_dida_datetime(due_date: str | None, due_time: str | None) -> str | None:
    """
    将日期和时间字符串格式化为滴答清单所需的日期时间格式

    滴答清单要求的格式为 ISO 8601 格式："YYYY-MM-DDTHH:MM:SS+HHMM"，
    例如 "2024-01-15T14:30:00+0800"。
    如果只传了日期没传时间，则默认为当天 00:00。

    【Python 知识点：str | None】
    表示参数可以是字符串或 None。Python 3.10+ 语法。

    参数：
        due_date (str | None): 截止日期字符串，格式 "YYYY-MM-DD"（如 "2024-01-15"）
        due_time (str | None): 截止时间字符串，格式 "HH:MM"（如 "14:30"），None 表示全天

    返回：
        str | None: 格式化后的日期时间字符串，如 "2024-01-15T14:30:00+0800"。
                    如果 due_date 为 None 或空字符串，返回 None。
    """
    if not due_date:  # 如果没有截止日期，返回 None
        return None
    # 默认小时和分钟为 0
    hour = 0
    minute = 0
    if due_time:  # 如果传了时间
        parts = due_time.split(":")  # 按冒号分割，例如 "14:30" → ["14", "30"]
        hour = int(parts[0])  # 第一个部分是小时，int() 将字符串转为整数
        # len(parts) > 1 检查是否有分钟部分（防止只传了 "14" 没传 "30"）
        minute = int(parts[1]) if len(parts) > 1 else 0
    # 【Python 知识点：datetime.combine()】
    # datetime.combine(date, time) 将一个日期对象和一个时间对象合并为一个完整的日期时间对象。
    # datetime.strptime(due_date, "%Y-%m-%d").date() 先用指定格式解析字符串为 datetime，再取出 date 部分。
    # "%Y-%m-%d" 是日期格式字符串：%Y=四位年份，%m=两位月份，%d=两位日期
    # time(hour=hour, minute=minute) 创建一个时间对象
    # tzinfo=ZoneInfo(DEFAULT_TIMEZONE) 设置时区为 Asia/Shanghai（北京时间，UTC+8）
    dt = datetime.combine(
        datetime.strptime(due_date, "%Y-%m-%d").date(),
        time(hour=hour, minute=minute),
        tzinfo=ZoneInfo(DEFAULT_TIMEZONE),
    )
    # strftime 将 datetime 对象格式化为字符串
    # "%Y-%m-%dT%H:%M:%S%z" 格式说明：
    #   %Y-%m-%d → 日期部分，如 2024-01-15
    #   T → 字母 T（ISO 8601 标准中日期和时间的分隔符）
    #   %H:%M:%S → 时间部分，如 14:30:00
    #   %z → 时区偏移，如 +0800
    return dt.strftime("%Y-%m-%dT%H:%M:%S%z")
