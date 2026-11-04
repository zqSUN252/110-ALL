---
name: MC STAR LOGISTICS
description: Observed blue-brand logistics workspace and public welcome system.
colors:
  blue: "#1a56db"
  nav: "#12409e"
  focus: "#0ea5e9"
  link: "#164fbc"
  canvas: "#f1f5f9"
  surface: "#fff"
  ink: "#0f172a"
  muted: "#475569"
  line: "#cbd5e1"
  divider: "#e2e8f0"
  field-border: "#94a3b8"
  control-hover: "#eaf0fb"
  control-hover-border: "#8ba7d9"
  control-active: "#dce8fc"
  table-heading: "#eaf0f8"
  selected: "#edf4ff"
  receivable-red: "#ffe4e6"
  receivable-yellow: "#fff1b8"
  loading-notified: "#ffe978"
  error-background: "#fff1f2"
  error-ink: "#9f1239"
  error-border: "#fecdd3"
  caution-background: "#fffbeb"
  caution-ink: "#665008"
  caution-border: "#ead9a5"
  public-line: "#526276"
  public-action: "#0284c7"
typography:
  headline:
    fontFamily: 'Ebrima, "Microsoft YaHei", "Segoe UI", sans-serif'
    fontSize: "1.6rem"
    lineHeight: 1.3
  title:
    fontFamily: 'Ebrima, "Microsoft YaHei", "Segoe UI", sans-serif'
    fontSize: "1.25rem"
  section-title:
    fontFamily: 'Ebrima, "Microsoft YaHei", "Segoe UI", sans-serif'
    fontSize: "1.05rem"
  public-headline:
    fontFamily: 'Ebrima, "Microsoft YaHei", "Segoe UI", sans-serif'
    fontSize: "1.9rem"
    lineHeight: 1.4
  body:
    fontFamily: 'Ebrima, "Microsoft YaHei", "Segoe UI", sans-serif'
    lineHeight: 1.65
  label:
    fontFamily: 'Ebrima, "Microsoft YaHei", "Segoe UI", sans-serif'
    fontSize: "0.875rem"
    fontWeight: 600
  table:
    fontFamily: 'Ebrima, "Microsoft YaHei", "Segoe UI", sans-serif'
    fontSize: "0.85rem"
  table-heading:
    fontFamily: 'Ebrima, "Microsoft YaHei", "Segoe UI", sans-serif'
    fontSize: "0.85rem"
    fontWeight: 600
  fact-label:
    fontFamily: 'Ebrima, "Microsoft YaHei", "Segoe UI", sans-serif'
    fontSize: "0.78rem"
  fact-value:
    fontFamily: 'Ebrima, "Microsoft YaHei", "Segoe UI", sans-serif'
    fontSize: "0.9rem"
rounded:
  field: "5px"
  control: "6px"
  panel: "8px"
  public-form: "12px"
  public-card: "13px"
spacing:
  compact: "6px"
  small: "8px"
  action-gap: "9px"
  inline: "12px"
  content-gap: "14px"
  mobile-panel: "16px"
  field-gap: "17px"
  filter-panel: "18px"
  section: "20px"
  detail-panel: "24px"
components:
  button-primary:
    backgroundColor: "{colors.blue}"
    textColor: "{colors.surface}"
    rounded: "{rounded.control}"
    padding: "9px 13px"
  button-primary-hover:
    backgroundColor: "{colors.nav}"
    textColor: "{colors.surface}"
  button-standard:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.control}"
    padding: "9px 13px"
  button-standard-hover:
    backgroundColor: "{colors.control-hover}"
  button-text:
    textColor: "{colors.link}"
    padding: "0"
  field:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.field}"
    padding: "9px 10px"
  navigation-item:
    textColor: "{colors.surface}"
    rounded: "{rounded.control}"
    padding: "9px 12px"
    typography: "{typography.fact-value}"
  navigation-item-active:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.nav}"
  tab-active:
    backgroundColor: "{colors.selected}"
    textColor: "{colors.link}"
    rounded: "{rounded.control}"
    padding: "9px 13px"
  detail-panel:
    backgroundColor: "{colors.surface}"
    rounded: "{rounded.panel}"
    padding: "24px"
  public-service-card:
    backgroundColor: "#ffffff08"
    rounded: "{rounded.public-card}"
    padding: "20px"
  table-heading:
    backgroundColor: "{colors.table-heading}"
    textColor: "{colors.ink}"
    padding: "11px 12px"
    typography: "{typography.table-heading}"
  field-group:
    rounded: "{rounded.control}"
    padding: "15px"
---

# Design System: MC STAR LOGISTICS

## Overview

**Creative North Star: "MC STAR blue-brand operations workspace"**

This is the inherited MC STAR visual identity as implemented in the unified React application: the supplied logo, blue navigation, light operational surfaces and ordinary business forms. The name above describes that incumbent; it is not a newly approved concept. User templates, field labels, field order and function sequence take precedence over decorative changes.

