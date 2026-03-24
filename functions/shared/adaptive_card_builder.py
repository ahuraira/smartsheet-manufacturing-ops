from typing import Dict, Any, List, Optional

def build_margin_approval_card(
    tag_sheet_id: str,
    costing_metrics: Dict[str, Any],
    pending_tags_for_lpo: List[Dict[str, str]],
    approval_row_id: str
) -> Dict[str, Any]:
    """
    Builds a v1.4 Microsoft Teams Adaptive Card for the Production Manager.
    Allows applying an area penalty % and merging Tag Sheets into one DO.
    """
    
    # 1. Formatting Display Values
    gm_pct_display = f"{costing_metrics['gm_pct'] * 100:.2f}%"
    target_margin_display = f"{costing_metrics['target_margin_pct'] * 100:.2f}%"
    variation_pct_display = f"{costing_metrics['area_variation_pct'] * 100:.2f}%"
    
    suggested_penalty = costing_metrics.get("suggested_manager_penalty_pct", 0.0)

    # 2. Build ChoiceSet for merging tags
    choices = []
    for tag in pending_tags_for_lpo:
        # Exclude the current tag as it is implicitly selected
        if str(tag["id"]) != str(tag_sheet_id):
            choices.append({
                "title": f"Tag {tag['id']} ({tag['delivered_sqm']} SQM)",
                "value": str(tag["id"])
            })

    # Optional UI block if no other tags exist
    merge_section = []
    if choices:
        merge_section = [
            {
                "type": "TextBlock",
                "text": "Merge with other Pending Tags for this LPO?",
                "weight": "Bolder",
                "wrap": True,
                "spacing": "Medium"
            },
            {
                "type": "Input.ChoiceSet",
                "id": "merge_tags",
                "style": "compact",
                "isMultiSelect": True,
                "placeholder": "Select other Output Tags...",
                "choices": choices
            }
        ]
    else:
        merge_section = [
            {
                "type": "TextBlock",
                "text": "No other pending Tag Sheets for this LPO currently available to merge.",
                "isSubtle": True,
                "wrap": True,
                "spacing": "Medium"
            }
        ]

    # 3. Assemble the Card JSON
    card_json = {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.4",
        "body": [
            {
                "type": "TextBlock",
                "text": f"📋 Margin Review & DO Generation: **Tag {tag_sheet_id}**",
                "weight": "Bolder",
                "size": "Large",
                "wrap": True
            },
            {
                "type": "FactSet",
                "facts": [
                    { "title": "Delivered Area:", "value": f"{costing_metrics['delivered_sqm']} sqm" },
                    { "title": "Selling Price (LPO):", "value": f"{costing_metrics['selling_price_per_sqm']} AED/sqm" },
                    { "title": "Total Revenue:", "value": f"{costing_metrics['total_revenue_aed']:,.2f} AED" },
                    { "title": "Material Cost:", "value": f"{costing_metrics['material_cost_aed']:,.2f} AED" },
                    { "title": "Other Costs (incl Risk):", "value": f"{costing_metrics['fixed_cost_aed'] + costing_metrics['credit_risk_aed']:,.2f} AED" },
                    { "title": "Corporate Tax (9% approx):", "value": f"{costing_metrics['corp_tax_aed']:,.2f} AED" },
                    { "title": "Gross Margin (Pre-Tax):", "value": gm_pct_display },
                    { "title": "Required Margin Variation:", "value": f"Need {variation_pct_display} area bump to hit {target_margin_display} target" }
                ],
                "spacing": "Medium",
                "separator": True
            },
            {
                "type": "TextBlock",
                "text": "Apply Area Variance Penalty % to reach Target Margin",
                "weight": "Bolder",
                "wrap": True,
                "spacing": "Medium"
            },
            {
                "type": "Input.Number",
                "id": "manager_penalty_pct",
                "placeholder": "Enter % (e.g., 5.0 for 5%)",
                "value": suggested_penalty,
                "min": 0,
                "max": 1000
            }
        ],
        "actions": [
            {
                "type": "Action.Submit",
                "title": "Proceed to DO",
                "data": {
                    "action": "proceed_to_do",
                    "approval_row_id": str(approval_row_id),
                    "tag_sheet_id": str(tag_sheet_id)
                }
            },
            {
                "type": "Action.Submit",
                "title": "Hold / Wait",
                "data": {
                    "action": "hold_tag",
                    "approval_row_id": str(approval_row_id),
                    "tag_sheet_id": str(tag_sheet_id)
                }
            }
        ]
    }
    
    # Inject merge section
    card_json["body"].extend(merge_section)
    
    return card_json


