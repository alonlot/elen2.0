// In-process providers. To add one written in GJS:
//   1. Put it in this folder (copy demo.js as a start).
//   2. Register it here with a type name.
//   3. Turn it on: add the type name to the "providers" setting
//      (gsettings set org.gnome.shell.extensions.chathub providers "['demo', 'mytype']").
//
// Providers in other languages do not need this file. They use the D-Bus API
// and Chat Hub finds them by itself (see docs/API.md).
import {registerProvider} from '../lib/api.js';
import {DemoProvider} from './demo.js';

export function registerBuiltinProviders() {
    registerProvider('demo', () => new DemoProvider());
}
