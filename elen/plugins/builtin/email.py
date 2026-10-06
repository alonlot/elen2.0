"""Email over IMAP (read) and SMTP (send). Works with any custom mail server.

[[plugins.email.accounts]]
name = "work"
address = "me@company.com"
display_name = "My Name"
imap_host = "mail.company.com"
imap_port = 993
smtp_host = "mail.company.com"
smtp_port = 587            # 587 = STARTTLS, 465 = SSL
username = "me@company.com"
password = "cmd:secret-tool lookup elen mail-work"
sent_folder = "Sent"
drafts_folder = "Drafts"
"""

from __future__ import annotations

import asyncio
import email
import email.utils
import imaplib
import re
import smtplib
import ssl
import time
from email.message import EmailMessage
from email.policy import default as default_policy
from html import unescape
from typing import Any

from elen.plugins import Plugin, ToolResult, tool
from elen.secrets import resolve_secret


def html_to_text(html: str) -> str:
    html = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
    html = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>", "\n", html)
    text = unescape(re.sub(r"<[^>]+>", " ", html))
    return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n\n", text)).strip()


def body_text(msg: email.message.EmailMessage) -> str:
    part = msg.get_body(preferencelist=("plain", "html"))
    if part is None:
        return ""
    try:
        content = part.get_content()
    except (LookupError, UnicodeDecodeError):
        content = part.get_payload(decode=True).decode("utf-8", "replace")
    return html_to_text(content) if part.get_content_type() == "text/html" else content.strip()


def quote_folder(name: str) -> str:
    return '"' + name.replace('"', '\\"') + '"'


class Account:
    def __init__(self, cfg: dict[str, Any]):
        self.cfg = cfg
        self.name = cfg.get("name") or cfg.get("address", "mail")
        self.address = cfg.get("address", "")

    def password(self) -> str:
        return resolve_secret(self.cfg.get("password"))

    def imap(self) -> imaplib.IMAP4:
        host = self.cfg["imap_host"]
        port = int(self.cfg.get("imap_port", 993))
        ctx = ssl.create_default_context()
        if port == 993 or self.cfg.get("imap_ssl", port == 993):
            conn: imaplib.IMAP4 = imaplib.IMAP4_SSL(host, port, ssl_context=ctx, timeout=30)
        else:
            conn = imaplib.IMAP4(host, port, timeout=30)
            conn.starttls(ssl_context=ctx)
        conn.login(self.cfg.get("username") or self.address, self.password())
        return conn

    def smtp(self) -> smtplib.SMTP:
        host = self.cfg.get("smtp_host") or self.cfg["imap_host"]
        port = int(self.cfg.get("smtp_port", 587))
        ctx = ssl.create_default_context()
        if port == 465:
            conn: smtplib.SMTP = smtplib.SMTP_SSL(host, port, context=ctx, timeout=30)
        else:
            conn = smtplib.SMTP(host, port, timeout=30)
            conn.starttls(context=ctx)
        conn.login(self.cfg.get("username") or self.address, self.password())
        return conn


