# FSD SOA Diff Visualizer — Technical Overview

## 1. App Overview

FSD SOA Diff Visualizer is a zero-dependency, single-file HTML application for comparing two to five FSD (Functional Study Design) JSON export files. Its primary use case is auditing changes to a **Schedule of Activities (SOA)** between protocol versions — e.g., protocol import vs. data-entry state, or baseline vs. amendment. It also supports comparison of other protocol sections (objectives/endpoints, inclusion/exclusion criteria, population, design, and statistical considerations).

The core problem it solves is that naïve JSON diffing of these exports is useless: every entity carries an opaque UUID that rotates between exports, and arrays re-index whenever items are inserted or moved. A generic diff produces hundreds of false "changed" rows from UUID churn and index shifts. This tool replaces generic key matching with **content-based semantic matching** — epochs matched by type ID, activities by name within a group-membership set, rules by a composite `type|epochDay` key, footnotes by normalized text, and objectives by normalized content with bigram-fuzzy fallback. All processing is entirely client-side; the Content Security Policy explicitly sets `connect-src 'none'`, so no data leaves the browser.

---

## 2. Major Features

### File Upload
Drop zone and `<input type="file">` accept 2–5 JSON files simultaneously or incrementally. Files are read with the `FileReader` API and parsed with `JSON.parse`. Each file gets a labeled pill (A–E, color-coded) showing its display name, size, and parse status. Duplicate filenames are silently rejected. Files can be removed individually without clearing the session. The comparison buttons stay disabled until at least two files parse successfully.

### SOA Comparison
The primary mode. After parsing, `extractSoaSections` recursively walks `parsed.sections[]`, `section.children[]`, and `section.sections[]` to find all sections containing a `scheduleOfActivities` key. This handles both flat layouts and folder-nested section structures. If any file has more than one SOA section, a per-file `<select>` picker appears so the user can choose which SOA to use per file. Clicking "Compare SOA" runs the six-category diff engine and renders the results table.

### Compare Other Sections
A second comparison mode triggered by a separate button. This mode runs a different diff engine (`runOtherDiff`) over the raw parsed JSON objects rather than just the SOA subtree. It covers: Inclusion Criteria, Exclusion Criteria, Population (name, size, summary text), Objectives & Endpoints (with estimand sub-fields and endpoint matching), Overall Design, and Statistical Considerations. The tab bar switches to the dynamically discovered subset of sections that actually contain content, rather than the fixed SOA tab set.

### Tab Navigation
Results are presented in a tab bar. In SOA mode there are eight fixed tabs: All, Epochs, Activity Groups, Activities, Schedule Rules, Scheduled Days, Intraday, and Footnotes. The "Scheduled Days" tab shows only day-level rows; "Intraday" shows only time slot rows (split from the same underlying `d.days` array by `category === 'Time Slot'`). In Other Sections mode the tab bar is built dynamically from whichever section defs returned non-empty rows. Each tab button shows its row count inline. Clicking a tab re-runs `renderResults()` without re-running the diff.

### Summary Counters
Below the tab bar, three color-coded pills show the count of added, removed, and changed rows for the currently active tab. Group header rows and `'present'` (matched, no change) rows are excluded from counts. The "changed" bucket includes `'changed'`, `'updated'` (rule pairing), and `'moved'` change types.

### Copy Diff
The "Copy diff" button serializes the active tab's rows into a plain-text fixed-width table using `navigator.clipboard.writeText()`, with `document.execCommand('copy')` as a fallback for environments where the Clipboard API is unavailable or blocked. The output includes a header block listing file labels and names, then one row per diff entry, with a 2-second button state change ("Copied!") and a toast notification.

