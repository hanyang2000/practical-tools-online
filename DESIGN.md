# Mobile design system

## Direction

Operational clarity with a quiet, confident visual system. Each surface has its own job accent: collection uses teal, analysis uses indigo, diagnostics uses amber/green status, and image viewing stays neutral dark. Shared neutral surfaces, typography, spacing, and motion keep the product coherent.

## Tokens

### Spacing

All Android dimensions that express layout rhythm use this scale only: 0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 128. Do not add screen-specific values.

### Shape

- Small: 8dp for inputs, buttons, chips, badges, and icon tiles.
- Large: 16dp for cards, dialogs, sheets, hero sections, and large media surfaces.
- No other radius values.

### Type

- Screen title: 24sp, semibold/bold.
- Section title: 18sp, semibold.
- Body: 14sp, regular.
- Supporting text: 12sp, regular.
- Metric value: 24sp, semibold.
- Use sp for every text size and let system font scaling work.

### Surface and color

- App background: soft neutral surface.
- Cards: tonal surface with soft elevation; no dark outline.
- Primary action: one clear filled button per surface.
- Secondary action: tonal or text button; do not make every action a filled rectangle.
- Error: reserved for actionable failure states.
- Status colors must be paired with text, not color alone.

### Motion

- Tap feedback: transform/alpha only, short and reversible.
- Screen transition: fade-through or shared-axis equivalent using native Android animation APIs.
- Lists: restrained stagger only after content is available; never delay data behind decoration.
- Respect system reduced-motion settings and cancel animations when leaving the route.

## Surface rules

Login, home, collection, analysis, diagnostics, settings, and image viewer are native Android surfaces with shared tokens but distinct information architecture. The image viewer owns its back stack entry and must close before the collection screen handles Back. Returning from detail restores scroll, filters, and loaded data.

## Copy rules

Use action labels and useful state. Remove repeated subtitles such as “all functions are native” and obvious instructions such as “click here to view”. Keep concise messages for loading, errors, retry, security, and irreversible actions.
