# MoneyPilot visual identity — cockpit gauge (2026-06-12)

**Motif.** A dark rounded-square tile carrying a neon-teal arc gauge (270°
sweep, gap at the bottom, fine tick marks) with an amber needle reading
"healthy" at the upper right, and the ₪ glyph (Consolas Bold — the app's own
mono) at its heart. The same stroke-based glyph appears as an inline SVG in
the header, the onboarding card, and the dimmed empty states.

**Palette.** Lifted verbatim from `app/ui/app.css` `:root` — bg `#0d1117`,
panel `#141b26`, accent teal `#4ef0c0`, amber `#ffb46b`, line `#26334a`.

**Asset inventory** (`app/ui/assets/`): `icon.ico` (16/24/32/48/64/128/256;
≤24px = thick arc + hub needle, 32–48 adds ticks, ≥64 adds the ₪ heart),
`icon-256.png`, `favicon-32.png`.

**Regenerate:** `.venv\Scripts\python.exe scripts\make_assets.py`
(dev tool; `pip install pillow` first — deliberately not in requirements.txt).

**Known limitation.** pywebview on Windows cannot set the window/taskbar
icon — it shows pythonw's. The desktop shortcut (.lnk IconLocation), the
favicon, and the in-app branding carry the identity instead.
