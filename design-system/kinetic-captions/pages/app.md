# App Page Overrides

> **PROJECT:** Kinetic Captions
> **Generated:** 2026-11-01 21:25:23
> **Page Type:** General

> ⚠️ **IMPORTANT:** Rules in this file **override** the Master file (`design-system/MASTER.md`).
> Only deviations from the Master are documented here. For all other rules, refer to the Master.

---

## Page-Specific Rules

### Layout Overrides

- **Max Width:** 800px (narrow, focused)
- **Layout:** Single column, centered

### Spacing Overrides

- **Content Density:** Low — focus on clarity

### Typography Overrides

- No overrides — use Master typography

### Color Overrides

- No overrides — use Master colors

### Component Overrides

- Avoid: Present AI as human
- Avoid: Static output only
- Avoid: Auto-play high-resolution loops without pause or captions

---

## Page-Specific Components

- No unique components for this page

---

## Recommendations

- Effects: Typing indicators (3-dot pulse), streaming text animations, pulse animations, context cards, smooth reveals
- AI Interaction: Clearly label AI generated content
- AI Interaction: Thumps up/down or 'Regenerate'
- Sustainability: Prefer click-to-play; provide pause and captions; stop off-screen and honor reduced motion

---
## Overrides for the app screen (decided by lead after verifying fit)

The generated "Feature-Rich Showcase" pattern is a landing-page pattern and does NOT fit;
this page is a single-screen tool. Override:

- **Mode:** dark only (video preview is the hero; dark surround keeps it the brightest thing).
- **Tokens (dark zinc + one accent):**
  --color-background #09090B · --color-card #18181B · --color-elevated #27272A ·
  --color-border #3F3F46 · --color-foreground #FAFAFA · --color-muted-foreground #A1A1AA ·
  --color-accent #EC4899 (CTA only, text on it #000) · --color-ring #F472B6 · --color-destructive #F87171 · --color-success #4ADE80
- **Layout:** 2-column on ≥1024px — left: drop zone → result player (sticky); right: settings panel.
  Single column below 1024px (player first). No horizontal scroll at 375px.
- **Flow:** ① drop file → ② auto-detected badge (音声 / 動画) + "作成" CTA → ③ step progress
  (準備 → 文字おこし → 人物検出 → 演出 → 書き出し, "Step 2 / 5" + bar) → ④ player + download + transcript.
- **Progressive disclosure:** defaults work with zero input; "スタイル" cards + フォント always visible,
  everything else (色・サイズ・位置・背景・言語・精度) inside a collapsed "詳細設定".
- **Typography:** Inter (UI), fallback system-ui; caption fonts previewed in their own face.
- **Icons:** inline SVG (Lucide-style, 1.75 stroke). No emoji.
- **Interaction:** 44px min targets, visible focus ring (2px --color-ring, offset 2px),
  150–250ms transitions, prefers-reduced-motion respected, drop zone also a button (click/Enter opens picker).
