# Fonts

TinoHelm QDS Warm uses:

- **Inter** — UI / sans font (`--font-sans`, alias `--font-u`)
  - Features enabled: `cv11` (single-story `a`), `ss01` (open digits), `ss03` (flat-terminal `g`) — gives Inter a "Styrene B" feel.
- **Source Serif 4** — optional serif (`--font-serif`)
- **JetBrains Mono** — data / code font (`--font-mono`, alias `--font-d`)

Chinese fallback stack:
- Sans: HarmonyOS Sans SC → PingFang SC → system UI
- Serif: Source Han Serif SC → Georgia
- Mono: Sarasa Mono SC → ui-monospace

All three are open source (SIL OFL / Apache 2.0). They load from the Google Fonts CDN via the `@import` at the top of `colors_and_type.css`:

```css
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Source+Serif+4:opsz,wght@8..60,400;8..60,500;8..60,600;8..60,700&family=JetBrains+Mono:wght@400;500;600;700&display=swap');
```

## Offline deployment

If you need local TTF files, upload to this directory:

```
Inter-Regular.ttf
Inter-Medium.ttf
Inter-SemiBold.ttf
Inter-Bold.ttf
SourceSerif4-Regular.ttf
SourceSerif4-SemiBold.ttf
JetBrainsMono-Regular.ttf
JetBrainsMono-Medium.ttf
JetBrainsMono-SemiBold.ttf
```

And replace the `@import` with `@font-face` declarations.
