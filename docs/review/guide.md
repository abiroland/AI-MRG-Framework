# Reviewing a governance pack

AI-MRG's controls, citations and thresholds live in **packs**. Before a pack is marked
*reviewed*, practitioners outside the project check it. This page explains what we ask,
how long it takes, and what we publish.

## Who we're looking for

People who do this work: model validators and model risk managers, compliance and
fair-lending officers, internal auditors, examiners (current or former), and lawyers or
consultants who advise banks and lenders on model risk or consumer protection. You can
review the whole pack or only the part you know best (for example, consumer-protection
controls).

## What you'll be asked to do

1. Get the review workbook: the maintainers will send it, or you can generate it yourself:
   ```bash
   pip install "ai-mrg[review]"
   aimrg pack review-sheet fin-us-banking -o fin-us-banking-review.xlsx
   ```
2. For each control in your scope, answer four questions (Yes / No / Unsure) and rate any
   issue (None / Minor / Major):
   - **Citation accurate?** Does the cited source and section support the control?
   - **Objective reflects the source?** Is the requirement neither stronger nor weaker
     than the source says?
   - **Applies to the right assets?** Is the applicability rule (tier, track, business
     use, jurisdiction) right?
   - **Evidence appropriate?** Would the listed evidence satisfy an examiner or auditor?
3. Use the comments column for corrections, missing controls, better sources, or
   thresholds you would set differently.
4. On the **Sources** sheet, flag any source whose status has changed.
5. Fill in the reviewer block on the **Instructions** sheet and return the workbook.

A full review of `fin-us-banking` (36 controls, 10 sources) typically takes **2–3 hours**;
a focused review of one area takes under an hour.

## What we publish

- A summary of your review (`aimrg pack review-summary`) in `docs/reviews/`, with the
  answers, the issues found and your comments.
- An entry in the pack's `pack.yaml` under `reviews:` with the name, role, affiliation,
  date, scope and conflicts **exactly as you choose**. You may be listed as
  "Anonymous", and affiliation is optional.
- The changes made in response, linked from the review summary.

Before publishing, we send you the summary and the entry for approval.

## What a review is not

- It is **not an endorsement** of AI-MRG, of any institution's use of it, or of any
  vendor. The published entry says so.
- It is not legal advice, and your employer is not a party to it unless you say so.
- You are reviewing published regulatory sources against our summaries. Please don't
  include confidential information from your institution or from examinations.

## When a pack is marked "reviewed"

A pack version is marked `reviewed: true` once at least three reviews covering all of its
controls are published and the issues they raise are resolved or recorded as open.
Any later change to a control's meaning resets that control's review status in the next
version's notes.

## Conflicts of interest

Please declare any relationship that a reader might consider relevant: for example, if
you work for a vendor of model risk software, or have a personal relationship with a
maintainer. Declared conflicts are published with the review.
