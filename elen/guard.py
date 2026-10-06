"""Safety guard.

The guard has four jobs:
1. Ask the user before any "write" or "dangerous" tool runs. The user sees a
   full preview and can edit the fields the tool marks as editable.
2. Check recipients (email addresses, phone numbers). A recipient that is not
   in the contacts, not typed by the user, and not seen in earlier tool data is
   flagged as UNVERIFIED in the approval dialog.
3. Catch replies that claim an action ("I sent the email") when no such action
   ran in this turn.
4. Keep an audit log of every action at ~/.local/share/elen/audit.log.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from urllib.parse import urlparse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .plugins import ToolSpec

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"\+?\d[\d\s\-().]{6,}\d")

ACTION_CLAIM_RE = re.compile(
    r"\b(?:I(?:'ve| have)?|I just|I've just|has been|have been|was|were)\s+"
    r"(?:successfully\s+)?"
    r"(sent|emailed|mailed|replied|forwarded|scheduled|booked|created|added|deleted|removed|"
    r"cancel(?:l)?ed|called|messaged|texted|moved|ran|executed|installed|saved|submitted|paid|"
    r"ordered|posted|published|updated)\b",
    re.IGNORECASE,
)


def split_addresses(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        items: list[str] = []
        for v in value:
            items.extend(split_addresses(v))
        return items
    text = str(value)
    return [p.strip() for p in re.split(r"[,;]", text) if p.strip()]


def normalise(addr: str) -> str:
    m = EMAIL_RE.search(addr)
    if m:
        return m.group(0).lower()
    digits = re.sub(r"[^\d+]", "", addr)
    return digits or addr.strip().lower()


@dataclass
class Confirmation:
    id: str
    tool: str
    title: str
    plugin: str
    risk: str
    arguments: dict[str, Any]
    editable: list[str]
    warnings: list[str] = field(default_factory=list)
    recipients: list[dict[str, Any]] = field(default_factory=list)
    allow_label: str = ""  # offered "don't ask again" scope, empty = not offered
    created: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "tool": self.tool,
            "title": self.title,
            "plugin": self.plugin,
            "risk": self.risk,
            "arguments": self.arguments,
            "editable": self.editable,
            "warnings": self.warnings,
            "recipients": self.recipients,
            "allow_label": self.allow_label,
        }


def scope_value(value: Any) -> str:
    """How an argument counts for an allow rule: a URL by its domain, addresses normalised."""
    if isinstance(value, (list, tuple)):
        return ", ".join(sorted(scope_value(v) for v in value))
    text = str(value or "").strip()
    if re.match(r"^[a-z][a-z0-9+.-]*://", text, re.I):
        host = (urlparse(text).hostname or "").lower()
        return host[4:] if host.startswith("www.") else host
    if EMAIL_RE.search(text):
        return ", ".join(sorted(normalise(a) for a in split_addresses(text)))
    return text.lower()


class AllowRules:
    """ "Approve and don't ask again" rules, made only by the user in the approval dialog."""

    def __init__(self, path: Path):
        self.path = path
        try:
            self.rules: list[dict[str, Any]] = json.loads(path.read_text())
        except (OSError, ValueError):
            self.rules = []

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.rules, ensure_ascii=False, indent=1))
        self.path.chmod(0o600)

    def match(self, tool: str, scope: dict[str, str]) -> dict[str, Any] | None:
        for rule in self.rules:
            if rule["tool"] == tool and rule["scope"] == scope:
                return rule
        return None

    def add(self, tool: str, scope: dict[str, str], label: str) -> dict[str, Any]:
        existing = self.match(tool, scope)
        if existing:
            return existing
        rule = {"id": uuid.uuid4().hex[:6], "tool": tool, "scope": scope, "label": label, "created": time.time()}
        self.rules.append(rule)
        self._save()
        return rule

    def remove(self, rule_id: str) -> bool:
        before = len(self.rules)
        self.rules = [r for r in self.rules if r["id"] != rule_id]
        self._save()
        return len(self.rules) != before


class Guard:
    def __init__(self, config: dict[str, Any], audit_path: Path):
        self.cfg = config.get("guard", {})
        self.audit_path = audit_path
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)

    def effective_risk(self, spec: ToolSpec) -> str:
        overrides = self.cfg.get("overrides") or {}
        return overrides.get(spec.full_name) or overrides.get(spec.plugin) or spec.risk

    def needs_confirmation(self, spec: ToolSpec) -> bool:
        return self.effective_risk(spec) in ("write", "dangerous")

    def allow_scope(self, spec: ToolSpec, args: dict[str, Any]) -> dict[str, str] | None:
        """The scope an allow rule for this call would have, or None if it cannot have one."""
        if self.effective_risk(spec) == "dangerous" or not self.cfg.get("allow_rules", True):
            return None
        keys = spec.allow_scope if spec.allow_scope is not None else spec.recipients
        return {k: scope_value(args.get(k)) for k in keys}

    @staticmethod
    def scope_label(spec: ToolSpec, scope: dict[str, str]) -> str:
        if not scope:
            return f"{spec.title} (any)"
        return f"{spec.title}: " + ", ".join(f"{k} = {v}" for k, v in scope.items())

    def check_recipients(
        self,
        spec: ToolSpec,
        args: dict[str, Any],
        known: set[str],
        user_texts: Iterable[str],
        tool_texts: Iterable[str],
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """Return (recipient report, warnings) for the recipient args of a tool."""
        if not spec.recipients or not self.cfg.get("verify_recipients", True):
            return [], []
        known_norm = {normalise(k) for k in known}
        user_blob = "\n".join(user_texts).lower()
        user_norm = {normalise(m) for m in EMAIL_RE.findall(user_blob)}
        user_norm |= {normalise(m) for m in PHONE_RE.findall(user_blob)}
        tool_blob = "\n".join(tool_texts).lower()
        tool_norm = {normalise(m) for m in EMAIL_RE.findall(tool_blob)}
        tool_norm |= {normalise(m) for m in PHONE_RE.findall(tool_blob)}

        report: list[dict[str, Any]] = []
        warnings: list[str] = []
        for field_name in spec.recipients:
            for raw in split_addresses(args.get(field_name)):
                norm = normalise(raw)
                if field_name and "@" not in raw and not PHONE_RE.search(raw):
                    status = "invalid"
                    warnings.append(f"'{raw}' in '{field_name}' is not an address or number.")
                elif norm in known_norm:
                    status = "contact"
                elif norm in user_norm:
                    status = "typed_by_you"
                elif norm in tool_norm:
                    status = "from_data"
                else:
                    status = "unverified"
                    warnings.append(
                        f"UNVERIFIED recipient {raw}: not in your contacts, not typed by you, "
                        "and not found in any data Elen read. Check it is the right person."
                    )
                report.append({"field": field_name, "value": raw, "status": status})
        return report, warnings

    def action_claim_warning(self, text: str, executed: list[dict[str, Any]]) -> str | None:
        """Flag a reply that claims an action no tool performed in this turn."""
        if not self.cfg.get("action_claim_check", True) or not text:
            return None
        if any(a.get("ok") and a.get("risk") in ("low", "write", "dangerous") for a in executed):
            return None
        m = ACTION_CLAIM_RE.search(text)
        if not m:
            return None
        return (
            f"Guard: this reply says '{m.group(0)}', but no action ran in this turn. "
            "Nothing was done on your behalf."
        )

    def audit(self, event: str, **data: Any) -> None:
        entry = {"time": time.strftime("%Y-%m-%dT%H:%M:%S"), "event": event, **data}
        try:
            with open(self.audit_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
        except OSError:
            pass
