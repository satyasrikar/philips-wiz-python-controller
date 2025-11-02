# WiZ Controller (Python GUI)

A modern Tk/ttkbootstrap GUI to control **Philips WiZ** bulbs locally over UDP.

- Auto-discovers bulbs on your LAN
- Power, brightness, white temperature
- RGB picker with HEX + live preview
- Built-in & custom presets (persist to `presets.json`)
- Smooth fades (animate RGB/CT over N ms)
- Sidebar UI, resizable, dark theme

> **Note**: This targets **WiZ** (UDP port 38899), not Philips Hue.

## Install (Python 3.10+)

```bash
python -m venv wizenv
source wizenv/bin/activate   # Windows: wizenv\Scripts\activate
pip install -r requirements.txt
```