def build_do_creation_card(
    delivery_id: str,
    lpo_reference: str,
    tags: List[str],
    total_billed_area: float,
    penalty_pct: float,
    margin_summary: Dict[str, Any],
    approval_row_id: str,
    production_lines: List[Dict[str, Any]] = None,
    accessory_lines: List[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Builds a DO creation notification Adaptive Card for Supervisor / Teams channel.

    Contains:
    - DO summary (LPO, tags, area, margin)
    - Consumption totals (production qty, accessory qty, combined total)
    - Consumption breakdown by material (production + accessories separate)
    - Instructions to create DO in SAP and submit SAP DO# + DO PDF via form
    """
    gm_pct = margin_summary.get("adjusted_gm_pct", 0.0)
    total_revenue = margin_summary.get("adjusted_revenue_aed", 0.0)
    total_cost = margin_summary.get("total_cost_aed", 0.0)
    production_lines = production_lines or []
    accessory_lines = accessory_lines or []

    # Calculate totals
    total_production_qty = sum(l.get("quantity", 0.0) for l in production_lines)
    total_accessory_qty = sum(l.get("quantity", 0.0) for l in accessory_lines)
    total_consumption_qty = total_production_qty + total_accessory_qty

    # ── 1. Header + Summary Facts ────────────────────────────────────────────
    card_body: List[Dict[str, Any]] = [
        {
            "type": "TextBlock",
            "text": f"Delivery Order Approved: **{delivery_id}**",
            "weight": "Bolder",
            "size": "Large",
            "wrap": True,
        },
        {
            "type": "TextBlock",
            "text": "Margin approved by Production Manager. Please create the Delivery Order in SAP, then submit the SAP DO# and upload the DO PDF.",
            "wrap": True,
            "spacing": "Medium",
        },
        {
            "type": "FactSet",
            "facts": [
                {"title": "Delivery ID:", "value": delivery_id},
                {"title": "LPO Reference:", "value": lpo_reference},
                {"title": "Tag Sheet(s):", "value": ", ".join(tags)},
                {"title": "PM Penalty:", "value": f"{penalty_pct}%"},
                {"title": "Total Billed Area:", "value": f"{total_billed_area:,.2f} sqm"},
                {"title": "Total Revenue:", "value": f"{total_revenue:,.2f} AED"},
                {"title": "Total Cost:", "value": f"{total_cost:,.2f} AED"},
                {"title": "Adjusted Gross Margin:", "value": f"{gm_pct * 100:.2f}%"},
            ],
            "spacing": "Medium",
            "separator": True,
        },
    ]

    # ── 2. Consumption Summary Totals ─────────────────────────────────────────
    card_body.append({
        "type": "FactSet",
        "facts": [
            {"title": "Total Consumption:", "value": f"{total_consumption_qty:,.2f}"},
            {"title": "Production Qty:", "value": f"{total_production_qty:,.2f} ({len(production_lines)} records)"},
            {"title": "Accessories Qty:", "value": f"{total_accessory_qty:,.2f} ({len(accessory_lines)} records)"},
        ],
        "spacing": "Medium",
        "separator": True,
    })

    # ── 3. Consumption Breakdown by Material ──────────────────────────────────
    # Aggregate production and accessory by material
    all_lines = production_lines + accessory_lines
    material_totals: Dict[str, Dict[str, Any]] = {}
    for line in all_lines:
        mat = line.get("material_code", "Unknown")
        if mat not in material_totals:
            material_totals[mat] = {"production": 0.0, "accessory": 0.0, "uom": line.get("uom", "")}
        qty = line.get("quantity", 0.0)
        if line in accessory_lines:
            material_totals[mat]["accessory"] += qty
        else:
            material_totals[mat]["production"] += qty

    if material_totals:
        card_body.append({
            "type": "TextBlock",
            "text": f"Consumption by Material ({len(material_totals)} materials)",
            "weight": "Bolder",
            "spacing": "Medium",
            "separator": True,
            "wrap": True,
        })
        card_body.append({
            "type": "ColumnSet",
            "columns": [
                {"type": "Column", "width": 5, "items": [
                    {"type": "TextBlock", "text": "Material", "weight": "Bolder", "size": "Small"}
                ]},
                {"type": "Column", "width": 3, "items": [
                    {"type": "TextBlock", "text": "Production", "weight": "Bolder", "size": "Small"}
                ]},
                {"type": "Column", "width": 3, "items": [
                    {"type": "TextBlock", "text": "Accessory", "weight": "Bolder", "size": "Small"}
                ]},
                {"type": "Column", "width": 2, "items": [
                    {"type": "TextBlock", "text": "UOM", "weight": "Bolder", "size": "Small"}
                ]},
            ],
        })
        for mat, info in sorted(material_totals.items()):
            card_body.append({
                "type": "ColumnSet",
                "columns": [
                    {"type": "Column", "width": 5, "items": [
                        {"type": "TextBlock", "text": str(mat)[:30], "wrap": True, "size": "Small"}
                    ]},
                    {"type": "Column", "width": 3, "items": [
                        {"type": "TextBlock", "text": f"{info['production']:,.2f}", "size": "Small"}
                    ]},
                    {"type": "Column", "width": 3, "items": [
                        {"type": "TextBlock", "text": f"{info['accessory']:,.2f}", "size": "Small"}
                    ]},
                    {"type": "Column", "width": 2, "items": [
                        {"type": "TextBlock", "text": info["uom"], "size": "Small"}
                    ]},
                ],
            })

    # ── 4. Action Instructions ───────────────────────────────────────────────
    card_body.append({
        "type": "TextBlock",
        "text": "Next Steps",
        "weight": "Bolder",
        "spacing": "Large",
        "separator": True,
    })
    card_body.append({
        "type": "TextBlock",
        "text": (
            "1. Create the Delivery Order in SAP using the details above\n"
            "2. Click **Submit SAP DO & PDF** below to open the submission form\n"
            "3. Enter the SAP DO number and upload the DO PDF"
        ),
        "wrap": True,
        "spacing": "Small",
    })

    card_json = {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.4",
        "body": card_body,
        "actions": [
            {
                "type": "Action.Submit",
                "title": "Submit SAP DO & PDF",
                "data": {
                    "action": "open_do_submission_form",
                    "delivery_id": delivery_id,
                    "lpo_reference": lpo_reference,
                    "approval_row_id": approval_row_id,
                },
            },
        ],
    }

    return card_json


def build_sap_conflict_card(
    sap_reference: str,
    customer_name: str,
    project_name: str,
    brand: str,
    conflicts: Dict[str, Dict[str, Any]],
    trace_id: str,
    lpo_details: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Builds an adaptive card for PM to resolve SAP material conflicts.

    Each conflict shows a canonical code with multiple possible SAP codes.
    PM selects which SAP code to use for this LPO, creating overrides.

    Args:
        conflicts: Dict of canonical_code -> {
            "entries": List[CatalogEntry],
            "sap_description": str,
            "default_sap_code": Optional[str],
        }
        lpo_details: Optional dict with customer_lpo_ref, po_quantity_sqm,
            po_value, price_per_sqm, wastage_pct, planned_gm_pct
    """
    conflict_items = list(conflicts.items())
    capped = conflict_items[:20]
    has_more = len(conflict_items) > 20
    lpo = lpo_details or {}

    # -- 1. Header + LPO Details -----------------------------------------------
    facts = [
        {"title": "LPO Reference:", "value": sap_reference},
        {"title": "Customer:", "value": customer_name},
        {"title": "Project:", "value": project_name},
        {"title": "Brand:", "value": brand},
    ]
    if lpo.get("customer_lpo_ref"):
        facts.append({"title": "Customer Ref:", "value": str(lpo["customer_lpo_ref"])})
    if lpo.get("po_quantity_sqm"):
        facts.append({"title": "PO Quantity:", "value": f"{lpo['po_quantity_sqm']:,.2f} sqm"})
    if lpo.get("po_value"):
        facts.append({"title": "PO Value:", "value": f"{lpo['po_value']:,.2f} AED"})
    if lpo.get("price_per_sqm"):
        facts.append({"title": "Price/sqm:", "value": f"{lpo['price_per_sqm']:,.2f} AED"})
    if lpo.get("wastage_pct") is not None:
        facts.append({"title": "Wastage:", "value": f"{lpo['wastage_pct']}%"})
    if lpo.get("planned_gm_pct") is not None:
        facts.append({"title": "Planned GM:", "value": f"{lpo['planned_gm_pct']}%"})
    facts.append({"title": "Conflicts:", "value": f"{len(conflicts)} materials"})

    card_body: List[Dict[str, Any]] = [
        {
            "type": "TextBlock",
            "text": "SAP Material Conflict Resolution",
            "weight": "Bolder",
            "size": "Large",
            "wrap": True,
        },
        {
            "type": "TextBlock",
            "text": f"The following materials have multiple SAP codes in the catalog. Please select the correct SAP code for LPO **{sap_reference}**.",
            "wrap": True,
            "spacing": "Medium",
        },
        {
            "type": "FactSet",
            "facts": facts,
            "spacing": "Medium",
            "separator": True,
        },
    ]

    # -- 2. Per-conflict selection ----------------------------------------------
    for canonical_code, info in capped:
        entries = info["entries"]
        sap_desc = info.get("sap_description", canonical_code)
        default_sap = info.get("default_sap_code")

        card_body.append({
            "type": "TextBlock",
            "text": f"**{canonical_code}** — {sap_desc}",
            "wrap": True,
            "spacing": "Medium",
            "separator": True,
        })

        # Build choices from catalog entries
        choices = []
        default_value = None
        for entry in entries:
            label = f"{entry.sap_code} — {entry.nesting_description}" if entry.nesting_description else entry.sap_code
            choices.append({"title": label, "value": entry.sap_code})
            if default_sap and entry.sap_code == default_sap:
                default_value = entry.sap_code

        choice_set: Dict[str, Any] = {
            "type": "Input.ChoiceSet",
            "id": f"conflict_{canonical_code}",
            "choices": choices,
            "placeholder": "Select SAP code",
            "style": "compact",
        }
        if default_value:
            choice_set["value"] = default_value

        card_body.append(choice_set)

    if has_more:
        card_body.append({
            "type": "TextBlock",
            "text": f"... and {len(conflict_items) - 20} more conflicts. Defaults will be used for uncapped items.",
            "isSubtle": True,
            "wrap": True,
            "size": "Small",
            "spacing": "Medium",
        })

    # -- 3. Actions -------------------------------------------------------------
    card_json = {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.4",
        "body": card_body,
        "actions": [
            {
                "type": "Action.Submit",
                "title": "Approve Overrides",
                "style": "positive",
                "data": {
                    "action": "approve_sap_overrides",
                    "sap_reference": sap_reference,
                    "trace_id": trace_id,
                },
            },
            {
                "type": "Action.Submit",
                "title": "Skip / Use Defaults",
                "data": {
                    "action": "skip_sap_overrides",
                    "sap_reference": sap_reference,
                    "trace_id": trace_id,
                },
            },
        ],
    }

    return card_json
