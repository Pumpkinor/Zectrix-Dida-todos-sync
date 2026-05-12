"""
转发器基类模块 (Base Forwarder Module)
========================================

本文件定义了所有"转发器"的抽象基类。

【什么是转发器？】
转发器的作用是：当 Dida365（滴答清单）上的待办事项发生变化时，
把这些变化"转发"到另一个外部平台（比如 Zectrix）。
你可以把它理解为"同步目标平台"的统一接口。

【Python 概念：ABC（Abstract Base Class，抽象基类）】
ABC 是 Python 中用来定义"接口"的方式。
- 带有 @abstractmethod 装饰器的方法叫做"抽象方法"，
  它只有方法签名（函数名、参数、返回值类型），没有具体实现代码。
- 子类（继承这个基类的类）必须实现所有抽象方法，否则 Python 会在实例化时报错。
- 这就像一份"合同"：基类说"你必须提供这些功能"，子类负责具体实现。

【本模块的结构】
BaseForwarder 是所有转发器的父类，它定义了 5 个必须实现的方法：
  1. create_todo   - 在远程平台创建一个新待办事项
  2. update_todo   - 更新远程平台上已有的待办事项
  3. complete_todo - 在远程平台上将待办事项标记为已完成
  4. delete_todo   - 从远程平台上删除一个待办事项
  5. fetch_remote_todos - 从远程平台拉取所有待办事项

【如何扩展】
如果你想支持一个新的同步目标平台，只需要：
  1. 创建一个新类，继承 BaseForwarder
  2. 实现这 5 个抽象方法
  3. 在配置中注册你的新转发器
"""

# 从 Python 标准库的 abc 模块导入 ABC 和 abstractmethod
# abc = Abstract Base Classes（抽象基类）
# ABC：用于创建抽象基类的基类
# abstractmethod：装饰器，标记一个方法为"抽象方法"（子类必须实现）
from abc import ABC, abstractmethod

# 从本项目的 app.models 模块导入 Todo 数据模型
# Todo 是一个数据类，包含待办事项的所有信息（标题、描述、优先级、截止日期等）
from app.models import Todo


class BaseForwarder(ABC):
    """
    所有转发器的抽象基类。

    【Python 概念：继承】
    class BaseForwarder(ABC) 表示 BaseForwarder 继承自 ABC，
    即 BaseForwarder 是一个"抽象基类"。
    这意味着你不能直接创建 BaseForwarder 的实例（对象），
    只能创建它的子类的实例。

    【Python 概念：self】
    在 Python 中，self 代表"当前对象实例本身"。
    类似于 Java 中的 this，或 JavaScript 中的 this。
    在定义方法时，self 必须是第一个参数；
    但在调用方法时，self 由 Python 自动传入，不需要你手动写。

    【使用方式】
    继承此类并实现所有 @abstractmethod 标记的方法，
    即可添加一个新的同步目标平台（转发器）。
    """

    @abstractmethod
    async def create_todo(self, todo: Todo) -> str:
        """
        在远程平台上创建一个新的待办事项。

        【Python 概念：async/await（异步编程）】
        - async def 表示这是一个"异步函数"（也叫"协程"）。
        - 异步函数在执行耗时操作（如网络请求）时不会阻塞整个程序，
          而是先"让出"执行权，等操作完成后再继续。
        - 调用异步函数时需要用 await 关键字：
          result = await some_async_function()

        【Python 概念：类型提示 (Type Hints)】
        - (self, todo: Todo) -> str 表示：
          - 参数 todo 的类型是 Todo
          - 返回值的类型是 str（字符串）
        - 类型提示不是强制的，Python 不会在运行时检查类型，
          但它帮助开发者理解代码，IDE 也能利用它提供更好的提示。

        【参数】
        self  : 当前对象实例（Python 自动传入）
        todo  : Todo 对象，包含待办事项的所有信息（标题、描述、优先级等）

        【返回值】
        str : 远程平台返回的该待办事项的唯一标识 ID（远程 ID），
              后续操作（更新、删除等）需要用这个 ID 来定位该待办事项。
        """
        ...  # ... 是 Python 的占位符，表示"这里没有具体实现，由子类来实现"

    @abstractmethod
    async def update_todo(self, remote_id: str, todo: Todo):
        """
        更新远程平台上已存在的待办事项。

        用远程 ID 找到对应的待办事项，然后用新的 Todo 数据更新它。

        【参数】
        self      : 当前对象实例
        remote_id : 远程平台上该待办事项的唯一 ID（字符串）
        todo      : 包含最新数据的 Todo 对象

        【返回值】
        无（没有返回值）
        """
        ...

    @abstractmethod
    async def complete_todo(self, remote_id: str):
        """
        将远程平台上的待办事项标记为"已完成"。

        【参数】
        self      : 当前对象实例
        remote_id : 远程平台上该待办事项的唯一 ID

        【返回值】
        无
        """
        ...

    @abstractmethod
    async def delete_todo(self, remote_id: str):
        """
        从远程平台上删除一个待办事项。

        【注意】删除操作不可逆，删除后远程平台上该待办事项将不存在。

        【参数】
        self      : 当前对象实例
        remote_id : 远程平台上该待办事项的唯一 ID

        【返回值】
        无
        """
        ...

    @abstractmethod
    async def fetch_remote_todos(self) -> list[dict]:
        """
        从远程平台拉取所有待办事项。

        【Python 概念：list[dict]】
        - list[dict] 表示"字典列表"，即一个列表，里面的每个元素都是字典。
        - 例如：[{"title": "买牛奶", "done": False}, {"title": "写代码", "done": True}]
        - dict（字典）是 Python 中的键值对数据结构，类似于 JSON 对象。
          用大括号 {} 创建，例如：{"name": "张三", "age": 25}
          访问值用中括号：person["name"] 得到 "张三"

        【返回值】
        list[dict] : 远程平台上所有待办事项的原始数据列表。
                     每个字典代表一个待办事项，包含远程平台返回的所有字段。
                     这些原始数据会在同步逻辑中被进一步处理和转换。
        """
        ...
