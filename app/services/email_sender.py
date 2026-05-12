"""
邮件发送模块（Email Sender Module）

本模块负责通过 SMTP 协议发送任务提醒邮件。
主要功能是将待办任务的信息（标题、正文、截止日期）组装成一封电子邮件，
然后通过配置好的 SMTP 邮件服务器发送给指定收件人。

【模块中涉及的 Python 概念说明】

- import：导入 Python 标准库或第三方库中的功能，类似于其他语言中的 #include 或 using。
- smtplib：Python 内置的 SMTP 协议库，用来连接邮件服务器并发送邮件。
  SMTP（Simple Mail Transfer Protocol，简单邮件传输协议）是互联网上发送邮件的标准协议。
- MIME：Multipurpose Internet Mail Extensions（多用途互联网邮件扩展），
  是电子邮件的格式标准，支持在邮件中包含文本、附件、HTML 等多种内容。
  - MIMEText：表示邮件中的纯文本内容。
  - MIMEMultipart：表示一封多部分组成的邮件（例如同时包含正文和附件）。
- typing.Optional：Python 的类型注解工具，表示一个值可以是某种类型，也可以是 None（空值）。
  例如 Optional[str] 表示"可以是字符串，也可以是 None"。
- async/await：Python 的异步编程语法。用 async 标记的函数是"协程"（coroutine），
  可以在等待网络请求等耗时操作时不阻塞整个程序。await 用于等待异步操作完成。
- try/except：Python 的异常处理机制。try 里面的代码如果出错，
  程序不会崩溃，而是跳到 except 里面去处理错误。
- logging：Python 内置的日志记录库，用来记录程序的运行信息（如调试信息、错误信息）。
"""

# logging 是 Python 内置的日志模块，用于记录程序运行过程中的信息
# 例如：发送邮件成功时记录一条 info 日志，失败时记录一条 error 日志
import logging

# smtplib 是 Python 内置的 SMTP 客户端库
# 它提供了 SMTP 和 SMTP_SSL 两个类，用于连接邮件服务器并发送邮件
import smtplib

# 从 email.mime.text 模块导入 MIMEText 类
# MIMEText 用于创建邮件中的纯文本内容部分
from email.mime.text import MIMEText

# 从 email.mime.multipart 模块导入 MIMEMultipart 类
# MIMEMultipart 用于创建"多部分"邮件（一封邮件可以包含正文、附件等多个部分）
from email.mime.multipart import MIMEMultipart

# 从 typing 模块导入 Optional 类型
# Optional[str] 等价于 "str 或 None"，用于在函数参数中标记"这个参数可以不传"
from typing import Optional

# 创建一个 logger（日志记录器）实例
# __name__ 是 Python 的内置变量，值为当前模块的名称（即 "app.services.email_sender"）
# 这样做的好处是日志中能清楚地看到是哪个模块输出的信息
logger = logging.getLogger(__name__)


