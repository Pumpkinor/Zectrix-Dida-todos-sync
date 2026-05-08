import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional

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
    msg = MIMEMultipart()
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg["Subject"] = title

    parts = []
    if body:
        parts.append(body)
    if due_date:
        parts.append(f"截止日期: {due_date}")

    text = "\n".join(parts) if parts else title
    msg.attach(MIMEText(text, "plain", "utf-8"))

    try:
        if smtp_port == 465:
            server = smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=30)
        else:
            server = smtplib.SMTP(smtp_host, smtp_port, timeout=30)
            server.starttls()

        if smtp_user and smtp_password:
            server.login(smtp_user, smtp_password)

        server.sendmail(from_addr, to_addr, msg.as_string())
        server.quit()
        logger.info(f"Sent task email: {title} -> {to_addr}")
        return True
    except Exception as e:
        logger.error(f"Failed to send email: {e}")
        return False
