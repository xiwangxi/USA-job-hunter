"""Build and send the daily HTML email summary.

Supported providers (config.yaml -> email.provider):
  - "resend"      recommended: https://resend.com free tier (100 emails/day,
                   3000/month), simple HTTP API, no SMTP app-password hassle.
  - "sendgrid"    alternative HTTP API, similar free tier.
  - "gmail_smtp"  fallback using an app password, no third-party signup needed.
"""

import html as html_lib
import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import requests

logger = logging.getLogger("job_hunter.notify")

SPONSORSHIP_BADGE = {
    "likely": ("#16a34a", "#dcfce7", "签证担保: 可能"),
    "unclear": ("#6b7280", "#f3f4f6", "签证担保: 不确定"),
}


def _sponsorship_history_html(history: dict | None) -> str:
    if not history:
        return ""
    case_count = history.get("case_count", 0)
    certified_count = history.get("certified_count", 0)
    rate = f"{certified_count / case_count:.0%}" if case_count else "N/A"
    return (
        f'<br/><span style="color:#0369a1;font-size:12px;">'
        f"公开数据：该公司近期披露 {case_count} 起 H-1B/E-3 申请，{certified_count} 起获批（约 {rate}）"
        f"</span>"
    )


def _job_row_html(job: dict) -> str:
    color, bg, label = SPONSORSHIP_BADGE.get(job["sponsorship_likelihood"], SPONSORSHIP_BADGE["unclear"])
    title = html_lib.escape(job.get("title", ""))
    company = html_lib.escape(job.get("company", ""))
    location = html_lib.escape(job.get("location", ""))
    reason = html_lib.escape(job.get("one_line_reason", ""))
    url = html_lib.escape(job.get("url", "#"))
    score = job.get("score", 0)
    history_html = _sponsorship_history_html(job.get("_sponsorship_history"))
    return f"""
    <tr style="border-bottom:1px solid #e5e7eb;">
      <td style="padding:10px 8px;">
        <a href="{url}" style="color:#1d4ed8;text-decoration:none;font-weight:600;">{title}</a><br/>
        <span style="color:#4b5563;font-size:13px;">{company} · {location}</span><br/>
        <span style="color:#374151;font-size:13px;">{reason}</span>{history_html}
      </td>
      <td style="padding:10px 8px;text-align:center;font-weight:700;font-size:16px;">{score}</td>
      <td style="padding:10px 8px;text-align:center;">
        <span style="background:{bg};color:{color};padding:2px 8px;border-radius:10px;font-size:12px;white-space:nowrap;">{label}</span>
      </td>
    </tr>"""


def build_html(jobs_notified: list[dict], stats: dict) -> str:
    jobs_sorted = sorted(jobs_notified, key=lambda j: j.get("score", 0), reverse=True)

    if not jobs_sorted:
        body = """<p style="font-size:15px;color:#374151;">今天没有发现符合条件的新职位。程序运行正常，明天会继续搜索。</p>"""
    else:
        rows = "".join(_job_row_html(j) for j in jobs_sorted)
        body = f"""
        <table style="width:100%;border-collapse:collapse;font-family:Arial,Helvetica,sans-serif;">
          <thead>
            <tr style="border-bottom:2px solid #111827;">
              <th style="padding:8px;text-align:left;font-size:13px;color:#111827;">职位</th>
              <th style="padding:8px;text-align:center;font-size:13px;color:#111827;">匹配分</th>
              <th style="padding:8px;text-align:center;font-size:13px;color:#111827;">签证</th>
            </tr>
          </thead>
          <tbody>{rows}</tbody>
        </table>"""

    stats_rows = "".join(
        f'<tr><td style="padding:4px 8px;color:#4b5563;">{html_lib.escape(k)}</td>'
        f'<td style="padding:4px 8px;text-align:right;font-weight:600;">{v}</td></tr>'
        for k, v in stats.items()
    )

    return f"""
    <div style="font-family:Arial,Helvetica,sans-serif;max-width:640px;margin:0 auto;">
      <h2 style="color:#111827;">美国职位每日摘要</h2>
      {body}
      <h3 style="color:#111827;margin-top:24px;">今日统计</h3>
      <table style="border-collapse:collapse;font-size:13px;">{stats_rows}</table>
    </div>"""


def send_via_resend(html_body: str, subject: str) -> bool:
    api_key = os.environ.get("RESEND_API_KEY")
    from_addr = os.environ.get("EMAIL_FROM")
    to_addr = os.environ.get("EMAIL_TO")
    if not all([api_key, from_addr, to_addr]):
        logger.error("RESEND_API_KEY / EMAIL_FROM / EMAIL_TO not fully set")
        return False
    try:
        resp = requests.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {api_key}"},
            json={"from": from_addr, "to": [to_addr], "subject": subject, "html": html_body},
            timeout=20,
        )
        if resp.status_code in (200, 201, 202):
            return True
        logger.error("Resend API returned HTTP %s: %s", resp.status_code, resp.text)
        return False
    except requests.RequestException:
        logger.exception("Failed to send email via Resend")
        return False


def send_via_sendgrid(html_body: str, subject: str) -> bool:
    api_key = os.environ.get("SENDGRID_API_KEY")
    from_addr = os.environ.get("EMAIL_FROM")
    to_addr = os.environ.get("EMAIL_TO")
    if not all([api_key, from_addr, to_addr]):
        logger.error("SENDGRID_API_KEY / EMAIL_FROM / EMAIL_TO not fully set")
        return False
    try:
        resp = requests.post(
            "https://api.sendgrid.com/v3/mail/send",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "personalizations": [{"to": [{"email": to_addr}]}],
                "from": {"email": from_addr},
                "subject": subject,
                "content": [{"type": "text/html", "value": html_body}],
            },
            timeout=20,
        )
        if resp.status_code in (200, 201, 202):
            return True
        logger.error("SendGrid API returned HTTP %s: %s", resp.status_code, resp.text)
        return False
    except requests.RequestException:
        logger.exception("Failed to send email via SendGrid")
        return False


def send_via_gmail_smtp(html_body: str, subject: str) -> bool:
    user = os.environ.get("GMAIL_SMTP_USER")
    app_password = os.environ.get("GMAIL_SMTP_APP_PASSWORD")
    to_addr = os.environ.get("EMAIL_TO")
    if not all([user, app_password, to_addr]):
        logger.error("GMAIL_SMTP_USER / GMAIL_SMTP_APP_PASSWORD / EMAIL_TO not fully set")
        return False
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = to_addr
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=20) as server:
            server.login(user, app_password)
            server.sendmail(user, [to_addr], msg.as_string())
        return True
    except smtplib.SMTPException:
        logger.exception("Failed to send email via Gmail SMTP")
        return False


_SENDERS = {
    "resend": send_via_resend,
    "sendgrid": send_via_sendgrid,
    "gmail_smtp": send_via_gmail_smtp,
}


def send_daily_email(provider: str, subject_prefix: str, date_str: str, jobs_notified: list[dict], stats: dict) -> bool:
    if jobs_notified:
        subject = f"{subject_prefix} {date_str} 新职位 {len(jobs_notified)} 个"
    else:
        subject = f"{subject_prefix} {date_str} 今日无新增"

    html_body = build_html(jobs_notified, stats)
    sender = _SENDERS.get(provider)
    if not sender:
        logger.error("Unknown email provider: %s", provider)
        return False
    return sender(html_body, subject)
