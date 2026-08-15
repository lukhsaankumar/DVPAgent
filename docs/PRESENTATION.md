# DVP Meeting Prep — 3-Minute Presentation

*Slide content + speaker notes. ~30 sec/slide. `---` marks a new slide.*

---

## Slide 1 — Intro

**DVP Meeting Prep**
Automated, AI-generated advisor briefing documents.

- Search an advisor → get a ready-to-read meeting prep doc in seconds

**Speaker notes:**
"This is DVP Meeting Prep — a tool that turns scattered advisor data into a single, ready-to-read briefing document before a District VP sits down with an advisor. Let me show you why we built it and how it works."

---

## Slide 2 — Problem

- Advisor data lives in **3+ disconnected systems**: Salesforce, Tableau, consultant scorecards
- Before every meeting, DVPs manually dig through notes, dashboards, and spreadsheets
- Slow, inconsistent, easy to miss important context

**Speaker notes:**
"Today, prepping for one advisor meeting means checking Salesforce activity history, a Tableau export, and a scorecard spreadsheet — separately, by hand. That's real time lost every week, and the quality of prep depends on who's doing it and how thorough they are."

---

## Slide 3 — Workflow

1. **Search** the advisor by name (autocomplete across sources)
2. **Pull** their Salesforce, Tableau, and scorecard data automatically
3. **Generate** a summary with Gemini Enterprise (AI)
4. **Download** a formatted Word document — ready for the meeting

**Speaker notes:**
"The workflow is deliberately simple: type a name, click generate, get a Word doc. Everything else — fetching from three sources, building the right context, writing the summary — happens automatically behind that one click."

---

## Slide 4 — Diagram

```
Salesforce ─┐
Tableau ─────┼─> SQLite (local DB) ─> Gemini Enterprise (AI) ─> Markdown ─> .docx
Scorecard ──┘
```

- One local database, no external DB server or vendor account
- AI runs on enterprise Google credentials — no API keys, no data leaving via a third-party key

**Speaker notes:**
"Architecturally it's a straight pipeline: three data sources feed a local database, the database feeds the AI prompt, and the AI's answer gets turned into a Word doc. Everything runs on infrastructure we already control and trust — enterprise Google credentials, not a personal API key."

---

## Slide 5 — Demo

*(live demo / screenshots)*

- Home page → type "Avery" → advisor dropdown appears
- Select advisor → click **Get meeting prep document**
- `.docx` downloads with a formatted summary
- Upload page → drag in a new Tableau/scorecard file → instant dedup confirmation

**Speaker notes:**
"Let me show it live: I search for an advisor, the dropdown filters as I type, I click generate, and a few seconds later I have a Word doc with everything a DVP needs — recent activity, scorecard metrics, key notes — already written up."

---

## Slide 6 — Impact & Recap

- **Minutes, not an hour** — prep time collapses from ad-hoc digging to one click
- **Consistent** — every advisor gets the same structured format
- **Secure & low-maintenance** — local data, enterprise AI auth, no API keys or hosted DB to manage

**Recap:** scattered data → one search → AI-written brief → downloadable doc.

**Speaker notes:**
"To recap: we had a real time-and-consistency problem across three disconnected systems. Now it's one search and one click. That's the whole pitch — less manual digging, more consistent prep, and infrastructure that's simple to run and secure by default."
