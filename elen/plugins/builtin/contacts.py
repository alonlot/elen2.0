"""Contacts: the trusted list of people the guard uses to check recipients.

Sources:
  ~/.config/elen/contacts.toml   (edit by hand, see config/contacts.example.toml)
  vCard files                    [plugins.contacts] vcf_paths = ["~/contacts.vcf"]
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

from elen.config import config_dir
from elen.plugins import Plugin, ToolResult, tool

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib


def parse_vcf(text: str) -> list[dict[str, Any]]:
    contacts, cur = [], None
    text = re.sub(r"\r?\n[ \t]", "", text)  # unfold lines
    for line in text.splitlines():
        upper = line.upper()
        if upper.startswith("BEGIN:VCARD"):
            cur = {"name": "", "emails": [], "phones": [], "aliases": [], "notes": ""}
        elif upper.startswith("END:VCARD") and cur is not None:
            if cur["name"] or cur["emails"]:
                contacts.append(cur)
            cur = None
        elif cur is not None and ":" in line:
            key, value = line.split(":", 1)
            key = key.split(";")[0].upper()
            if key == "FN":
                cur["name"] = value.strip()
            elif key == "EMAIL":
                cur["emails"].append(value.strip())
            elif key == "TEL":
                cur["phones"].append(value.strip())
            elif key == "NICKNAME":
                cur["aliases"].extend(v.strip() for v in value.split(","))
            elif key == "ORG":
                cur["notes"] = value.replace(";", " ").strip()
    return contacts


class ContactsPlugin(Plugin):
    name = "contacts"
    description = "Look up people."

    async def setup(self) -> None:
        self.path = Path(self.config.get("file") or config_dir() / "contacts.toml").expanduser()
        self.load()

    def load(self) -> None:
        self.contacts: list[dict[str, Any]] = []
        if self.path.exists():
            with open(self.path, "rb") as f:
                for c in tomllib.load(f).get("contact", []):
                    self.contacts.append(
                        {
                            "name": c.get("name", ""),
                            "emails": list(c.get("emails", [])),
                            "phones": list(c.get("phones", [])),
                            "aliases": list(c.get("aliases", [])),
                            "notes": c.get("notes", ""),
                            "photo": str(Path(c["photo"]).expanduser()) if c.get("photo") else "",
                        }
                    )
        for p in self.config.get("vcf_paths", []):
            path = Path(p).expanduser()
            if path.exists():
                self.contacts.extend(parse_vcf(path.read_text(errors="replace")))

    def known_addresses(self) -> set[str]:
        out: set[str] = set()
        for c in self.contacts:
            out.update(c["emails"])
            out.update(c["phones"])
        return out

    def search(self, query: str) -> list[dict[str, Any]]:
        q = query.lower().strip()
        words = q.split()
        hits = []
        for c in self.contacts:
            hay = " ".join([c["name"], *c["aliases"], *c["emails"], c["notes"]]).lower()
            if q and (q in hay or all(w in hay for w in words)):
                hits.append(c)
        return hits

    @tool(
        "Find a contact by name, nickname, company or email. Returns every match. If there is "
        "more than one match, ask the user which person they mean.",
        params={"query": "string: name or part of a name"},
    )
    async def find_contact(self, query: str):
        hits = self.search(query)
        result = {"query": query, "matches": hits, "count": len(hits)}
        if not hits:
            result["note"] = "No contact found. Ask the user for the exact address."
            return result
        if len(hits) > 1:
            result["note"] = "More than one match. Ask the user which one."
            visual = {
                "type": "list",
                "title": f"{len(hits)} contacts match '{query}'",
                "items": [{"title": c["name"], "subtitle": ", ".join(c["emails"] + c["phones"]), "meta": c["notes"]} for c in hits],
            }
        else:
            c = hits[0]
            fields = [{"label": "Email", "value": e} for e in c["emails"]]
            fields += [{"label": "Phone", "value": p} for p in c["phones"]]
            if c["notes"]:
                fields.append({"label": "Notes", "value": c["notes"]})
            visual = {"type": "card", "title": c["name"], "subtitle": "Contact", "fields": fields, "tags": c["aliases"]}
            if c.get("photo"):
                visual["image"] = c["photo"]
        return ToolResult(data=result, visual=visual)

    @tool(
        "Add a person to the contacts file.",
        params={
            "name": "string: full name",
            "emails": "array: email addresses",
            "phones": "array: phone numbers",
        },
        required=["name"],
        risk="write",
        editable=["name", "emails", "phones"],
        title="Add contact",
    )
    async def add_contact(self, name: str, emails: list | None = None, phones: list | None = None):
        def q(s: str) -> str:
            return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'

        emails = [e for e in (emails or []) if e] if isinstance(emails, list) else [str(emails)]
        phones = [p for p in (phones or []) if p] if isinstance(phones, list) else [str(phones)]
        block = (
            f"\n[[contact]]\nname = {q(name)}\n"
            f"emails = [{', '.join(q(e) for e in emails)}]\n"
            f"phones = [{', '.join(q(p) for p in phones)}]\n"
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(block)
        self.load()
        return {"added": name}

    @tool("Show all contacts on screen.")
    async def list_contacts(self):
        return ToolResult(
            data={"count": len(self.contacts)},
            visual={
                "type": "list",
                "title": "Contacts",
                "items": [
                    {"title": c["name"], "subtitle": ", ".join(c["emails"] + c["phones"]), "meta": c["notes"]}
                    for c in self.contacts
                ],
            },
        )
