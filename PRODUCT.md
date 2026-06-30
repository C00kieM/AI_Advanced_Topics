# Product

## Register

product

## Users

Local weather-ops users who need to inspect whether DWD forecasts match the real conditions at their own weather stations. They work in a local desktop or browser app, often while data sources may be stale, offline, or only partly configured.

## Product Purpose

This product explains how official DWD forecasts behave at a specific local site. It reads local weather-station data from InfluxDB, stores local CSV history, archives DWD MOSMIX and CDC data, compares forecast-vs-actual pairs, trains local correction models, and presents the results through a transparent German Weather-Ops terminal and API.

Success means the app makes the data state obvious, keeps InfluxDB read-only, runs long sync and training tasks safely in the background, and explains forecast uncertainty and model reliability without requiring an LLM provider.

## Brand Personality

Calm, technical, transparent. The interface should feel like a trustworthy operations console: precise enough for repeated work, clear under degraded data conditions, and restrained rather than decorative.

## Anti-references

Avoid marketing-page polish, generic AI-gradient dashboards, decorative glass panels, fake metrics, hidden data uncertainty, cramped terminal novelty, and any UI that makes live data look healthier than it is. Avoid redesign choices that obscure the German operational language, safe slash-command model, or read-only InfluxDB guarantee.

## Design Principles

1. Surface data trust first: freshness, source reachability, stale sensors, and model confidence must be visible before actions compete for attention.
2. Make operations safe: long-running sync, archive, compare, and train actions should be explicit, reversible in comprehension, and never imply raw shell access.
3. Prefer earned density: users need scan-friendly status, jobs, and model diagnostics, but panels should group meaning rather than decorate.
4. Explain uncertainty plainly: comparisons and model scores should show sample counts, baselines, improvement, and limits.
5. Preserve local control: secrets stay in `.env`, InfluxDB stays read-only, and the app remains useful with local CSV data when network services are unavailable.

## Accessibility & Inclusion

Target WCAG AA contrast, keyboard-accessible controls, visible focus states, touch targets of at least 44px where practical, reduced-motion alternatives, and state messaging that does not depend on color alone.