### Download (CSV + PDF)
A dropdown button exposes two export formats. **CSV** generates a `text/csv;charset=utf-8;` Blob and triggers a download via a temporary `<a>` element. Each row is RFC 4180-quoted. The first two lines are a human-readable title (file names + timestamp) and a blank separator before the column header row. The `locationFull` field (used for O&E rows where the location is truncated in the table) is used in the CSV to preserve full content. **PDF** uses `html2canvas` (v1.4.1 from cdnjs) to rasterize the `#diff-content` element at `max(devicePixelRatio, 3)x` scale, then `jsPDF` (v2.5.1 from cdnjs) to wrap the image in a PDF page sized to the canvas dimensions. A temporary title element is inserted before rasterization and removed afterward. A temporary `<style>` tag (`pdf-badge-fix`) forces `display:inline-block` on badge spans to work around an `html2canvas` rendering bug with `inline-flex`.

---

## 3. Implementation Components

### 3.1 File Parsing and SOA Extraction

**`processFiles(fileList)`** — Entry point. Reads files via `FileReader`, parses JSON, stores entries in `state.files[]`. Deduplicates by name. Max 5 files.

**`extractSoaSections(parsed)`** — Recursive section walker.
```js
const walk = (sections) => {
  for (const sec of sections) {
    if (sec.scheduleOfActivities) result.push({ title, soa });
    if (sec.children) walk(sec.children);   // folder sections
    if (sec.sections) walk(sec.sections);   // nested section lists
  }
};
walk(parsed.sections);
```
Key design decision: walk both `children` and `sections` arrays because different FSD export versions use different nesting keys. The SOA section itself is `sec.scheduleOfActivities` — an object containing `epochConfiguration`, `activityGroups`, `scheduledDays`, and `footnotes`.

Titles are HTML-stripped with `stripHtml()` before display. If a file has exactly one SOA section the picker select is rendered but disabled (greyed out) to confirm the selection without requiring interaction.

**Limitation**: Only the first occurrence of each `type|epochDay` rule key is retained per activity (`buildRuleKeyMap` uses first-occurrence-wins). If an activity has duplicate rule keys (which should not happen per schema but can occur in malformed exports), silent data loss occurs.

---

### 3.2 Semantic Key Builders

These functions are the foundation of the matching engine. Each one extracts a stable, human-meaningful key from an entity instead of using its UUID.

| Builder | Key | Rationale |
|---|---|---|
| `buildEpochByTypeIdMap` | `epochType.id` | Semantic type like `"screening"`, `"blinded-treatment"` — stable across exports |
| `buildEpochRefMap` | epoch UUID → display title | Used for resolving `scheduledDay.epochId` references, not for matching |
| `buildByName` | `item.name` | For groups, scheduled days, time slots — name is the authoring-stable identifier |
| `buildRuleKeyMap` | `type|epochDay` or `custom-non-reported|description` | Composite key encoding the schedule rule's business identity |
| `buildFootnoteByTextMap` | whitespace-normalized `fn.text` | Footnote text is the only stable cross-export identifier |
| `buildActivityIdNameMap` | UUID → name | Lookup-only map for resolving `footnote.activityIds` to human names |

`unionKeys(maps)` computes the union of all keys across N maps, driving the N-way diff loop in each diff function.

---

### 3.3 SOA Diff Engine

#### `diffEpochs(soas)` → `{ rows, epochRefMaps }`

Matches epochs by `epochType.id`. For each epoch ID in the union:
- If any file is missing the epoch: emits a single summary row with `changeType: 'added'/'removed'` showing title, type, and duration packed into a string.
- If all files have the epoch: compares `title` (HTML-stripped), `epochType.name`, `duration.days`, and `duration.timeUnit` field-by-field. Only emits rows for fields that differ.

Also returns `epochRefMaps` — per-file `Map<UUID, displayTitle>` built at this stage and threaded through to `diffScheduledDays` for epoch title resolution.

**Limitation**: Does not compare epoch `order` (sequence position). If two epochs swap sequence positions, no diff is emitted.

---

#### `diffActivityGroups(soas)` → `{ rows }`

Matches groups by name. Emits one row per group:
- Groups present in all files: `changeType: 'present'` (shown in the Groups tab as reference rows, filtered from the All tab).
- Groups present in some but not all files: `changeType: 'added'/'removed'` with a badge that reads "Only in Study A" rather than the generic "Added/Removed" badge used elsewhere.

