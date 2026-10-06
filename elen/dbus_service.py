"""D-Bus service org.elen.Assistant on the session bus.

The GNOME Shell extension talks to the daemon only through this interface.
All events from the core go out as one signal: Event(kind, json_payload).
"""

import json
import logging

from dbus_next import BusType
from dbus_next.aio import MessageBus
from dbus_next.constants import RequestNameReply
from dbus_next.service import ServiceInterface, method, signal

from . import APP_ID, OBJECT_PATH
from .core import Elen

log = logging.getLogger("elen.dbus")


class ElenInterface(ServiceInterface):
    def __init__(self, core: Elen):
        super().__init__(APP_ID)
        self.core = core
        core.subscribe(self._on_event)

    def _on_event(self, kind: str, payload: dict) -> None:
        self.Event(kind, json.dumps(payload, ensure_ascii=False, default=str))

    @signal()
    def Event(self, kind, payload) -> "ss":
        return [kind, payload]

    @method()
    def SendMessage(self, text: "s") -> "s":
        return self.core.submit(text)

    @method()
    def GetHistory(self) -> "s":
        return json.dumps(self.core.history.display, ensure_ascii=False)

    @method()
    def ShowVisual(self, visual_id: "s") -> "b":
        """Show a saved visual again."""
        return self.core.reshow_visual(visual_id)

    @method()
    def ClearHistory(self) -> "b":
        self.core.clear_history()
        return True

    @method()
    def Confirm(self, conf_id: "s", approved: "b", arguments_json: "s", reason: "s") -> "b":
        return self.core.resolve_confirmation(conf_id, approved, arguments_json, reason)

    @method()
    def PendingConfirmations(self) -> "s":
        return json.dumps(self.core.pending_confirmations(), ensure_ascii=False)

    @method()
    def Stop(self) -> "b":
        """Stop the running request, speech and recording."""
        return self.core.cancel()

    @method()
    def ToggleListening(self) -> "s":
        return self.core.toggle_listening()

    @method()
    def ToggleWake(self) -> "s":
        """Turn the wake word on or off."""
        return json.dumps(self.core.toggle_wake())

    @method()
    def RegisterUI(self) -> "s":
        self.core.ui_present = True
        return json.dumps(self.core.status())

    @method()
    def ScreenshotDone(self, shot_id: "s", ok: "b") -> "b":
        self.core.screenshot_done(shot_id, ok)
        return True

    @method()
    def GetStatus(self) -> "s":
        return json.dumps(self.core.status())

    @method()
    def GetModels(self) -> "s":
        from .config_edit import get_model_settings

        return json.dumps(get_model_settings())

    @method()
    def SetModels(self, settings_json: "s") -> "s":
        """Save model / URL / key settings to config.toml, then reload."""
        from .config_edit import apply_model_settings

        try:
            apply_model_settings(json.loads(settings_json))
        except (ValueError, OSError) as e:
            return json.dumps({"ok": False, "error": str(e)})
        self.core._spawn(self._reload())
        return json.dumps({"ok": True})

    @method()
    def Reload(self) -> "s":
        self.core._spawn(self._reload())
        return "reloading"

    async def _reload(self) -> None:
        status = await self.core.reload()
        self.core.emit("reloaded", status)


async def serve(core: Elen) -> None:
    bus = await MessageBus(bus_type=BusType.SESSION).connect()
    iface = ElenInterface(core)
    bus.export(OBJECT_PATH, iface)
    reply = await bus.request_name(APP_ID)
    if reply not in (RequestNameReply.PRIMARY_OWNER, RequestNameReply.ALREADY_OWNER):
        raise SystemExit("Another Elen daemon is already running.")
    log.info("Elen 2.0 is on the session bus as %s", APP_ID)
    core.set_state("idle")
    await bus.wait_for_disconnect()