async def send_task_email(
    smtp_host: str,
    smtp_port: int,
    smtp_user: str,
    smtp_password: str,
    from_addr: str,
    to_addr: str,
    title: str,
    body: str = "",
    due_date: Optional[str] = None,
) -> bool:
    """
    发送任务提醒邮件（异步函数）

    通过 SMTP 邮件服务器将待办任务信息发送给指定收件人。

    【什么是 async 函数？】
    async def 定义的函数叫"异步函数"（也叫协程）。
    它在执行到 await 时可以"暂停"，让程序去处理其他事情，
    等待网络请求等耗时操作完成后再继续执行。
    这样可以提高程序的并发处理能力——比如同时发送多封邮件而不会互相等待。

    【参数说明】
    - smtp_host（str）：SMTP 服务器地址，例如 "smtp.qq.com"、"smtp.163.com"
    - smtp_port（int）：SMTP 服务器端口号，常见端口：
        - 465：SSL 加密连接端口（更安全）
        - 587：STARTTLS 加密连接端口（先明文连接，再升级为加密）
        - 25：非加密端口（不推荐使用）
    - smtp_user（str）：SMTP 登录用户名（通常是邮箱地址）
    - smtp_password（str）：SMTP 登录密码或授权码
        （很多邮箱服务商不使用登录密码，而是需要生成一个专门的"授权码"）
    - from_addr（str）：发件人邮箱地址
    - to_addr（str）：收件人邮箱地址
    - title（str）：邮件主题（任务标题）
    - body（str）：邮件正文内容，默认为空字符串 ""
    - due_date（Optional[str]）：任务截止日期，可以传字符串日期，也可以不传（默认为 None）

    【返回值】
    - bool：发送成功返回 True，失败返回 False

    【类型注解说明】
    参数后面的 : str、: int 等是 Python 的"类型注解"（Type Hints），
    它们不会影响程序的运行，只是告诉阅读代码的人和代码编辑器：
    "这个参数应该传什么类型的值"。
    -> bool 表示这个函数的返回值类型是布尔值（True 或 False）。
    """

    # 创建一封多部分邮件对象（MIMEMultipart）
    # 这是一封可以包含多个内容部分（如正文、附件）的邮件
    msg = MIMEMultipart()

    # 设置邮件的发件人地址
    msg["From"] = from_addr

    # 设置邮件的收件人地址
    msg["To"] = to_addr

    # 设置邮件的主题（在收件箱中显示的标题行）
    msg["Subject"] = title

    # 用于收集邮件正文的所有文本片段
    parts = []

    # 如果传入了正文内容（body 不为空字符串），就添加到文本片段列表中
    if body:
        parts.append(body)

    # 如果传入了截止日期（due_date 不为 None），就在正文末尾追加截止日期信息
    if due_date:
        parts.append(f"截止日期: {due_date}")
        # f"..." 是 Python 的"格式化字符串"（f-string），
        # 花括号 {} 中的变量会被替换为它的实际值。
        # 例如 due_date 为 "2025-06-01" 时，结果为 "截止日期: 2025-06-01"

    # 将所有文本片段用换行符 "\n" 连接成完整的正文
    # 如果没有任何片段（body 为空且没有截止日期），则用 title（标题）作为正文
    text = "\n".join(parts) if parts else title

    # 创建纯文本邮件内容部分
    # MIMEText(text, "plain", "utf-8") 的三个参数分别是：
    #   - text：文本内容
    #   - "plain"：内容类型为纯文本（如果是 HTML 则用 "html"）
    #   - "utf-8"：字符编码（支持中文等国际字符）
    # 然后通过 msg.attach() 将文本内容附加到邮件中
    msg.attach(MIMEText(text, "plain", "utf-8"))

    # try/except 是 Python 的异常处理机制
    # try 里面的代码如果执行时发生错误（异常），程序不会崩溃，
    # 而是跳到 except 块中去处理错误
    try:
        # 根据端口号选择不同的连接方式
        if smtp_port == 465:
            # 端口 465 使用 SSL 加密连接（从头到尾都是加密的）
            # SMTP_SSL 会直接建立一条加密的安全连接
            # timeout=30 表示连接超时时间为 30 秒（超过 30 秒连不上就放弃）
            server = smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=30)
        else:
            # 其他端口（通常是 587 或 25）先建立普通连接
            server = smtplib.SMTP(smtp_host, smtp_port, timeout=30)
            # 然后通过 starttls() 命令将连接升级为加密连接（TLS 加密）
            # STARTTLS 的意思是：先以明文连接，再协商升级为加密
            server.starttls()

        # 如果提供了用户名和密码，就进行登录认证
        # 有些 SMTP 服务器不要求登录（如内网服务器），所以这里做个判断
        if smtp_user and smtp_password:
            server.login(smtp_user, smtp_password)

        # 发送邮件
        # sendmail() 的三个参数：发件人地址、收件人地址、邮件内容（转为字符串格式）
        # msg.as_string() 将 MIMEMultipart 对象转换为符合邮件标准的字符串格式
        server.sendmail(from_addr, to_addr, msg.as_string())

        # 断开与邮件服务器的连接（礼貌地关闭连接，释放资源）
        server.quit()

        # 记录一条 info 级别的日志，表示邮件发送成功
        # 这条日志会出现在程序的运行日志中，方便排查问题
        logger.info(f"Sent task email: {title} -> {to_addr}")

        # 邮件发送成功，返回 True
        return True
    except Exception as e:
        # except 块：当 try 中的代码出现任何错误时执行
        # Exception 是所有异常的基类，e 是捕获到的具体异常对象
        # 常见的错误原因：网络连接失败、用户名密码错误、收件人地址无效等

        # 记录一条 error 级别的日志，包含错误信息，方便排查问题
        logger.error(f"Failed to send email: {e}")

        # 邮件发送失败，返回 False
        return False