Does not compare any group fields beyond name (e.g., `displayOrder`).

---

#### `diffActivities(soas)` → `{ rows, activityMap }`

The most complex diff function. Uses **set-based group-membership semantics** rather than per-composite-key matching:

1. Build per-file: `actName → Map<groupName, activity>` (flattening nested `children[]` recursively).
2. For each activity name in the union: compute `groupsA`, `groupsB` as sets, then:
   - `groupsA ∩ groupsB` → **matched groups**: check placeholder status (`placeholderName != null || assessmentMeasurementConfigurationId == null`).
   - `groupsA − groupsB` → **A-only**: only removed from those groups.
   - `groupsB − groupsA` → **B-only**: only added in those groups.
3. **Cross-group move** (N=2): if both A-only and B-only are non-empty, emits a single `changeType: 'moved'` row with location `"GroupA › Act → GroupB › Act"`.
4. **Pure add/remove**: if only one side is non-empty.
5. If both empty (activity in matched groups only): no row unless placeholder status changed.

The "also in X" context note is appended to pure-add/remove locations when the activity also exists in matched groups.

Also builds and returns `activityMap: Map<\`${groupName}\x00${actName}\`, { name, groupName, perFile }>` — used by `diffScheduleRules` to scope rule comparisons to their activity+group context.

**Limitation**: Activity matching is name-only within groups. Two unrelated activities that share the same name (e.g., "Other" appearing in multiple groups as a catch-all) will be merged in the set-based view. The placeholder detection heuristic (`assessmentMeasurementConfigurationId == null`) may produce false "changed" rows for activities that are legitimately unconfigured in both files (though only if the null/non-null state actually differs between files).

---

#### `diffScheduleRules(soas, activityMap)`

The diff function most affected by group reorganizations. Architecture overview:

**`compareRuleMaps(mapA, mapB, location)` (N=2 helper)**:
- Iterates rule key union.
- Fully-matched rules: compare `RULE_COMPARE_FIELDS = ['endEpochDay', 'description']`. Note: `cardinality` and `showAllDays` are intentionally suppressed — they change too frequently as authoring defaults and generate more noise than signal.
- Unmatched rules collected into `ua` / `ub` sides, then:
  - Equal counts: 1-to-1 pairing by array order; compare `type`, `epochDay`, `endEpochDay`, `description`. Multiple `epochDay` diff rows consolidated into one comma-separated row.
  - Unequal counts: consolidate by `type`, listing all `epochDay` values per type as `"non-fixed-day — days: 1, 8, 29"`.

**Cross-group move handling** (N=2 only):
- Groups `activityMap` entries by `actName`.
- Classifies per-name entries into `matched` / `aOnly` / `bOnly`.
- For `aOnly.length > 0 && bOnly.length > 0`: merges all A-side rule maps and all B-side rule maps, then calls `compareRuleMaps` on the merged maps.
- If the merged comparison produces zero rows (rules are identical post-move): **suppresses entirely** — the activity's group change is already captured in the Activities diff.
- If the merged comparison produces rows: emits them under `"GroupA → GroupB › ActName"` location to make the context clear.

This prevents the most common source of false-positive noise in this tool: an activity that moves from one group to another while keeping identical rules would previously show as N "Removed" rows + N "Added" rows. Now it produces zero rows.

**N>2 fallback**: No cross-group move detection. Each `activityMap` entry is processed independently.

**Known limitation**: The 1-to-1 rule pairing heuristic for equal-count unmatched rules uses array order, not content similarity. If three rules are unmatched on each side but happen to be reordered, the pairing is wrong and will report spurious field changes. A sort-by-`epochDay` pre-pass before pairing would improve this significantly.

---

#### `diffScheduledDays(soas, epochRefMaps)`