class EmailPlugin(Plugin):
    name = "email"
    description = "Read and send email."

    async def setup(self) -> None:
        self.accounts = {a.name: a for a in (Account(c) for c in self.config.get("accounts", []))}
        if not self.accounts:
            raise RuntimeError("no [[plugins.email.accounts]] configured")
        self._seen_addresses: set[str] = set()

    def prompt_hint(self) -> str:
        names = ", ".join(f"{a.name} <{a.address}>" for a in self.accounts.values())
        return f"mail accounts: {names}. The first one is the default."

    def known_addresses(self) -> set[str]:
        return {a.address for a in self.accounts.values()}

    def account(self, name: str = "") -> Account:
        if name and name in self.accounts:
            return self.accounts[name]
        if name:
            raise ValueError(f"No mail account named '{name}'. Accounts: {', '.join(self.accounts)}")
        return next(iter(self.accounts.values()))

    # -- sync helpers (run in a thread) --
    def _fetch_headers(self, acc: Account, folder: str, criteria: list[str], limit: int) -> list[dict]:
        conn = acc.imap()
        try:
            conn.select(quote_folder(folder), readonly=True)
            if all(c.isascii() for c in criteria):
                typ, data = conn.uid("SEARCH", *criteria)
            else:
                conn._encoding = "utf-8"  # send the UTF-8 search text as is
                typ, data = conn.uid("SEARCH", "CHARSET", "UTF-8", *criteria)
            if typ != "OK":
                raise RuntimeError(f"IMAP search failed: {data}")
            uids = data[0].split()[-limit:][::-1]
            out = []
            for uid in uids:
                typ, msg_data = conn.uid(
                    "FETCH", uid, "(FLAGS BODY.PEEK[HEADER.FIELDS (FROM TO SUBJECT DATE)])"
                )
                if typ != "OK" or not msg_data or not isinstance(msg_data[0], tuple):
                    continue
                flags = msg_data[0][0].decode(errors="replace")
                msg = email.message_from_bytes(msg_data[0][1], policy=default_policy)
                out.append(
                    {
                        "uid": uid.decode(),
                        "from": str(msg.get("From", "")),
                        "to": str(msg.get("To", "")),
                        "subject": str(msg.get("Subject", "")),
                        "date": str(msg.get("Date", "")),
                        "unread": "\\Seen" not in flags,
                    }
                )
            return out
        finally:
            try:
                conn.logout()
            except Exception:  # noqa: BLE001
                pass

    def _fetch_full(self, acc: Account, folder: str, uid: str) -> dict:
        conn = acc.imap()
        try:
            conn.select(quote_folder(folder), readonly=True)
            typ, data = conn.uid("FETCH", uid, "(BODY.PEEK[])")
            if typ != "OK" or not data or not isinstance(data[0], tuple):
                raise RuntimeError(f"Email {uid} not found in {folder}")
            msg = email.message_from_bytes(data[0][1], policy=default_policy)
            attachments = [p.get_filename() for p in msg.iter_attachments() if p.get_filename()]
            return {
                "uid": uid,
                "from": str(msg.get("From", "")),
                "to": str(msg.get("To", "")),
                "cc": str(msg.get("Cc", "")),
                "subject": str(msg.get("Subject", "")),
                "date": str(msg.get("Date", "")),
                "message_id": str(msg.get("Message-ID", "")),
                "body": body_text(msg)[:20000],
                "attachments": attachments,
            }
        finally:
            try:
                conn.logout()
            except Exception:  # noqa: BLE001
                pass

    def _build(self, acc: Account, to: str, subject: str, body: str, cc: str, reply_to_uid: str, folder: str) -> EmailMessage:
        msg = EmailMessage()
        msg["From"] = email.utils.formataddr((acc.cfg.get("display_name", ""), acc.address))
        msg["To"] = to
        if cc:
            msg["Cc"] = cc
        msg["Subject"] = subject
        msg["Date"] = email.utils.formatdate(localtime=True)
        msg["Message-ID"] = email.utils.make_msgid(domain=acc.address.split("@")[-1] or None)
        if reply_to_uid:
            orig = self._fetch_full(acc, folder, reply_to_uid)
            if orig.get("message_id"):
                msg["In-Reply-To"] = orig["message_id"]
                msg["References"] = orig["message_id"]
        signature = acc.cfg.get("signature", "")
        msg.set_content(body + (f"\n\n{signature}" if signature else ""))
        return msg

    def _append(self, acc: Account, folder: str, msg: EmailMessage, flags: str) -> None:
        conn = acc.imap()
        try:
            conn.append(quote_folder(folder), flags, imaplib.Time2Internaldate(time.time()), msg.as_bytes())
        finally:
            try:
                conn.logout()
            except Exception:  # noqa: BLE001
                pass

    def _send(self, acc: Account, msg: EmailMessage) -> None:
        conn = acc.smtp()
        try:
            conn.send_message(msg)
        finally:
            try:
                conn.quit()
            except Exception:  # noqa: BLE001
                pass
        if acc.cfg.get("sent_folder", "Sent"):
            try:
                self._append(acc, acc.cfg.get("sent_folder", "Sent"), msg, "(\\Seen)")
            except Exception as e:  # noqa: BLE001
                self.log.warning("could not save to Sent: %s", e)

    # -- tools --
    @tool(
        "List recent emails (newest first) and show them on screen.",
        params={
            "account": "string: account name (empty = default)",
            "folder": "string: mail folder, default INBOX",
            "unread_only": "boolean: only unread mail",
            "limit": "integer: how many, default 10",
        },
        untrusted=True,
    )
    async def list_emails(self, account: str = "", folder: str = "INBOX", unread_only: bool = False, limit: int = 10):
        acc = self.account(account)
        criteria = ["UNSEEN"] if unread_only else ["ALL"]
        items = await asyncio.to_thread(self._fetch_headers, acc, folder or "INBOX", criteria, min(int(limit), 50))
        return ToolResult(
            data={"account": acc.name, "folder": folder, "emails": items},
            visual={
                "type": "list",
                "title": f"{'Unread' if unread_only else 'Inbox'} · {acc.name}",
                "subtitle": f"{len(items)} messages",
                "items": [
                    {"title": m["subject"] or "(no subject)", "subtitle": m["from"], "meta": m["date"][:22], "unread": m["unread"]}
                    for m in items
                ],
            },
        )

    @tool(
        "Search emails by sender, subject or text.",
        params={
            "query": "string: words to find",
            "field": {"type": "string", "enum": ["any", "from", "subject", "body"], "description": "where to search"},
            "since": "string: only mail since this date, YYYY-MM-DD",
            "account": "string: account name",
            "folder": "string: mail folder, default INBOX",
            "limit": "integer: default 10",
        },
        required=["query"],
        untrusted=True,
    )
    async def search_emails(self, query: str, field: str = "any", since: str = "", account: str = "", folder: str = "INBOX", limit: int = 10):
        acc = self.account(account)
        q = '"' + query.replace('"', "") + '"'
        if field == "from":
            criteria = ["FROM", q]
        elif field == "subject":
            criteria = ["SUBJECT", q]
        elif field == "body":
            criteria = ["BODY", q]
        else:
            criteria = ["OR", "OR", "FROM", q, "SUBJECT", q, "BODY", q]
        if since:
            day = time.strptime(since, "%Y-%m-%d")
            criteria += ["SINCE", time.strftime("%d-%b-%Y", day)]
        items = await asyncio.to_thread(self._fetch_headers, acc, folder or "INBOX", criteria, min(int(limit), 50))
        return ToolResult(
            data={"emails": items},
            visual={
                "type": "list",
                "title": f"Mail search: {query}",
                "items": [{"title": m["subject"], "subtitle": m["from"], "meta": m["date"][:22]} for m in items],
            },
        )

    @tool(
        "Read one email in full (by uid from list or search) and show it on screen.",
        params={"uid": "string: email uid", "account": "string", "folder": "string: default INBOX"},
        required=["uid"],
        untrusted=True,
    )
    async def read_email(self, uid: str, account: str = "", folder: str = "INBOX"):
        acc = self.account(account)
        msg = await asyncio.to_thread(self._fetch_full, acc, folder or "INBOX", str(uid))
        return ToolResult(data=msg, visual={"type": "email", "title": "Email", **{k: msg[k] for k in ("from", "to", "cc", "subject", "date", "body")}})

    @tool(
        "Send an email. The user sees the full email and must approve it before it is sent. "
        "Write the complete final text. For a reply give reply_to_uid.",
        params={
            "to": "string: recipient addresses, comma separated",
            "subject": "string",
            "body": "string: full plain-text body",
            "cc": "string: cc addresses, comma separated",
            "reply_to_uid": "string: uid of the email you reply to",
            "account": "string: account to send from",
            "folder": "string: folder of the email you reply to",
        },
        required=["to", "subject", "body"],
        risk="write",
        editable=["to", "cc", "subject", "body"],
        recipients=["to", "cc"],
        title="Send email",
    )
    async def send_email(self, to: str, subject: str, body: str, cc: str = "", reply_to_uid: str = "", account: str = "", folder: str = "INBOX"):
        acc = self.account(account)
        msg = await asyncio.to_thread(self._build, acc, to, subject, body, cc, reply_to_uid, folder or "INBOX")
        await asyncio.to_thread(self._send, acc, msg)
        return {"sent": True, "from": acc.address, "to": to, "cc": cc, "subject": subject}

    @tool(
        "Save an email as a draft in the Drafts folder (it is not sent).",
        params={
            "to": "string",
            "subject": "string",
            "body": "string",
            "cc": "string",
            "account": "string",
        },
        required=["to", "subject", "body"],
        risk="low",
    )
    async def save_draft(self, to: str, subject: str, body: str, cc: str = "", account: str = ""):
        acc = self.account(account)
        msg = await asyncio.to_thread(self._build, acc, to, subject, body, cc, "", "INBOX")
        folder = acc.cfg.get("drafts_folder", "Drafts")
        await asyncio.to_thread(self._append, acc, folder, msg, "(\\Draft)")
        return {"saved_to": folder}
