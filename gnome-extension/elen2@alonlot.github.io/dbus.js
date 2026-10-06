// D-Bus client for the Elen daemon (org.elen.Assistant on the session bus).
import Gio from 'gi://Gio';

const IFACE = `
<node>
  <interface name="org.elen.Assistant">
    <method name="SendMessage"><arg type="s" direction="in"/><arg type="s" direction="out"/></method>
    <method name="GetHistory"><arg type="s" direction="out"/></method>
    <method name="ClearHistory"><arg type="b" direction="out"/></method>
    <method name="Confirm">
      <arg type="s" direction="in"/><arg type="b" direction="in"/>
      <arg type="s" direction="in"/><arg type="s" direction="in"/>
      <arg type="b" direction="out"/>
    </method>
    <method name="PendingConfirmations"><arg type="s" direction="out"/></method>
    <method name="ToggleListening"><arg type="s" direction="out"/></method>
    <method name="RegisterUI"><arg type="s" direction="out"/></method>
    <method name="ScreenshotDone">
      <arg type="s" direction="in"/><arg type="b" direction="in"/><arg type="b" direction="out"/>
    </method>
    <method name="GetStatus"><arg type="s" direction="out"/></method>
    <method name="Reload"><arg type="s" direction="out"/></method>
    <signal name="Event"><arg type="s" name="kind"/><arg type="s" name="payload"/></signal>
  </interface>
</node>`;

const ElenProxy = Gio.DBusProxy.makeProxyWrapper(IFACE);

export class ElenClient {
    constructor(onEvent, onConnection) {
        this._onEvent = onEvent;
        this._onConnection = onConnection;
        this._proxy = null;
        this._signalId = 0;
        this._ownerId = 0;
        this._cancellable = new Gio.Cancellable();
        new ElenProxy(Gio.DBus.session, 'org.elen.Assistant', '/org/elen/Assistant',
            (proxy, error) => {
                if (error) {
                    console.error(`Elen: cannot create D-Bus proxy: ${error}`);
                    return;
                }
                this._proxy = proxy;
                this._signalId = proxy.connectSignal('Event', (_p, _sender, [kind, payload]) => {
                    let data = {};
                    try {
                        data = JSON.parse(payload);
                    } catch (e) {
                        console.error(`Elen: bad event payload: ${e}`);
                    }
                    this._onEvent(kind, data);
                });
                this._ownerId = proxy.connect('notify::g-name-owner',
                    () => this._onConnection(!!proxy.g_name_owner));
                this._onConnection(!!proxy.g_name_owner);
            }, this._cancellable, Gio.DBusProxyFlags.NONE);
    }

    get connected() {
        return !!this._proxy?.g_name_owner;
    }

    // Call a daemon method. Returns a Promise with the first return value.
    // A call also starts the daemon through D-Bus activation when it is not running.
    call(method, args = []) {
        return new Promise((resolve, reject) => {
            if (!this._proxy) {
                reject(new Error('Elen daemon is not reachable'));
                return;
            }
            this._proxy[`${method}Remote`](...args, (result, error) => {
                if (error)
                    reject(error);
                else
                    resolve(result?.[0]);
            });
        });
    }

    destroy() {
        this._cancellable.cancel();
        if (this._proxy) {
            if (this._signalId)
                this._proxy.disconnectSignal(this._signalId);
            if (this._ownerId)
                this._proxy.disconnect(this._ownerId);
        }
        this._proxy = null;
    }
}