Matches scheduled days by name. For each day:
- Resolves `rep.epochId` → epoch display title using the `epochRefMaps` from `diffEpochs`.
- Location: `"EpochTitle › DayName"`.
- Epoch Day column: `"EpochTitle Day N"` or `"Day N"`.
- Add/remove: summary string packing `epochDay`, `epoch`, `type`.
- Changed: compares `type`, resolved epoch title (via all files' `epochRefMaps`), and `epochDay` number.
- **Time slots**: matched by name within each day's `intraDaySchedule.timeSlots`. Only emits slot rows when at least one slot has a diff. A `isGroupHeader` separator row (`"Location — time slots"`) is injected above slot rows.
- Skipped fields: `ruleIds`, `activityIds`, `derivedCardinality`, intraday `id/scheduledDayId`, `timeSlots[].activityIds` — all UUID-based or derivative fields with no authoring signal.

The "Scheduled Days" tab filters to `category !== 'Time Slot'`; the "Intraday" tab filters to `category === 'Time Slot'` (or `isGroupHeader` with `category === 'Time Slot'`). Both tabs draw from the same `d.days` array.

**Limitation**: Does not compare day ordering within an epoch. Two days that swap position are not detected.

---

#### `diffFootnotes(soas)`

Matches footnotes by whitespace-normalized `fn.text`. For each text key:
- **Add/remove**: resolves `fn.activityIds` to activity names via `buildActivityIdNameMap`; shows names as the value (not the raw footnote text, which could be long).
- **Changed** (same text, same in all files): compares resolved activity name lists (sorted, joined). If the attachment list changed, emits a `'changed'` row.

**Limitation**: A minor edit to a footnote's text (e.g., punctuation fix) produces an "added new + removed old" pair rather than a "changed text" row, because the text is the match key. For the current use case (catching which activities a footnote is attached to) this is acceptable.

---

### 3.4 Other Sections Diff Engine

A separate engine for non-SOA protocol sections, activated by "Compare Other Sections". Operates on the full parsed JSON objects, not on the SOA subtree.

#### Text Section Diffing

**`splitTextSection(html)`** — Parses HTML content into an array of text items. Priority: `<li>` elements first (for criteria lists), then `<p>` elements, then the whole string as one item. Uses regex matching on raw HTML, not a DOM parser.

**`diffTextItems(itemsPerFile, sectionTitle, locationPrefix)`** — Positional diff: compares items at the same array index. Designed for ordered content (criteria numbered 1–N) where position is meaningful.

**`diffSingleTextSection`** / **`diffTopLevelTextSection`** — Wrappers that find a section by `type` field (or top-level key), extract `summaryTextContent`, and call `diffTextItems`. `diffTopLevelTextSectionWhole` compares the full text as a single value rather than paragraph-by-paragraph (used for Statistical Considerations where paragraph boundaries are not semantically stable).

**`diffPopulationSection`** — Extracts `summaryTextContent` paragraphs plus `population.name` and `population.size` as separate field-level rows.

#### `matchItemsNWay(itemsPerFile, getId, getNorm)` — Three-phase N-way matcher

1. **Phase 1 — ID match**: Collects all IDs across files, matches by ID first.
2. **Phase 2 — Exact norm match**: Matches remaining items by normalized content.
3. **Phase 3 — Unmatched**: Any item not matched in phases 1–2 is emitted as a singleton (added or removed).

Used for structured items (objectives, endpoints) where IDs may be present.

#### `matchByNormThenFuzzy(itemsPerFile, getNorm)` — Two-phase matcher with fuzzy fallback

1. **Phase 1 — Exact norm match**: Only matches if the same normalized string appears in ≥2 files (singleton norms go to fuzzy).
2. **Phase 2 — Fuzzy match (N=2 only)**: Greedy by descending `diceSimilarity` score. Threshold: 0.80 (character-level Dice coefficient on bigrams — no external library). Sorted by score descending to assign the best available match first; `usedI`/`usedJ` sets prevent double-assignment.
3. **Remainder**: Any item not matched gets its own added/removed row.

Used for objectives and endpoints where IDs are unreliable across exports.

**`diceSimilarity(a, b)`** — Character bigram Dice coefficient: `2 * |intersection| / (|bigrams(a)| + |bigrams(b)|)`. Efficient Map-based implementation.

#### `diffObjectivesAndEndpoints(parsedFiles)`

The most layered diff in the "other sections" engine:

1. Builds per-file `Map<typeId, { typeName, objectives[] }>`. Each objective carries normalized/raw content, an estimand object (four sub-fields: `treatment`, `analysisPopulation`, `variableOfInterest`, `intercurrentEvents`), and an `endpoints[]` array.
2. **Cross-type move detection** (N=2): scans all type pairs for objectives with matching `norm` content appearing in different types. Emits `'moved'` rows and adds matching keys to `crossTypeSkip` to prevent double-processing.
3. Types are sorted in canonical order: `primary → secondary → exploratory → other`.
4. Within each type, runs `matchByNormThenFuzzy` on objectives.
5. For each matched objective pair: emits the objective row, then estimand sub-field rows (skipped when both sides are empty), then runs `matchByNormThenFuzzy` on endpoints.

---

### 3.5 Rendering Pipeline

#### Row Schema
```js
{
  category: string,       // 'Epoch' | 'Activity Group' | 'Activity' | 'Schedule Rule' |
                          // 'Scheduled Day' | 'Time Slot' | 'Footnote' | 'Objectives & Endpoints' | ...
  location: string,       // Human-readable path, e.g. "Screening › Visit 4"
  locationFull?: string,  // Untruncated location for CSV export (O&E rows only)
  epochDay?: string,      // Resolved label e.g. "Screening Day 14" (Scheduled Days / Rules)
  field: string,          // Field name being compared
  values: any[],          // One entry per loaded file; null = absent in that file
  changeType: string,     // 'added' | 'removed' | 'changed' | 'updated' | 'moved' | 'present'
  isGroupHeader?: boolean // When true, row spans all columns as a section divider
}
```

#### `renderResults()`

Single function that re-renders everything from scratch on every tab switch. No virtual DOM or incremental update.

1. **Tab bar**: Iterates `activeTabs`, calls `countRows(getTabRows(tab.id))` for each badge count, renders as HTML string.
2. **Summary bar**: Calls `countRows(getTabRows(state.activeTab))` for the three colored pills.
3. **Diff table**: Builds HTML string for `<colgroup>` + `<thead>` + `<tbody>` via `Array.map().join('')`. Column widths are percentage-based: narrow fixed columns (8%), location (20%), field (11%), equal-share value columns.

**Value cell coloring logic** (for `'changed'` rows): computes the majority value string across all files. Cells matching the majority get `val-match` (gray); outliers get `val-changed` (amber). This lets a 3-file comparison immediately highlight which file is the odd one out.

**Group header rows**: `isGroupHeader: true` rows span all columns with a light slate background (`#F1F5F9`). Used to visually separate rule blocks per activity and time slot blocks per day.

**Epoch Day column**: Hidden for the tabs in `TABS_HIDE_EPOCH_DAY = new Set(['epochs', 'groups', 'activities', 'footnotes'])` where the concept is not applicable.

**O&E location two-tone rendering**: For `category === 'Objectives & Endpoints'` rows, the location is split on ` › ` and the first segment (type label) is rendered bold/dark, subsequent segments are muted gray.

**Activity disclaimer**: A yellow warning banner is injected at the top of the Activities tab noting that placeholder/sub-item activities with near-identical names may produce false add/remove pairs.

#### `getTabRows(tab)`

Routes active tab and compare mode to the correct row source:
- SOA mode / `'all'`: concatenates all SOA diff arrays, filtering out `'present'` rows from groups.
- SOA mode / `'days'`: `days.filter(r => r.category !== 'Time Slot')`.
- SOA mode / `'intraday'`: `days.filter(r => r.category === 'Time Slot' || (r.isGroupHeader && r.category === 'Time Slot'))`.
- Other mode: `state.otherDiffResult.byTab[tab]`.

#### `countRows(rows)`

Skips `isGroupHeader` and `'present'` rows. Buckets: `added` (`'added'`), `removed` (`'removed'`), `changed` (`'changed' | 'updated' | 'moved'`). Returns `{ added, removed, changed, total }`.

---

### 3.6 Export

#### CSV Export
```
ExportTitle\r\n
\r\n
"Category","Location","Field","Study A","Study B","Change"\r\n
...data rows...
```
Uses `row.locationFull || row.location` to avoid truncated O&E content. Values are rendered via `displayVal()` then RFC 4180-quoted via `csvCell()`. `Blob` → `URL.createObjectURL` → temp `<a>` click → `URL.revokeObjectURL`.

#### PDF Export

Workflow:
1. Insert a temporary title `<div>` before `#diff-content`.
2. Inject a temporary `<style>` (`pdf-badge-fix`) that forces `display:inline-block` on badge spans — `html2canvas` misrenders `inline-flex` elements.
3. Capture `html2canvas(el, { scale: max(devicePixelRatio, 3), useCORS: true, backgroundColor: '#ffffff' })`.
4. Determine orientation (landscape if width > height).
5. `new jsPDF({ unit: 'px', format: [pw, ph] })` → `addImage` → `save()`.
6. Remove injected elements in `finally` block.

**Limitation**: The PDF is a raster image — not text-searchable and degrades on long tables that exceed canvas memory limits in some browsers. For tables with hundreds of rows, the canvas allocation may fail silently.

---

## 4. Known Limitations

### Rule Pairing Heuristic
When unmatched rule counts are equal on both sides, the code pairs them by array order (`ua[0]↔ub[0]`, `ua[1]↔ub[1]`, …). If the rules exist in a different sequence, this produces incorrect pairing and reports spurious field-level changes. Sort-stable pairing by `epochDay` ascending before comparing would fix the most common case.

### Duplicate Rule Keys Silently Dropped
`buildRuleKeyMap` uses first-occurrence-wins. If an activity has two rules with the same `type|epochDay` key (schema violation, but possible in malformed exports), the second is silently lost.

### Activity Name Collisions
Activity matching is name-only within a group. If two semantically distinct activities share the same name (e.g., a generic "Other" placeholder appearing as a real activity in another group), they will be conflated in the set-based diff.

### Placeholder Detection Heuristic
Placeholder activities are detected via `placeholderName != null || assessmentMeasurementConfigurationId == null`. The `== null` check for `assessmentMeasurementConfigurationId` may incorrectly flag newly-authored activities that haven't been linked to an assessment configuration yet. This produces false `'changed'` rows showing `"placeholder" → "configured activity"` or vice versa.

### Fuzzy Matching Threshold
`FUZZY_THRESHOLD = 0.80` is hardcoded with an inline comment suggesting 0.85–0.90 would reduce false pairings. For short objectives (< 20 characters), Dice bigrams are unreliable — two short objectives with overlapping words but different meanings can score above 0.80. The threshold is not user-configurable.

### HTML Parsing in `splitTextSection`
Uses regex on raw HTML strings, not a `DOMParser`. Nested list structures (e.g., `<ol>` inside `<li>`) can produce duplicate item extraction. Complex inline markup within list items may also cause the regex to miss content.

### PDF Rasterization Constraints
The PDF is a single-image screenshot. It is not text-searchable, not reflowable, and can fail on very long tables if the canvas memory limit is hit (typically ~300 MP in Chrome). The `max(devicePixelRatio, 3)` scale factor attempts to ensure legibility on high-DPI screens but compounds memory usage.

### N > 2 Degraded Semantics
Cross-group move detection in `diffScheduleRules` and fuzzy matching in `matchByNormThenFuzzy` are both N=2 only. For three or more files, schedule rules for moved activities produce the full add/remove noise that the cross-group detection was designed to suppress.

### O&E Estimand Sub-fields
The estimand is a flat object with four string fields. If an objective is entirely removed or added, the estimand sub-field rows are not emitted (guarded by `ct !== 'added' && ct !== 'removed'`). This is intentional but means an added objective's estimand content is not visible inline — only the objective text is shown.

### Statistical Considerations Whole-text Diff
`diffTopLevelTextSectionWhole` compares the entire statistical considerations block as a single value. For long sections, this makes the diff essentially useless — the entire text appears as "changed" with no indication of which part changed.

### Epoch Day Column in Schedule Rules
The `epochDay` field is stored in the row schema and shown in the Scheduled Days tab, but Schedule Rule rows do not populate `epochDay` (it's part of the rule key, embedded in the `field` string). This is a minor inconsistency.

---

## 5. Potential Future Improvements

### Highest-Value

**Inline word-level diff for changed cells** — Currently, when a value changes, both sides are shown as full strings. A word-level or character-level diff highlighting within the cell (e.g., `<del>` / `<ins>` spans) would make it much faster to spot small edits in long text values (especially O&E content).

**Sort-stable rule pairing** — Before the 1-to-1 pairing loop, sort both `ua` and `ub` by `epochDay` ascending, then by `type`. This would fix the majority of wrong pairings for real-world data.

**Configurable suppressed fields** — `cardinality` and `showAllDays` are currently hardcoded as suppressed from schedule rule comparisons. Exposing these as user-toggleable checkboxes would let users opt into the noisier-but-complete view when they actually need to audit those fields.

**Searchable PDF** — Replace the `html2canvas` approach with a programmatic jsPDF table render using `pdf.text()` and `pdf.autoTable()` (via `jspdf-autotable`). This produces a text-searchable, reflowable PDF that doesn't hit canvas memory limits.

**Statistical considerations paragraph-level diff** — Replace `diffTopLevelTextSectionWhole` with a `matchByNormThenFuzzy` pass over `splitTextSection` output, the same way other text sections work. The whole-text comparison is only justifiable when the section is guaranteed to be a single paragraph.

### Medium-Value

**Fuzzy threshold control** — A slider or `<select>` (0.70 / 0.80 / 0.90 / Exact only) in the UI, stored in `state`, allowing the user to tune sensitivity for their specific data. Particularly useful for short objectives where 0.80 produces false pairings.

**Cross-group move detection for N > 2** — Extend the `diffScheduleRules` cross-group logic to the N>2 case by computing, for each `actName`, which files have the activity in which groups and emitting a multi-column "moved" summary.

**Collapse/expand group header rows** — Group headers in the Schedule Rules table (one per activity) could be made collapsible. For a study with 50+ activities, the table becomes hard to navigate. Storing expanded state in a `Set` and toggling on click would be straightforward.

**LocalStorage persistence** — Saving `state.diffResult` and file names to `localStorage` would let users refresh without losing results. The `parsed` JSON objects are large (1–2 MB each) but `structuredClone` + `localStorage.setItem` is feasible for the typical file sizes.

**Epoch ordering diff** — Add epoch display order to `diffEpochs`. The current implementation only compares fields within a matched epoch; a structural reordering is invisible.

**Day ordering within epoch** — Similarly, `scheduledDays` are matched by name but their ordering within an epoch is not compared. Adding a `displayOrder` or `epochDay`-based ordering check would catch cases where visit sequences are reshuffled.

### Low-Value / Architectural

**DOMParser for HTML extraction** — Replace the regex-based `splitTextSection` with `new DOMParser().parseFromString(html, 'text/html').querySelectorAll('li, p')` to handle nested lists and complex inline markup correctly. Currently blocked only by habit; `DOMParser` is available in all target browsers and is not affected by the CSP.

**Extract shared rule comparison into a proper class** — The `compareRuleMaps` function is currently a free function called from `diffScheduleRules`. As more rule comparison variants are added (N>2 cross-group, special field handling), a `RuleComparator` class with configurable field lists would be cleaner.

**Test harness** — There are no automated tests. The diffing logic is complex enough (cross-group move detection, rule pairing, fuzzy matching) that a small test suite using synthetic JSON fixtures loaded via `data:` URIs would prevent regressions during future changes.
