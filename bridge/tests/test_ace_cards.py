"""Unit tests for app.ace_cards — 6 ACE Adaptive Card builders."""
import json
from app.ace_cards import (
    build_daily_scorecard,
    build_action_required_card,
    build_new_referral_card,
    build_stage_advice_card,
    build_stage_change_card,
    build_stale_deals_card,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _card_content(envelope: dict) -> dict:
    """Extract the AdaptiveCard content from the Power Automate envelope."""
    return envelope["attachments"][0]["content"]


def _body_texts(envelope: dict) -> list[str]:
    """Collect all TextBlock text values from the card body."""
    body = _card_content(envelope)["body"]
    return [b["text"] for b in body if b.get("type") == "TextBlock"]


def _action_urls(envelope: dict) -> list[str]:
    card = _card_content(envelope)
    return [a["url"] for a in card.get("actions", [])]


def _action_titles(envelope: dict) -> list[str]:
    card = _card_content(envelope)
    return [a["title"] for a in card.get("actions", [])]


# ── Envelope structure (all 6 cards) ─────────────────────────────────────────

def test_envelope_structure_scorecard():
    env = build_daily_scorecard({})
    assert env["type"] == "message"
    att = env["attachments"][0]
    assert att["contentType"] == "application/vnd.microsoft.card.adaptive"
    assert att["contentUrl"] is None
    card = att["content"]
    assert card["type"] == "AdaptiveCard"
    assert card["version"] == "1.4"
    assert card["msteams"] == {"width": "Full"}
    assert isinstance(card["body"], list)


def test_envelope_structure_action_required():
    env = build_action_required_card({})
    assert env["type"] == "message"
    assert _card_content(env)["msteams"] == {"width": "Full"}


def test_envelope_structure_referral():
    env = build_new_referral_card({})
    assert _card_content(env)["type"] == "AdaptiveCard"


def test_envelope_structure_stage_advice():
    env = build_stage_advice_card({})
    assert _card_content(env)["version"] == "1.4"


def test_envelope_structure_stage_change():
    env = build_stage_change_card({})
    assert _card_content(env)["msteams"] == {"width": "Full"}


def test_envelope_structure_stale_deals():
    env = build_stale_deals_card({})
    assert env["type"] == "message"
    assert _card_content(env)["msteams"] == {"width": "Full"}


# ── 1. Daily scorecard ────────────────────────────────────────────────────────

def test_scorecard_title_includes_date():
    env = build_daily_scorecard({"date": "3 Apr 2026"})
    texts = _body_texts(env)
    assert any("3 Apr 2026" in t for t in texts)


def test_scorecard_no_date_fallback():
    env = build_daily_scorecard({})
    texts = _body_texts(env)
    assert any("ACE DAILY SCORECARD" in t for t in texts)


def test_scorecard_health_good_color():
    env = build_daily_scorecard({"health_score": 8})
    header = _card_content(env)["body"][0]
    assert header["color"] == "good"


def test_scorecard_health_warning_color():
    env = build_daily_scorecard({"health_score": 5})
    header = _card_content(env)["body"][0]
    assert header["color"] == "warning"


def test_scorecard_health_attention_color():
    env = build_daily_scorecard({"health_score": 2})
    header = _card_content(env)["body"][0]
    assert header["color"] == "attention"


def test_scorecard_health_boundary_7_is_good():
    env = build_daily_scorecard({"health_score": 7})
    assert _card_content(env)["body"][0]["color"] == "good"


def test_scorecard_health_boundary_4_is_warning():
    env = build_daily_scorecard({"health_score": 4})
    assert _card_content(env)["body"][0]["color"] == "warning"


def test_scorecard_pipeline_facts_present():
    env = build_daily_scorecard({"total_opps": 12, "action_required": 3, "health_score": 6})
    body = _card_content(env)["body"]
    factsets = [b for b in body if b.get("type") == "FactSet"]
    assert len(factsets) >= 1
    pipeline_fs = factsets[0]
    titles = [f["title"] for f in pipeline_fs["facts"]]
    assert "Open Opportunities" in titles
    assert "Action Required" in titles


def test_scorecard_funding_eligible_appears():
    env = build_daily_scorecard({"funding_eligible": 2})
    body = _card_content(env)["body"]
    all_facts = [f for b in body if b.get("type") == "FactSet" for f in b["facts"]]
    assert any(f["title"] == "Funding Eligible" for f in all_facts)


def test_scorecard_cosell_appears():
    env = build_daily_scorecard({"cosell_active": 4})
    body = _card_content(env)["body"]
    all_facts = [f for b in body if b.get("type") == "FactSet" for f in b["facts"]]
    assert any(f["title"] == "Co-Sell Active" for f in all_facts)


def test_scorecard_by_stage_section():
    env = build_daily_scorecard({
        "by_stage": {"Prospect": 5, "Qualified": 3, "Committed": 1}
    })
    texts = _body_texts(env)
    assert any("BY STAGE" in t for t in texts)
    body = _card_content(env)["body"]
    all_facts = [f for b in body if b.get("type") == "FactSet" for f in b["facts"]]
    assert any(f["title"] == "Prospect" for f in all_facts)


def test_scorecard_no_by_stage_skipped():
    env = build_daily_scorecard({})
    texts = _body_texts(env)
    assert not any("BY STAGE" in t for t in texts)


def test_scorecard_next_steps_numbered():
    env = build_daily_scorecard({"next_steps": ["Call AWS rep", "Submit funding"]})
    texts = _body_texts(env)
    assert any("1. Call AWS rep" in t for t in texts)
    assert any("2. Submit funding" in t for t in texts)


def test_scorecard_next_steps_capped_at_5():
    steps = [f"Step {i}" for i in range(10)]
    env = build_daily_scorecard({"next_steps": steps})
    texts = _body_texts(env)
    assert not any("6. Step 5" in t for t in texts)
    assert any("5. Step 4" in t for t in texts)


def test_scorecard_subtitle_appears():
    env = build_daily_scorecard({"subtitle": "Daily ops briefing"})
    texts = _body_texts(env)
    assert any("Daily ops briefing" in t for t in texts)


def test_scorecard_no_actions():
    env = build_daily_scorecard({})
    assert "actions" not in _card_content(env)


# ── 2. Action required ────────────────────────────────────────────────────────

def test_action_required_header_contains_company():
    env = build_action_required_card({"company": "UK Tote Group"})
    texts = _body_texts(env)
    assert any("UK Tote Group" in t for t in texts)


def test_action_required_opp_id_in_header():
    env = build_action_required_card({"company": "Acme", "opp_id": "O1234567"})
    texts = _body_texts(env)
    assert any("O1234567" in t for t in texts)


def test_action_required_sla_good_color():
    env = build_action_required_card({"days_remaining": 10})
    assert _card_content(env)["body"][0]["color"] == "good"


def test_action_required_sla_warning_color():
    env = build_action_required_card({"days_remaining": 5})
    assert _card_content(env)["body"][0]["color"] == "warning"


def test_action_required_sla_attention_color():
    env = build_action_required_card({"days_remaining": 1})
    assert _card_content(env)["body"][0]["color"] == "attention"


def test_action_required_sla_boundary_7_is_good():
    env = build_action_required_card({"days_remaining": 7})
    assert _card_content(env)["body"][0]["color"] == "warning"  # >7 → good, else warning


def test_action_required_no_days_remaining_is_attention():
    env = build_action_required_card({"company": "Acme"})
    assert _card_content(env)["body"][0]["color"] == "attention"


def test_action_required_issues_list():
    env = build_action_required_card({
        "issues": ["Missing close date", "No ARR entered"]
    })
    texts = _body_texts(env)
    assert any("Missing close date" in t for t in texts)
    assert any("No ARR entered" in t for t in texts)


def test_action_required_issues_capped_at_8():
    env = build_action_required_card({"issues": [f"Issue {i}" for i in range(12)]})
    texts = _body_texts(env)
    issue_texts = [t for t in texts if t.startswith("• Issue")]
    assert len(issue_texts) == 8


def test_action_required_action_text():
    env = build_action_required_card({"action": "Submit funding application"})
    texts = _body_texts(env)
    assert any("Submit funding application" in t for t in texts)


def test_action_required_hubspot_button():
    env = build_action_required_card({"hubspot_deal_id": "999"})
    urls = _action_urls(env)
    assert any("hubspot.com" in u for u in urls)


def test_action_required_ace_button():
    env = build_action_required_card({"opp_id": "O9999"})
    urls = _action_urls(env)
    assert any("partnercentral" in u for u in urls)


def test_action_required_no_buttons_without_ids():
    env = build_action_required_card({})
    assert "actions" not in _card_content(env)


def test_action_required_sla_label_shows_days():
    env = build_action_required_card({"sla_deadline": "10 Apr 2026", "days_remaining": 7})
    body = _card_content(env)["body"]
    all_facts = [f for b in body if b.get("type") == "FactSet" for f in b["facts"]]
    assert any("7d remaining" in f["title"] for f in all_facts)


# ── 3. New AWS referral ───────────────────────────────────────────────────────

def test_referral_header_color_good():
    env = build_new_referral_card({"company": "Acme"})
    assert _card_content(env)["body"][0]["color"] == "good"


def test_referral_company_in_header():
    env = build_new_referral_card({"company": "CloudStack"})
    texts = _body_texts(env)
    assert any("CloudStack" in t for t in texts)


def test_referral_referred_by_appears():
    env = build_new_referral_card({"aws_rep": "James O'Brien"})
    texts = _body_texts(env)
    assert any("James O'Brien" in t for t in texts)


def test_referral_contact_block():
    env = build_new_referral_card({
        "contact": "Sarah Smith",
        "contact_title": "CTO",
        "contact_email": "s@company.com",
        "contact_phone": "+44 7700 900001",
    })
    texts = _body_texts(env)
    assert any("Sarah Smith" in t for t in texts)
    assert any("s@company.com" in t for t in texts)
    assert any("+44 7700 900001" in t for t in texts)


def test_referral_linkedin_in_contact():
    env = build_new_referral_card({
        "contact": "Sarah Smith",
        "linkedin": "https://linkedin.com/in/sarahsmith",
    })
    texts = _body_texts(env)
    assert any("LinkedIn" in t for t in texts)


def test_referral_description_section():
    env = build_new_referral_card({"description": "Wants to migrate VMware estate to AWS"})
    texts = _body_texts(env)
    assert any("Wants to migrate VMware" in t for t in texts)


def test_referral_arr_fact():
    env = build_new_referral_card({"estimated_arr": "£120,000"})
    body = _card_content(env)["body"]
    all_facts = [f for b in body if b.get("type") == "FactSet" for f in b["facts"]]
    assert any(f["title"] == "Estimated ARR" for f in all_facts)


def test_referral_call_button_with_phone():
    env = build_new_referral_card({
        "contact": "Sarah Smith",
        "contact_phone": "+447700900001",
    })
    urls = _action_urls(env)
    assert any(u.startswith("tel:") for u in urls)


def test_referral_hubspot_button():
    env = build_new_referral_card({"hubspot_deal_id": "123", "opp_id": "O456"})
    urls = _action_urls(env)
    assert any("hubspot.com" in u for u in urls)
    assert any("partnercentral" in u for u in urls)


def test_referral_linkedin_action_button():
    env = build_new_referral_card({"linkedin": "https://linkedin.com/in/test"})
    urls = _action_urls(env)
    assert "https://linkedin.com/in/test" in urls


def test_referral_no_contact_skips_contact_section():
    env = build_new_referral_card({})
    texts = _body_texts(env)
    assert not any("CONTACT" in t for t in texts)


# ── 4. Stage advice ───────────────────────────────────────────────────────────

def test_stage_advice_header_accent():
    env = build_stage_advice_card({"company": "Acme"})
    assert _card_content(env)["body"][0]["color"] == "accent"


def test_stage_advice_stage_facts():
    env = build_stage_advice_card({
        "current_stage": "Prospect",
        "next_stage": "Qualified",
    })
    body = _card_content(env)["body"]
    all_facts = [f for b in body if b.get("type") == "FactSet" for f in b["facts"]]
    assert any(f["title"] == "Current Stage" for f in all_facts)
    assert any(f["title"] == "Target Stage" for f in all_facts)


def test_stage_advice_missing_fields_in_attention():
    env = build_stage_advice_card({"missing_fields": ["Close date", "ARR"]})
    texts = _body_texts(env)
    assert any("MISSING FIELDS" in t for t in texts)
    body = _card_content(env)["body"]
    attention_tbs = [
        b for b in body
        if b.get("type") == "TextBlock" and b.get("color") == "attention"
        and "Close date" in b.get("text", "")
    ]
    assert len(attention_tbs) >= 1


def test_stage_advice_required_fields_shown_when_no_missing():
    env = build_stage_advice_card({
        "required_fields": ["Close date", "ARR"],
        "missing_fields": [],
    })
    texts = _body_texts(env)
    assert any("REQUIRED FIELDS" in t for t in texts)


def test_stage_advice_tips_numbered():
    env = build_stage_advice_card({"tips": ["Attach SOW", "Get sign-off"]})
    texts = _body_texts(env)
    assert any("1. Attach SOW" in t for t in texts)
    assert any("2. Get sign-off" in t for t in texts)


def test_stage_advice_tips_capped_at_6():
    env = build_stage_advice_card({"tips": [f"Tip {i}" for i in range(10)]})
    texts = _body_texts(env)
    tip_texts = [t for t in texts if t.startswith(("1.", "2.", "3.", "4.", "5.", "6.", "7."))]
    numbered = [t for t in tip_texts if any(t.startswith(f"{n}.") for n in range(1, 8))]
    # Max 6 tips
    assert not any(t.startswith("7. ") for t in texts)


def test_stage_advice_hubspot_ace_buttons():
    env = build_stage_advice_card({"hubspot_deal_id": "42", "opp_id": "O100"})
    urls = _action_urls(env)
    assert any("hubspot.com" in u for u in urls)
    assert any("partnercentral" in u for u in urls)


def test_stage_advice_no_buttons_without_ids():
    env = build_stage_advice_card({})
    assert "actions" not in _card_content(env)


# ── 5. Stage change ───────────────────────────────────────────────────────────

def test_stage_change_forward_is_good():
    env = build_stage_change_card({"direction": "forward"})
    assert _card_content(env)["body"][0]["color"] == "good"


def test_stage_change_backward_is_attention():
    env = build_stage_change_card({"direction": "backward"})
    assert _card_content(env)["body"][0]["color"] == "attention"


def test_stage_change_unchanged_is_warning():
    env = build_stage_change_card({"direction": "unchanged"})
    assert _card_content(env)["body"][0]["color"] == "warning"


def test_stage_change_default_direction_is_forward():
    env = build_stage_change_card({})
    assert _card_content(env)["body"][0]["color"] == "good"


def test_stage_change_stage_arrow():
    env = build_stage_change_card({"old_stage": "Prospect", "new_stage": "Qualified"})
    texts = _body_texts(env)
    assert any("Prospect → Qualified" in t for t in texts)


def test_stage_change_only_new_stage_when_no_old():
    env = build_stage_change_card({"new_stage": "Committed"})
    texts = _body_texts(env)
    assert any("Committed" in t for t in texts)
    assert not any("→" in t for t in texts)


def test_stage_change_unlocked_with_checkmarks():
    env = build_stage_change_card({"unlocked": ["MAP funding eligible", "Co-sell now active"]})
    texts = _body_texts(env)
    assert any("✓ MAP funding eligible" in t for t in texts)
    assert any("✓ Co-sell now active" in t for t in texts)


def test_stage_change_unlocked_capped_at_5():
    env = build_stage_change_card({"unlocked": [f"Item {i}" for i in range(8)]})
    texts = _body_texts(env)
    unlocked_texts = [t for t in texts if t.startswith("✓ Item")]
    assert len(unlocked_texts) == 5


def test_stage_change_next_action():
    env = build_stage_change_card({"action": "Submit SOW to AWS"})
    texts = _body_texts(env)
    assert any("Submit SOW to AWS" in t for t in texts)


def test_stage_change_arr_fact():
    env = build_stage_change_card({"arr": "£80,000"})
    body = _card_content(env)["body"]
    all_facts = [f for b in body if b.get("type") == "FactSet" for f in b["facts"]]
    assert any(f["title"] == "Est. ARR" for f in all_facts)


def test_stage_change_buttons():
    env = build_stage_change_card({"hubspot_deal_id": "77", "opp_id": "O888"})
    urls = _action_urls(env)
    assert any("hubspot.com" in u for u in urls)
    assert any("partnercentral" in u for u in urls)


# ── 6. Stale deals ───────────────────────────────────────────────────────────

def test_stale_deals_zero_stale_is_good():
    env = build_stale_deals_card({"stale_total": 0})
    assert _card_content(env)["body"][0]["color"] == "good"


def test_stale_deals_zero_healthy_message():
    env = build_stale_deals_card({"stale_total": 0})
    texts = _body_texts(env)
    assert any("No stale deals" in t for t in texts)


def test_stale_deals_zero_no_deals_rendered():
    env = build_stale_deals_card({"stale_total": 0, "top_deals": [{"company": "Acme"}]})
    texts = _body_texts(env)
    assert not any("Acme" in t for t in texts)


def test_stale_deals_low_count_warning():
    env = build_stale_deals_card({"stale_total": 2})
    assert _card_content(env)["body"][0]["color"] == "warning"


def test_stale_deals_high_count_attention():
    env = build_stale_deals_card({"stale_total": 5})
    assert _card_content(env)["body"][0]["color"] == "attention"


def test_stale_deals_boundary_3_is_warning():
    env = build_stale_deals_card({"stale_total": 3})
    assert _card_content(env)["body"][0]["color"] == "warning"


def test_stale_deals_header_includes_count():
    env = build_stale_deals_card({"stale_total": 4, "stale_threshold_days": 30})
    texts = _body_texts(env)
    assert any("4 deals stale" in t for t in texts)


def test_stale_deals_singular_label():
    env = build_stale_deals_card({"stale_total": 1})
    texts = _body_texts(env)
    assert any("1 deal stale" in t for t in texts)


def test_stale_deals_week_ending_in_header():
    env = build_stale_deals_card({"stale_total": 2, "week_ending": "7 Apr 2026"})
    texts = _body_texts(env)
    assert any("7 Apr 2026" in t for t in texts)


def test_stale_deals_company_names_rendered():
    env = build_stale_deals_card({
        "stale_total": 2,
        "top_deals": [
            {"company": "UK Tote Group", "opp_id": "O111", "days_since": 45},
            {"company": "Acme Ltd", "days_since": 32},
        ],
    })
    texts = _body_texts(env)
    assert any("UK Tote Group" in t for t in texts)
    assert any("Acme Ltd" in t for t in texts)


def test_stale_deals_days_since_rendered():
    env = build_stale_deals_card({
        "stale_total": 1,
        "top_deals": [{"company": "Acme", "days_since": 55}],
    })
    texts = _body_texts(env)
    assert any("55 days since update" in t for t in texts)


def test_stale_deals_recommended_action():
    env = build_stale_deals_card({
        "stale_total": 1,
        "top_deals": [{"company": "Acme", "recommended_action": "Call AWS rep today"}],
    })
    texts = _body_texts(env)
    assert any("Call AWS rep today" in t for t in texts)


def test_stale_deals_capped_at_5():
    deals = [{"company": f"Company {i}", "days_since": 40} for i in range(8)]
    env = build_stale_deals_card({"stale_total": 8, "top_deals": deals})
    texts = _body_texts(env)
    company_texts = [t for t in texts if "Company" in t]
    assert len(company_texts) == 5


def test_stale_deals_overall_action():
    env = build_stale_deals_card({
        "stale_total": 1,
        "top_deals": [{"company": "X"}],
        "action": "Run hygiene session this week",
    })
    texts = _body_texts(env)
    assert any("Run hygiene session this week" in t for t in texts)


def test_stale_deals_no_actions_buttons():
    env = build_stale_deals_card({"stale_total": 0})
    assert "actions" not in _card_content(env)


# ── Payload size (<20 KB) ─────────────────────────────────────────────────────

def test_scorecard_under_20kb():
    env = build_daily_scorecard({
        "date": "3 Apr 2026",
        "total_opps": 25,
        "by_stage": {"Prospect": 10, "Qualified": 8, "Committed": 5, "Launched": 2},
        "action_required": 4,
        "funding_eligible": 3,
        "cosell_active": 6,
        "health_score": 7,
        "next_steps": ["Call AWS rep", "Submit MAP application", "Update 3 stale deals"],
    })
    assert len(json.dumps(env)) < 20_480


def test_action_required_under_20kb():
    env = build_action_required_card({
        "company": "UK Tote Group",
        "opp_id": "O1234567",
        "stage": "Qualified",
        "issues": ["Missing close date", "No ARR", "Customer use case blank", "AWS rep not assigned"],
        "sla_deadline": "10 Apr 2026",
        "days_remaining": 7,
        "action": "Complete all missing fields in ACE portal",
        "aws_rep": "James O'Brien",
        "hubspot_deal_id": "999",
    })
    assert len(json.dumps(env)) < 20_480


def test_stale_deals_under_20kb():
    deals = [
        {
            "company": f"Company {i}",
            "opp_id": f"O{1000+i}",
            "stage": "Qualified",
            "last_update": "1 Mar 2026",
            "days_since": 33 + i,
            "recommended_action": "Schedule discovery call with AWS rep",
        }
        for i in range(5)
    ]
    env = build_stale_deals_card({
        "week_ending": "7 Apr 2026",
        "stale_total": 5,
        "stale_threshold_days": 30,
        "top_deals": deals,
        "action": "Run weekly ACE hygiene session",
    })
    assert len(json.dumps(env)) < 20_480
