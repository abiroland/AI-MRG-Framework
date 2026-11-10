"""AI-MRG dashboard (Streamlit).

Run with ``aimrg dashboard`` (needs ``pip install "ai-mrg[dashboard]"`` and a running
API: ``aimrg serve``). Every action goes through the REST API with your token, so the
same roles and approval rules apply as everywhere else.

Deep links: ``?page=Asset&asset=<name>`` opens a page (and asset) directly.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
import streamlit as st

from aimrg.dashboard import views
from aimrg.dashboard.client import ApiClient, ApiError

PAGES = ("Inventory", "Asset", "Findings", "Monitoring", "Reports")
DISCLAIMER = (
    "AI-MRG structures and evidences governance work. It is not legal advice, and using "
    "it does not by itself establish regulatory compliance."
)


def _client() -> ApiClient | None:
    if "client" in st.session_state:
        client: ApiClient = st.session_state["client"]
        return client
    with st.sidebar:
        st.subheader("Connect")
        url = st.text_input("API URL", os.environ.get("AIMRG_API_URL", "http://127.0.0.1:8000"))
        token = st.text_input("API token", os.environ.get("AIMRG_API_TOKEN", ""), type="password")
        if st.button("Connect", key="connect") and token:
            candidate = ApiClient(url, token)
            try:
                candidate.me()
            except (ApiError, httpx.HTTPError, OSError) as exc:
                st.error(f"Could not connect: {exc}")
                return None
            st.session_state["client"] = candidate
            st.rerun()
    st.info("Enter the API URL and your token in the sidebar. Tokens come from `aimrg user add`.")
    return None


def _call(fn: Any, *args: Any, **kwargs: Any) -> Any:
    """Run an API call, showing errors (including workflow blockers) in the page."""
    try:
        return fn(*args, **kwargs)
    except (ApiError, httpx.HTTPError) as exc:
        st.error(str(exc))
        return None


def page_inventory(client: ApiClient) -> None:
    st.header("Inventory")
    assets = client.assets()
    findings = client.findings()
    metrics = views.portfolio_metrics(assets, findings)
    cols = st.columns(5)
    cols[0].metric("Assets", metrics["assets"])
    cols[1].metric("Tier 1", metrics["tier_1"])
    cols[2].metric("AI systems", metrics["ai_systems"])
    cols[3].metric("In production", metrics["in_production"])
    cols[4].metric("Open findings", metrics["open_findings"])
    if metrics["unclassified"]:
        st.warning(f"{metrics['unclassified']} asset(s) not yet classified.")
    st.dataframe(views.inventory_frame(assets, findings), hide_index=True, width="stretch")


def _asset_picker(client: ApiClient, key: str) -> dict[str, Any] | None:
    assets = client.assets()
    if not assets:
        st.info("No assets registered yet.")
        return None
    names = [a["name"] for a in assets]
    wanted = st.session_state.get("asset") or st.query_params.get("asset")
    default = names.index(wanted) if wanted in names else 0
    name = st.selectbox("Asset", names, index=default, key=key)
    st.session_state["asset"] = name
    return next(a for a in assets if a["name"] == name)


def page_asset(client: ApiClient, me: dict[str, Any]) -> None:
    st.header("Asset")
    asset = _asset_picker(client, "asset_select")
    if asset is None:
        return
    ref = asset["id"]
    classification = asset.get("classification") or {}
    risk = asset.get("risk_assessment") or {}
    tier = (risk.get("override") or {}).get("tier", risk.get("computed_tier"))
    cols = st.columns(3)
    cols[0].metric("Status", asset["status"])
    cols[1].metric("Track", classification.get("track", "—"))
    cols[2].metric("Tier", tier if tier is not None else "—")
    st.caption(
        f"Owner: {asset['owner']} · Technique: {asset['ai_category']}"
        + (f" · Vendor: {asset['vendor']}" if asset.get("vendor") else "")
    )
    if not classification or not risk:
        st.warning("Classify and tier this asset (CLI or API) to see its controls.")
        return

    controls, evidence_tab, findings_tab, lifecycle = st.tabs(
        ["Controls", "Evidence", "Findings", "Lifecycle"]
    )
    plan = client.plan(ref)
    control_ids = [p["control_id"] for p in plan]

    with controls:
        counts = views.coverage_counts(plan)
        c = st.columns(4)
        for col, (label, value) in zip(c, counts.items(), strict=True):
            col.metric(label.capitalize(), value)
        st.dataframe(views.coverage_frame(plan), hide_index=True, width="stretch")

    with evidence_tab:
        evidence = client.evidence(ref)
        st.dataframe(views.evidence_frame(evidence), hide_index=True, width="stretch")
        with st.form("add_evidence"):
            st.write("Record evidence")
            control = st.selectbox("Control", control_ids, key="ev_control")
            kind = st.selectbox("Type", ["document", "attestation"], key="ev_type")
            summary = st.text_input("Summary", key="ev_summary")
            upload = st.file_uploader("Attachment (optional)", key="ev_file")
            if st.form_submit_button("Record") and summary:
                file = (upload.name, upload.getvalue()) if upload else None
                if _call(client.add_evidence, ref, control, kind, summary, file):
                    st.success("Evidence recorded.")

    with findings_tab:
        all_findings = client.findings(ref, open_only=False)
        st.dataframe(
            views.findings_frame(all_findings, {asset["id"]: asset["name"]}),
            hide_index=True,
            width="stretch",
        )
        open_ids = [f["id"] for f in all_findings if f["status"] in ("open", "in_remediation")]
        with st.form("open_finding"):
            st.write("Open a finding")
            title = st.text_input("Title", key="f_title")
            severity = st.selectbox("Severity", ["low", "medium", "high", "critical"], key="f_sev")
            submitted = st.form_submit_button("Open")
            if (
                submitted
                and title
                and _call(client.open_finding, ref, title=title, severity=severity)
            ):
                st.success("Finding opened.")
        if open_ids:
            with st.form("resolve_finding"):
                st.write("Resolve a finding")
                target = st.selectbox(
                    "Finding", open_ids, format_func=lambda i: i[:8], key="f_target"
                )
                ev_ids = [e["id"] for e in client.evidence(ref)]
                remediation = st.multiselect(
                    "Remediation evidence (to close)",
                    ev_ids,
                    format_func=lambda i: i[:12],
                    key="f_evidence",
                )
                close, accept = st.columns(2)
                do_close = close.form_submit_button("Close with evidence")
                do_accept = accept.form_submit_button("Accept risk")
                if do_close and remediation and _call(client.close_finding, target, remediation):
                    st.success("Finding closed.")
                if do_accept and _call(client.accept_finding, target):
                    st.success("Risk accepted.")

    with lifecycle:
        status = client.status(ref)
        if status["history"]:
            st.dataframe(
                [
                    {
                        "When": e["occurred_at"][:16].replace("T", " "),
                        "From": e["from_status"],
                        "To": e["to_status"],
                        "By": e["actor"],
                        "Approver": e.get("approver") or "",
                        "Limits / note": e.get("limits") or e.get("note") or "",
                    }
                    for e in status["history"]
                ],
                hide_index=True,
                width="stretch",
            )
        gates = views.gate_lines(status)
        if not gates:
            st.info("No further transitions.")
            return
        for target, allowed, blockers in gates:
            if allowed:
                st.success(f"✓ {target}")
            else:
                st.error(f"✗ {target}\n\n" + "\n".join(f"- {b}" for b in blockers))
        with st.form("transition"):
            st.write(f"Move this asset (acting as **{me['name']}**)")
            target = st.selectbox("New status", [g[0] for g in gates], key="t_target")
            limits = st.text_input("Limits (approved_with_limits)", key="t_limits")
            note = st.text_input("Note", key="t_note")
            submitted = st.form_submit_button("Submit")
            if submitted and _call(client.transition, ref, target, limits=limits, note=note):
                st.success(f"Moved to {target}.")


def page_findings(client: ApiClient) -> None:
    st.header("Findings")
    names = {a["id"]: a["name"] for a in client.assets()}
    frame = views.aging_frame(client.aging(), names)
    overdue = int((frame["Days overdue"] > 0).sum()) if not frame.empty else 0
    cols = st.columns(2)
    cols[0].metric("Open", len(frame))
    cols[1].metric("Overdue", overdue)
    severities = st.multiselect(
        "Severity", ["critical", "high", "medium", "low"], default=[], key="sev_filter"
    )
    if severities:
        frame = frame[frame["Severity"].isin(severities)]
    st.dataframe(frame, hide_index=True, width="stretch")


def page_monitoring(client: ApiClient) -> None:
    st.header("Monitoring")
    asset = _asset_picker(client, "mon_asset")
    if asset is None:
        return
    results = client.check_results(asset["id"])
    if not results:
        st.info("No automated check results recorded for this asset.")
        return
    checks = views.checks_by_runs(results)
    check_id = st.selectbox("Check", checks, key="mon_check")
    selected = [r for r in results if r["check_id"] == check_id]
    metrics = views.numeric_metrics(selected)
    if metrics:
        metric = st.selectbox("Metric", metrics, key="mon_metric")
        trend = views.trend_frame(selected, metric)
        if len(trend) >= 2:
            st.line_chart(trend, x="Recorded", y=metric)
        else:
            st.metric(metric, f"{trend[metric].iloc[-1]:.4g}")
            st.caption("Only one run so far; the trend appears after the next run.")
    st.dataframe(
        [
            {
                "Recorded": r["recorded_at"][:16].replace("T", " "),
                "Control": r["control_id"],
                "Outcome": r["outcome"],
                "Summary": r["summary"],
            }
            for r in selected
        ],
        hide_index=True,
        width="stretch",
    )


def page_reports(client: ApiClient, me: dict[str, Any]) -> None:
    st.header("Reports")
    templates = client.templates()
    kind = st.radio("Scope", ["Asset", "Portfolio"], horizontal=True, key="rep_scope")
    asset_name = None
    if kind == "Asset":
        asset = _asset_picker(client, "rep_asset")
        if asset is None:
            return
        asset_name = asset["id"]
        options = templates["asset"]
    else:
        options = templates["portfolio"]
    template = st.selectbox("Report", options, key="rep_template")
    content = _call(client.report, template, asset_name, "md")
    if content is not None:
        st.download_button("Download Markdown", content, file_name=f"{template}.md", key="dl_md")
        docx = _call(client.report, template, asset_name, "docx")
        if docx is not None:
            st.download_button("Download Word", docx, file_name=f"{template}.docx", key="dl_docx")
        with st.expander("Preview", expanded=True):
            st.markdown(content.decode("utf-8"))
    if "auditor" in me["roles"] or "admin" in me["roles"]:
        st.subheader("Examination package")
        if st.button("Build package for all assets", key="build_pkg"):
            package = _call(client.export)
            if package:
                data, sums = package
                st.session_state["package"] = (data, sums)
        if "package" in st.session_state:
            data, sums = st.session_state["package"]
            st.write(f"SHA-256 of SHA256SUMS: `{sums}`. Record it independently.")
            st.download_button(
                "Download package", data, file_name="aimrg-examination-package.zip", key="dl_pkg"
            )


def main() -> None:
    st.set_page_config(page_title="AI-MRG", layout="wide")
    client = _client()
    if client is None:
        return
    me = _call(client.me)
    if me is None:
        st.session_state.pop("client", None)
        return
    with st.sidebar:
        st.markdown("**AI-MRG**")
        st.caption(f"Pack `{me['pack']}`")
        st.caption(f"Signed in as {me['name']} ({', '.join(me['roles'])})")
        requested = st.query_params.get("page", PAGES[0])
        page = st.radio(
            "Page", PAGES, index=PAGES.index(requested) if requested in PAGES else 0, key="page"
        )
        if st.button("Disconnect", key="disconnect"):
            st.session_state.pop("client", None)
            st.rerun()
        st.caption(DISCLAIMER)
    if page == "Inventory":
        page_inventory(client)
    elif page == "Asset":
        page_asset(client, me)
    elif page == "Findings":
        page_findings(client)
    elif page == "Monitoring":
        page_monitoring(client)
    else:
        page_reports(client, me)


main()
