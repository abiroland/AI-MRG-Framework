# AI-MRG Framework

**An open-source, full-lifecycle AI and model risk governance toolkit for U.S. regulated industries, starting with financial services.**

!!! warning "Pre-alpha"
    AI-MRG is in Phase 0. The data models, pack format and CLI exist; inventory, tiering, checks and reporting arrive in Phase 1. Pack content is a **draft that has not been reviewed by practitioners**. Nothing here is legal advice.

## What it does

AI-MRG turns principles-based guidance and legal requirements into **versioned control catalogs, automated checks, evidence trails and document templates** that an institution can run on its own infrastructure, with no license fee, vendor account or telemetry.

It covers two governance tracks:

- **Models** under SR 26-2 (traditional statistical models and non-generative machine learning).
- **AI systems** (generative and agentic AI), which SR 26-2 places outside its scope. These are governed using NIST AI RMF and its Generative AI Profile.

Consumer-protection controls (ECOA/Regulation B adverse action, fair lending, FCRA, Colorado's automated-decision law) attach to either track when an asset influences consumer credit decisions.

## Where to go next

- [Getting started](getting-started.md): install it and validate the built-in pack.
- [Writing packs](packs.md): the control format and the applicability expression language.
- [Architecture](ARCHITECTURE.md), [Regulatory map](REGULATORY_MAP.md) and [Roadmap](ROADMAP.md).
