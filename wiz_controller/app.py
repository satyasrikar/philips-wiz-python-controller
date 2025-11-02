# WiZ Controller — sidebar UI, presets, RGB picker, smooth fades
import json, os, socket, threading, time, math, tkinter as tk
from tkinter import messagebox, simpledialog, colorchooser
import ttkbootstrap as tb
from ttkbootstrap.constants import *

PORT = 38899
DISCOVER_MSG = {"method": "getSystemConfig", "params": {}}
# Keep presets alongside the working directory (portable for users)
PRESETS_FILE = os.path.join(os.getcwd(), "presets.json")

# ---------------- WiZ helpers ----------------
def discover(timeout=3.5):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    s.settimeout(timeout)
    s.sendto(json.dumps(DISCOVER_MSG).encode(), ("255.255.255.255", PORT))
    found, t0 = [], time.time()
    while time.time() - t0 < timeout:
        try:
            data, addr = s.recvfrom(4096)
            msg = json.loads(data.decode(errors="ignore"))
            if msg.get("result"):
                info = msg["result"]
                info["_ip"] = addr[0]
                found.append(info)
        except socket.timeout:
            break
    s.close()
    # de-dupe by ip
    return list({b["_ip"]: b for b in found}.values())

def _send(ip, payload, wait_ack=False):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(1.0)
    s.sendto(json.dumps(payload).encode(), (ip, PORT))
    resp = None
    if wait_ack:
        try:
            data, _ = s.recvfrom(4096)
            resp = json.loads(data.decode(errors="ignore"))
        except socket.timeout:
            pass
    s.close()
    return resp

def pilot(ip, **kwargs):
    params = {"state": True}
    params.update(kwargs)
    return _send(ip, {"method": "setPilot", "params": params})

def power(ip, on=True):
    return _send(ip, {"method": "setPilot", "params": {"state": bool(on)}})

def get_state(ip):
    return _send(ip, {"method": "getPilot", "params": {}}, wait_ack=True)

# ---------------- Built-in presets ----------------
BUILTIN = [
    ("Warm",   {"temp": 2700, "dimming": 65}),
    ("Cool",   {"temp": 5000, "dimming": 75}),
    ("Focus",  {"temp": 6500, "dimming": 100}),
    ("Relax",  {"temp": 2200, "dimming": 40}),
    ("Sunset", {"r": 255, "g": 120, "b": 40, "dimming": 55}),
    ("Forest", {"r": 0,   "g": 180, "b": 80, "dimming": 60}),
    ("Night",  {"temp": 2200, "dimming": 10}),
]