The system supports dense, evidence-oriented work. Lists and their detail forms remain together, broad tables keep their column structure, and small screens gain access through navigation collapse, form reflow and horizontal scrolling. The dark public welcome is a separate expression of the same identity, rather than a replacement palette for internal work.

**Key Characteristics:**
- Original MC STAR logo and established blue identity.
- Light, bordered operational surfaces with explicit field labels.
- Broad tables, tabular numerals and context-preserving detail forms.
- Responsive reflow without deleting required information.

Source authority: `web/src/style.css`, `web/src/ui.tsx`, `web/src/App.tsx`, `PRODUCT.md` and the truthful incumbent-direction record in `docs/development/ui-contract.md`. This scan records the frozen Task 6 build; source/build provenance and verification limits are in `.superpowers/sdd/2026-11-04-unified-implementation/task-6-ui-documentation.md`. It does not certify whole-app or production acceptance.

## Colors

The palette combines established primary blues with white and cool neutral operational surfaces; status colors carry specific business meaning.

### Primary
- **MC STAR blue** (`blue`): primary action fill and input caret; matches the existing `--blue` source token.
- **Navigation blue** (`nav`): persistent sidebar and primary hover; matches `--nav`.
- **Focus cyan** (`focus`): keyboard focus outline, separate from action fill.
- **Link blue** (`link`): links and text actions.

### Neutral
- **Operational canvas** (`canvas`): the light workspace ground.
- **Content white** (`surface`): tables, fields, header and detail containers; matches `--surface`.
- **Business ink** (`ink`) and **secondary slate** (`muted`): main and supporting text. The current `--muted` is darker than the original reference; record the shipped value without reverting it.
- **Structural line** (`line`), **row divider** (`divider`) and **field stroke** (`field-border`): different border roles. The current `--line` is darker than the original reference.
- **Table heading** (`table-heading`) and **selection tint** (`selected`): header separation and selected records.
- **Error**, **caution** and **receivable status** primitives: semantic surfaces, not additional brand accents. Loading-notified yellow remains distinct from receivable yellow.

The public surface also uses its existing dark blue gradient, cyan service icons and translucent card fills. Exact gradient and state declarations live in the sidecar snippets and public surface record; they do not replace internal primary colors.

Sidecar tonal ramps are eight-step OKLCH swatch previews synthesized for the design panel, as required by the document format. They are not extracted runtime scales or approved replacement colors; the frontmatter retains the actual source values.

**The Business Color Rule.** Preserve the difference between receivable red, receivable yellow, selection tint and loading-notified yellow; do not infer a financial or delivery event from color alone.

## Typography

**Body and interface font:** Ebrima, with Microsoft YaHei, Segoe UI and sans-serif fallbacks. No webfont is bundled or loaded. Actual font selection depends on the client; the source does not prove the rendered face on every device.

**Character:** familiar Windows business typography and explicit hierarchy, not an independent display-font identity. The root does not declare a body font size or weight; do not silently add a normative body-size token. Body line height in frontmatter describes paragraph text, not every element.

### Hierarchy
- **Headline:** internal first-level page headings; the smaller viewport overrides them to `1.4rem`.
- **Title / section title:** second- and third-level headings; unspecified heading weights and line heights remain browser defaults.
- **Public headline:** welcome introduction; changes to `1.75rem` at the small breakpoint, with balanced wrapping and a `38ch` maximum width.
- **Body:** inherited interface family with paragraph line height; public introductory text is constrained to `72ch`.
- **Label:** semibold field labels. Required markers remain attached to their labels.
- **Table:** compact table text with tabular numerals; headings are semibold.
- **Fact label / value:** compact supporting labels and slightly larger record values.

**The Original Template Rule.** The Ebrima commitment does not establish missing export font sizes or exact notice/label compositions. Keep those items pending their actual reference assets.

## Layout

Desktop internal layout uses a fixed header (`70px`), fixed sidebar (`224px`) and workspace offset matching the sidebar. Workspace padding is `98px 24px 40px`. Forms use three equal `minmax(0, 1fr)` columns and their observed field gap; filters use four columns. Repeating rows and grouped fields preserve their declaration order and span the full form grid when applicable.

At `max-width: 1000px`, forms, filters and record facts become two columns. At `max-width: 700px`, they become one column; the header becomes `66px`, the sidebar slides off canvas until opened, workspace left margin becomes zero, and workspace padding becomes `88px 14px 25px`. Filter/detail/notice padding reduces to the mobile-panel step. Account identity and language controls remain in the header.

Tables retain their actual widths, sticky headings, configured frozen groups and horizontal overflow. The source default column width is `150px`, adjustable down to `70px`; the selection column is `40px`. On a narrow viewport the frozen group may slide left before pinning. Its final frozen column plus the selection width reserve space so the phone is readable while later columns remain reachable. Resizing changes width only; it does not reorder fields. The bottom scrollbar mirrors horizontal scrolling. Table viewport height is limited to `62vh`, or `58vh` on small screens.

