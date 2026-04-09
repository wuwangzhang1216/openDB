# OpenDB Logo & Brand Guide

## Logo

The OpenDB logo is a stylized **"D"** inside a rounded square, with a horizontal bar that extends beyond the left edge — representing "open."

### Assets

| File | Description |
|------|-------------|
| `docs/assets/opendb-icon.svg` | Icon mark — black background, white mark |
| `docs/assets/opendb-icon-light.svg` | Icon mark — white background, black mark |
| `docs/assets/opendb-banner.svg` | Full banner — black background, logo + wordmark + tagline |

### Icon Anatomy

```
┌──────────────────┐
│                  │
│    ┌──────┐      │
│    │      │      │
├────┤      │      │   ← horizontal bar breaks through the rounded square
│    │      │      │
│    └──────┘      │
│                  │
└──────────────────┘
```

- **Outer shape**: Rounded square (`rx="17"` on 80×80 viewBox)
- **Inner mark**: A "D" formed by a quadratic Bézier path
- **Slit**: A horizontal rectangle at vertical center, extending past the left edge to `x=0`
- **Concept**: The slit "opens" the container — Open + DB

### Sizes

The icon is designed to work from 16px to any size. Minimum clear space = height of the slit.

## Color Palette

Black and white only. No gradients, no accent colors on the logo itself.

| Role | Hex | Usage |
|------|-----|-------|
| Primary | `#000000` | Icon background, banner background |
| Mark | `#FFFFFF` | Inner "D" shape + slit |
| Wordmark "open" | `#FFFFFF` | Banner text |
| Wordmark "DB" | `#666666` | Banner text (dimmed) |
| Tagline | `#666666` | Banner subtitle |

### Extended palette (UI, not logo)

From the brand kit (`opendb-brandkit.html`):

| Name | Hex | Usage |
|------|-----|-------|
| Slate 900 | `#0F172A` | Dark UI backgrounds |
| Slate 800 | `#1E293B` | Hover / secondary |
| Slate 600 | `#475569` | Body text |
| Slate 100 | `#F1F5F9` | Light backgrounds |
| Indigo | `#6366F1` | Accent / CTA |
| Emerald | `#059669` | Success |
| Cyan | `#0891B2` | Info / links |
| Red | `#EF4444` | Error / danger |

## Typography

| Role | Font | Weight |
|------|------|--------|
| Display / Headings | DM Sans | 700 |
| Body | DM Sans | 400 |
| Code / Mono | JetBrains Mono | 400–500 |

## Misuse

Do not:
- Rotate or skew the logo
- Recolor the inner mark
- Use low opacity or as watermark
- Stretch or distort proportions
- Add shadows or effects
- Add outlines or borders

## Full Brand Kit

See `opendb-brandkit.html` in the repository root for interactive previews and downloadable assets.