# ---------------- Main App ----------------
class WizApp(tb.Window):
    def __init__(self, bulbs):
        super().__init__(title="WiZ Controller", themename="darkly")
        self.geometry("980x620")
        self.minsize(860, 560)

        self.bulbs = bulbs
        self.current_ip = None
        self.custom_presets = self._load_custom_presets()

        # animation state
        self._fade_job = None

        # overall layout
        root = tb.Frame(self)
        root.pack(fill=BOTH, expand=YES)

        # sidebar
        self.sidebar = tb.Frame(root, width=70, bootstyle="dark")
        self.sidebar.pack(side=LEFT, fill=Y)
        self.sidebar.pack_propagate(False)

        # main area
        self.main = tb.Frame(root)
        self.main.pack(side=RIGHT, fill=BOTH, expand=YES)

        # topbar in main
        self._build_topbar(self.main)

        # stacked pages
        self.pages = {}
        self._build_pages(self.main)

        # sidebar buttons
        self._build_sidebar()

        # load devices
        self._populate_devices()

        # show default page
        self._show_page("dashboard")

    # ------------- Sidebar -------------
    def _build_sidebar(self):
        btn_style = (SECONDARY, OUTLINE)
        def add_btn(text, page, emoji):
            b = tb.Button(self.sidebar, text=f"{emoji}", bootstyle=btn_style, width=4,
                          command=lambda: self._show_page(page))
            b.pack(pady=8)
            lb = tb.Label(self.sidebar, text=text, font=("", 7))
            lb.pack()
        tb.Label(self.sidebar, text="WiZ", font=("", 12, "bold")).pack(pady=(10, 20))
        add_btn("Dashboard", "dashboard", "🏠")
        add_btn("Presets", "presets", "💡")
        add_btn("Color", "color", "🎨")
        add_btn("Device", "device", "⚙️")

    # ------------- Top bar -------------
    def _build_topbar(self, parent):
        bar = tb.Frame(parent, padding=10)
        bar.pack(fill=X)
        bar.columnconfigure(1, weight=1)

        tb.Label(bar, text="Device", bootstyle=INFO).grid(row=0, column=0, sticky=W, padx=(0, 8))
        self.device_var = tk.StringVar()
        self.device_combo = tb.Combobox(bar, textvariable=self.device_var, state="readonly")
        self.device_combo.grid(row=0, column=1, sticky="ew")
        self.device_combo.bind("<<ComboboxSelected>>", self._on_device_change)

        tb.Button(bar, text="Rescan", bootstyle=(SECONDARY, OUTLINE), command=self._rescan)\
            .grid(row=0, column=2, padx=(10, 0))

        tb.Button(bar, text="On", bootstyle=SUCCESS, width=6,
                  command=lambda: self._do(lambda ip: power(ip, True))).grid(row=0, column=3, padx=4)
        tb.Button(bar, text="Off", bootstyle=DANGER, width=6,
                  command=lambda: self._do(lambda ip: power(ip, False))).grid(row=0, column=4, padx=4)
        tb.Button(bar, text="Get", bootstyle=INFO, width=6,
                  command=self._get_state).grid(row=0, column=5, padx=4)

    # ------------- Pages -------------
    def _build_pages(self, parent):
        content = tb.Frame(parent)
        content.pack(fill=BOTH, expand=YES)

        dash = tb.Frame(content)
        dash.pack(fill=BOTH, expand=YES)
        self.pages["dashboard"] = dash
        self._build_dashboard(dash)

        presets = tb.Frame(content)
        self.pages["presets"] = presets
        self._build_presets_page(presets)

        colorp = tb.Frame(content)
        self.pages["color"] = colorp
        self._build_color_page(colorp)

        dev = tb.Frame(content)
        self.pages["device"] = dev
        self._build_device_page(dev)

    def _show_page(self, name):
        for _, frame in self.pages.items():
            frame.pack_forget()
        self.pages[name].pack(fill=BOTH, expand=YES)

    # ------------- Dashboard -------------
    def _build_dashboard(self, parent):
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(1, weight=1)

        cards = tb.Frame(parent)
        cards.grid(row=0, column=0, sticky="ew", padx=10, pady=(4, 10))

        bri_card = tb.Labelframe(cards, text="Brightness", padding=10)
        bri_card.pack(side=LEFT, fill=X, expand=YES, padx=(0, 10))
        self.bri = tb.Scale(bri_card, from_=10, to=100, orient=HORIZONTAL,
                            command=lambda v: self._do(lambda ip: pilot(ip, dimming=int(float(v)))))
        self.bri.set(60)
        self.bri.pack(fill=X)

        tmp_card = tb.Labelframe(cards, text="White Temp (K)", padding=10)
        tmp_card.pack(side=LEFT, fill=X, expand=YES, padx=(0, 10))
        self.tmp = tb.Scale(tmp_card, from_=2000, to=6500, orient=HORIZONTAL,
                            command=lambda v: self._do(lambda ip: pilot(ip, temp=int(float(v)))))
        self.tmp.set(3500)
        self.tmp.pack(fill=X)

        qa = tb.Labelframe(parent, text="Quick actions", padding=10)
        qa.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 10))
        qa.columnconfigure((0, 1, 2), weight=1)
        tb.Button(qa, text="Relax", bootstyle=SECONDARY,
                  command=lambda: self._apply_preset({"temp": 2200, "dimming": 40}, "Relax")).grid(row=0, column=0, sticky="ew", padx=6, pady=6)
        tb.Button(qa, text="Focus", bootstyle=SECONDARY,
                  command=lambda: self._apply_preset({"temp": 6500, "dimming": 100}, "Focus")).grid(row=0, column=1, sticky="ew", padx=6, pady=6)
        tb.Button(qa, text="Sunset", bootstyle=SECONDARY,
                  command=lambda: self._apply_preset({"r": 255, "g": 120, "b": 40, "dimming": 55}, "Sunset")).grid(row=0, column=2, sticky="ew", padx=6, pady=6)

        logbox = tb.Labelframe(parent, text="Log", padding=6)
        logbox.grid(row=2, column=0, sticky="nsew", padx=10, pady=(0, 10))
        logbox.rowconfigure(0, weight=1)
        logbox.columnconfigure(0, weight=1)
        self.log = tk.Text(logbox, height=5, wrap="word")
        self.log.grid(row=0, column=0, sticky="nsew")
        self._log("Ready.")

    # ------------- Presets page -------------
    def _build_presets_page(self, parent):
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(1, weight=1)

        tb.Label(parent, text="Built-in presets", font=("", 11, "bold")).grid(row=0, column=0, sticky="w", padx=10, pady=(10, 4))
        built = tb.Frame(parent)
        built.grid(row=1, column=0, sticky="ew", padx=10)
        for i, (name, params) in enumerate(BUILTIN):
            tb.Button(built, text=name, bootstyle=(SECONDARY),
                      command=lambda p=params, n=name: self._apply_or_fade(p, n))\
              .grid(row=i//3, column=i%3, sticky="ew", padx=4, pady=4)
            built.columnconfigure(i % 3, weight=1)

        wrapper = tb.Frame(parent)
        wrapper.grid(row=2, column=0, sticky="nsew", padx=10, pady=10)
        wrapper.columnconfigure(0, weight=1)
        wrapper.rowconfigure(0, weight=1)

        tb.Label(wrapper, text="My presets", font=("", 11, "bold")).grid(row=0, column=0, sticky="w", pady=(0, 6))
        inner = tb.Frame(wrapper)
        inner.grid(row=1, column=0, sticky="nsew")
        inner.columnconfigure(0, weight=1)
        inner.rowconfigure(0, weight=1)

        self.preset_list = tk.Listbox(inner, height=8)
        self.preset_list.grid(row=0, column=0, sticky="nsew")
        self._refresh_preset_list()

        btns = tb.Frame(inner)
        btns.grid(row=0, column=1, sticky="nsw", padx=(8, 0))
        tb.Button(btns, text="Apply", bootstyle=PRIMARY,
                  command=self._apply_selected_custom).pack(fill=X, pady=2)
        tb.Button(btns, text="Fade 1.5s", bootstyle=INFO,
                  command=lambda: self._apply_selected_custom(fade_ms=1500)).pack(fill=X, pady=2)
        tb.Button(btns, text="Save current…", bootstyle=SUCCESS,
                  command=self._save_current_as_preset).pack(fill=X, pady=2)
        tb.Button(btns, text="Delete", bootstyle=DANGER,
                  command=self._delete_selected_custom).pack(fill=X, pady=2)

    # ------------- Color page -------------
    def _build_color_page(self, parent):
        parent.columnconfigure(1, weight=1)

        tb.Label(parent, text="Color tools", font=("", 11, "bold")).grid(row=0, column=0, columnspan=2, sticky="w", padx=10, pady=(10, 4))

        self.r_var = tk.IntVar(value=255)
        self.g_var = tk.IntVar(value=120)
        self.b_var = tk.IntVar(value=40)

        for row, (label, var) in enumerate((("R", self.r_var), ("G", self.g_var), ("B", self.b_var)), start=1):
            tb.Label(parent, text=label).grid(row=row, column=0, sticky="e", padx=(10, 6), pady=6)
            s = tb.Scale(parent, from_=0, to=255, orient=HORIZONTAL,
                         command=lambda _v, v=var: self._rgb_changed(v))
            s.configure(value=var.get())
            s.grid(row=row, column=1, sticky="ew", padx=(0, 10), pady=6)
            var.trace_add("write", lambda *_a, slider=s, v=var: slider.configure(value=v.get()))

        row = 4
        tb.Label(parent, text="HEX").grid(row=row, column=0, sticky="e", padx=(10, 6))
        self.hex_var = tk.StringVar(value="#FF7830")
        tb.Entry(parent, textvariable=self.hex_var).grid(row=row, column=1, sticky="ew", padx=(0, 10), pady=6)

        tb.Button(parent, text="Pick…", bootstyle=(PRIMARY, OUTLINE), command=self._pick_color)\
            .grid(row=5, column=0, sticky="ew", padx=10, pady=(0, 6))
        tb.Button(parent, text="Apply now", bootstyle=PRIMARY, command=lambda: self._apply_rgb_now(fade_ms=None))\
            .grid(row=5, column=1, sticky="w", pady=(0, 6))
        tb.Button(parent, text="Fade 1.2s", bootstyle=INFO, command=lambda: self._apply_rgb_now(fade_ms=1200))\
            .grid(row=5, column=1, sticky="e", padx=(0, 10), pady=(0, 6))

        self.swatch = tb.Frame(parent, width=110, height=110, bootstyle=SECONDARY)
        self.swatch.grid(row=1, column=2, rowspan=4, padx=(10, 0))
        self.swatch.grid_propagate(False)
        self._update_swatch()

    # ------------- Device page -------------
    def _build_device_page(self, parent):
        tb.Label(parent, text="Device info", font=("", 11, "bold")).pack(anchor="w", padx=10, pady=10)
        self.device_info = tk.Text(parent, height=12)
        self.device_info.pack(fill=BOTH, expand=YES, padx=10, pady=(0, 10))
        self._update_device_info()

    # ------------- Discovery / selection -------------
    def _populate_devices(self):
        items = [f"{b.get('moduleName','WiZ Bulb')} @ {b['_ip']} ({b.get('mac','')})" for b in self.bulbs]
        self.device_combo["values"] = items
        if items:
            self.device_combo.current(0)
            self._on_device_change()
        else:
            messagebox.showwarning("WiZ", "No WiZ bulbs found. Check Wi-Fi and press Rescan.")

    def _on_device_change(self, *_):
        idx = self.device_combo.current()
        self.current_ip = self.bulbs[idx]["_ip"] if idx >= 0 else None
        self._log(f"Selected: {self.current_ip}")
        self._update_device_info()

    def _rescan(self):
        self._log("Rescanning…")
        def job():
            bulbs = discover(timeout=3.5)
            self.after(0, lambda: self._apply_rescan(bulbs))
        threading.Thread(target=job, daemon=True).start()

    def _apply_rescan(self, bulbs):
        self.bulbs = bulbs
        self._populate_devices()
        self._log(f"Found {len(bulbs)} device(s).")

    # ------------- Core actions -------------
    def _do(self, fn):
        ip = self.current_ip
        if not ip:
            self._log("No device selected.")
            return
        try:
            fn(ip)
        except Exception as e:
            self._log(f"Command failed: {e}")

    def _get_state(self):
        ip = self.current_ip
        if not ip:
            return
        def job():
            resp = get_state(ip)
            self.after(0, lambda: self._log(json.dumps(resp, indent=2) if resp else "No response"))
        threading.Thread(target=job, daemon=True).start()

    # ------------- RGB handlers -------------
    def _rgb_changed(self, _):
        r, g, b = self.r_var.get(), self.g_var.get(), self.b_var.get()
        self.hex_var.set(f"#{r:02X}{g:02X}{b:02X}")
        self._update_swatch()

    def _pick_color(self):
        rgb, hexv = colorchooser.askcolor(color=self.hex_var.get(), title="Pick color")
        if not hexv:
            return
        self.hex_var.set(hexv.upper())
        r, g, b = [int(round(x)) for x in rgb]
        self.r_var.set(r); self.g_var.set(g); self.b_var.set(b)
        self._update_swatch()

    def _apply_rgb_now(self, fade_ms=None):
        r, g, b = self.r_var.get(), self.g_var.get(), self.b_var.get()
        dim = int(self.bri.get())
        params = {"r": r, "g": g, "b": b, "dimming": dim}
        if fade_ms:
            self._fade_to(params, fade_ms)
        else:
            self._do(lambda ip: pilot(ip, **params))
        self._log(f"RGB -> {r},{g},{b} dim={dim}")

    def _update_swatch(self):
        style = tb.Style()
        name = f"Swatch{self.hex_var.get().replace('#','')}.TFrame"
        style.configure(name, background=self.hex_var.get())
        self.swatch.configure(style=name)

    # ------------- Presets logic -------------
    def _apply_preset(self, params, name="Preset"):
        if "dimming" in params: self.bri.set(int(params["dimming"]))
        if "temp" in params: self.tmp.set(int(params["temp"]))
        if all(k in params for k in ("r", "g", "b")):
            self.r_var.set(int(params["r"]))
            self.g_var.set(int(params["g"]))
            self.b_var.set(int(params["b"]))
            self.hex_var.set(f"#{int(params['r']):02X}{int(params['g']):02X}{int(params['b']):02X}")
            self._update_swatch()
        self._do(lambda ip: pilot(ip, **params))
        self._log(f"Applied preset: {name}")

    def _apply_or_fade(self, params, name, fade_ms=900):
        self._fade_to(params, fade_ms)
        self._log(f"Fading to preset: {name}")

    def _save_current_as_preset(self):
        name = simpledialog.askstring("Save preset", "Preset name:")
        if not name:
            return
        params = {"dimming": int(self.bri.get())}
        params.update({"r": self.r_var.get(), "g": self.g_var.get(), "b": self.b_var.get()})
        self.custom_presets[name] = params
        self._persist_custom_presets()
        self._refresh_preset_list()
        self._log(f"Saved preset: {name}")

    def _apply_selected_custom(self, fade_ms=None):
        name = self._selected_preset_name()
        if not name:
            return
        params = self.custom_presets[name]
        if fade_ms:
            self._fade_to(params, fade_ms)
            self._log(f"Fading to custom preset: {name}")
        else:
            self._apply_preset(params, name)

    def _delete_selected_custom(self):
        name = self._selected_preset_name()
        if not name:
            return
        del self.custom_presets[name]
        self._persist_custom_presets()
        self._refresh_preset_list()
        self._log(f"Deleted preset: {name}")

    def _selected_preset_name(self):
        sel = self.preset_list.curselection()
        if not sel:
            self._log("Select a custom preset first.")
            return None
        return self.preset_list.get(sel[0])

    def _refresh_preset_list(self):
        if not hasattr(self, "preset_list"):
            return
        self.preset_list.delete(0, "end")
        for name in sorted(self.custom_presets.keys()):
            self.preset_list.insert("end", name)

    # ------------- Fade engine -------------
    def _fade_to(self, target_params, duration_ms=1000, steps=20):
        ip = self.current_ip
        if not ip:
            self._log("No device selected.")
            return

        if self._fade_job:
            self.after_cancel(self._fade_job)
            self._fade_job = None

        current = get_state(ip) or {}
        cur_pilot = current.get("result", {})
        start = {
            "dimming": cur_pilot.get("dimming", int(self.bri.get())),
            "temp":    cur_pilot.get("temp",    int(self.tmp.get())),
            "r":       cur_pilot.get("r", self.r_var.get()),
            "g":       cur_pilot.get("g", self.g_var.get()),
            "b":       cur_pilot.get("b", self.b_var.get()),
        }
        end = {
            "dimming": target_params.get("dimming", start["dimming"]),
            "temp":    target_params.get("temp", start["temp"]),
            "r":       target_params.get("r", start["r"]),
            "g":       target_params.get("g", start["g"]),
            "b":       target_params.get("b", start["b"]),
        }

        interval = max(1, duration_ms // steps)
        t0 = time.time()

        def step(i=0):
            alpha = i / steps
            eased = 0.5 - 0.5 * math.cos(math.pi * alpha)  # ease in-out
            cur = {
                "dimming": int(start["dimming"] + (end["dimming"] - start["dimming"]) * eased),
                "temp":    int(start["temp"]    + (end["temp"]    - start["temp"])    * eased),
                "r":       int(start["r"]       + (end["r"]       - start["r"])       * eased),
                "g":       int(start["g"]       + (end["g"]       - start["g"])       * eased),
                "b":       int(start["b"]       + (end["b"]       - start["b"])       * eased),
            }
            pilot(ip, **cur)
            self.bri.set(cur["dimming"])
            self.tmp.set(cur["temp"])
            self.r_var.set(cur["r"]); self.g_var.set(cur["g"]); self.b_var.set(cur["b"])
            self.hex_var.set(f"#{cur['r']:02X}{cur['g']:02X}{cur['b']:02X}")
            self._update_swatch()

            if i < steps:
                self._fade_job = self.after(interval, lambda: step(i + 1))
            else:
                self._fade_job = None
                self._log(f"Fade done in {int((time.time() - t0) * 1000)} ms")

        step(0)

    # ------------- Device info -------------
    def _update_device_info(self):
        if not hasattr(self, "device_info"):
            return
        ip = self.current_ip
        if not ip:
            self.device_info.delete("1.0", "end")
            self.device_info.insert("end", "No device selected.")
            return
        info = get_state(ip)
        self.device_info.delete("1.0", "end")
        self.device_info.insert("end", json.dumps(info, indent=2))

    # ------------- persistence -------------
    def _load_custom_presets(self):
        if not os.path.exists(PRESETS_FILE):
            return {}
        try:
            with open(PRESETS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data
        except Exception:
            return {}

    def _persist_custom_presets(self):
        with open(PRESETS_FILE, "w", encoding="utf-8") as f:
            json.dump(self.custom_presets, f, indent=2)

    # ------------- log -------------
    def _log(self, msg):
        if hasattr(self, "log"):
            self.log.insert("end", msg + "\n")
            self.log.see("end")

def run():
    bulbs = discover(timeout=3.5)
    app = WizApp(bulbs)
    app.mainloop()

if __name__ == "__main__":
    run()