The public welcome uses a centered `1120px` maximum container, a desktop three-column service grid and a small-screen single column. Surface-specific composition, confirmed service order and conditional image placement are recorded in `.impeccable/surfaces/public-welcome.md`; internal form and table behavior is in `.impeccable/surfaces/internal-workspace.md`.

**The Field Order Rule.** Reflow and horizontal scrolling preserve the user's fields and operation sequence; density is not authorization to omit columns or change their meaning.

## Elevation & Depth

The shipped stylesheet contains no box shadows. White surfaces, border contrast, dark navigation and the public gradient separate regions. Sticky table layers and the fixed header use stacking order for visibility, not decorative elevation. No shadow or blur token has been invented.

The small-screen sidebar uses a transform transition (`180ms ease-out`). Reduced-motion preference disables transitions and smooth scrolling. There is no general animation scale in the source.

## Shapes

Fields use the smallest rounded corners; buttons and fieldsets share the next step; internal panels use the panel step. Public forms and service cards have their own larger corner values. Tables retain rectangular grid cells and explicit row/column borders. These are observed roles rather than a mathematically regular radius scale. The root `--radius` is declared, but most current selectors use literal radii; this document does not claim they are all live-bound to it.

## Components

### Buttons

Compact bordered controls with visible labels. Standard buttons are white, primary buttons use brand blue, and text buttons expose linked record actions. Standard padding is in frontmatter, with minimum height `38px` and icon gap `7px`; table actions use `30px` minimum height and smaller padding. Hover changes fill/stroke; active uses the source cascade; disabled controls keep `opacity: 0.5` and a not-allowed cursor. Keyboard focus uses the same cyan outline (`2px`, offset `3px`) as inputs and links.

### Inputs / Fields

White, explicitly labelled controls with the field stroke and a `40px` minimum height. Textareas resize vertically. The component supports ordinary fields, selects, booleans, lookups, nested groups, repeatable rows, string lists and original-file inputs. Lookups combine a search field with a native select; repeatable rows use real fieldsets and legends, with explicit add/remove controls.

The current file-field extension offers an explicit **Add original image from clipboard** action alongside native file selection. Source accepts PNG, JPEG or WebP clipboard image items and uploads them; a multiple-file field appends returned attachment IDs. Uploading disables its controls and errors remain visible. This records implementation, not verified clipboard permissions, real destination paste or OCR quality. It does not imply that copying a generated notice has sent or delivered it.

### Navigation / Tabs

The blue sidebar uses full-width, left-aligned text buttons. Active navigation is white with dark-blue text and bold weight; hover is a translucent light fill. Department headings are functional group labels. Local tabs wrap and use a pale selection tint with blue text and stroke. Language controls and their public active cyan state follow the public surface rather than the internal tab palette. No independent tag/chip component was found in this sampled implementation.

### Cards / Containers

Filters, record details and notices use white bordered panels with different padding according to their role; do not normalize them to a single invented card. Workflows inside details intentionally shed their own border/padding. Public service cards keep their observed translucent fill, larger corners and line icons; their six-card arrangement is a user constraint.

### Operational Table / Detail

The reusable table renders provided columns in order, keeps headings sticky and aligns numeric text with tabular figures. Selection, receivable color and loading-notified state are separate classes. The current cascade lets loading-notified yellow override other row fills; this is a visual precedence observation, not a new financial rule. Details expose facts, source material and form actions in the existing page context. Native fieldsets remain meaningful grouping, not decorative nesting.

## Do's and Don'ts

### Do:
- **Do** preserve the supplied logo, established blues, user labels and field/function order.
- **Do** keep internal controls in Chinese/English and public welcome controls in Chinese/English/French while preserving business values.
- **Do** retain broad tables, configured frozen groups, width-only resizing and access to later columns on small screens.
- **Do** keep source viewing, local errors and pending inputs in the current business context.
- **Do** distinguish user actions, generated/copyable material and actual delivery facts.
- **Do** refresh this scan when the recorded UI sources or build change.

### Don't:
- **Don't** replace missing hero originals, contact data or exact template references with invented substitutes.
- **Don't** interpret a prior screenshot, successful build or the four-fix review verdict as whole-app acceptance.
- **Don't** copy unconfirmed shipping schedules, free-storage terms or qualifications into public copy.
- **Don't** introduce a new brand, promotional display face or decorative shadow system through these documentation tokens.

Not canonized: exact export/template typography and missing promotional assets remain unverified; source defaults, current status-color precedence and current line/muted deviations from the old reference are observations rather than permission to repair or redesign them. The same-reviewer ship verdict closes only its four listed fixes. Real WhatsApp/OCR/delivery/destination paste and production behavior are outside this document's evidence.
