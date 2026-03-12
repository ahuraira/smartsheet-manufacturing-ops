"""
Adaptive Card Builder
=====================

Builds ready-to-post Microsoft Teams adaptive card JSON payloads server-side.

Design: Keep Power Automate as a dumb orchestrator — it just calls the API
and posts the returned card. All complexity lives here.

Used by:
- fn_pending_items    → tag_selection_card
- fn_allocations_aggregate → consumption_card
"""

from typing import List, Dict, Any
from .flow_models import TagChoice, ConsumptionCardLine


def build_tag_selection_card(
    tag_choices: List[TagChoice],
    pending_count: int,
) -> Dict[str, Any]:
    """
    Build an adaptive card for selecting a tag sheet.

    The card contains:
    - A dropdown of available tag sheets
    - A shift selector
    - A submit button

    Power Automate just posts this JSON as-is to Teams.

    Args:
        tag_choices: List of TagChoice (title/value pairs)
        pending_count: Number of pending allocations for display

    Returns:
        Complete adaptive card JSON dict, ready to post
    """
    choices = [{"title": tc.title, "value": tc.value} for tc in tag_choices]

    return {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.4",
        "body": [
            {
                "type": "TextBlock",
                "text": "🏭 Consumption Submission",
                "weight": "Bolder",
                "size": "Large",
                "wrap": True,
            },
            {
                "type": "TextBlock",
                "text": f"{pending_count} pending allocation(s) found. Select a tag sheet and shift to load materials.",
                "wrap": True,
                "isSubtle": True,
            },
            {
                "type": "Input.ChoiceSet",
                "id": "selectedTagId",
                "label": "Tag Sheet",
                "isRequired": True,
                "style": "expanded" if len(choices) <= 5 else "compact",
                "choices": choices,
            },
            {
                "type": "Input.ChoiceSet",
                "id": "shift",
                "label": "Shift",
                "isRequired": True,
                "style": "expanded",
                "choices": [
                    {"title": "Morning", "value": "Morning"},
                    {"title": "Evening", "value": "Evening"},
                ],
            },
        ],
        "actions": [
            {
                "type": "Action.Submit",
                "title": "📋 Load Materials",
                "style": "positive",
            }
        ],
    }


def build_consumption_card(
    tag_id: str,
    card_lines: List[ConsumptionCardLine],
) -> Dict[str, Any]:
    """
    Build an adaptive card for consumption submission.

    Layout per material (flat items, no Container):
      ─── separator ───
      1. Aluminium Tape 50mm
      SAP: 10003456 · Allocated: 100 m
      [Actual Qty (m): 100]   [Accessories (m): 0]

    Args:
        tag_id: Tag Sheet ID
        card_lines: Pre-built ConsumptionCardLine objects

    Returns:
        Complete adaptive card JSON dict, ready to post
    """
    body: List[Dict[str, Any]] = [
        {
            "type": "TextBlock",
            "text": f"Consumption: {tag_id}",
            "weight": "Bolder",
            "size": "Large",
            "wrap": True,
        },
        {
            "type": "TextBlock",
            "text": f"{len(card_lines)} material(s). Edit quantities only if actual differs.",
            "wrap": True,
            "isSubtle": True,
            "spacing": "Small",
        },
    ]

    for i, line in enumerate(card_lines):
        alloc_id = line.allocation_id
        alloc_display = f"{line.allocated_raw_qty:g} {line.raw_uom}"

        # Material name — full width, bold, with separator
        body.append({
            "type": "TextBlock",
            "text": f"{i + 1}. {line.nesting_description}",
            "weight": "Bolder",
            "size": "Medium",
            "wrap": True,
            "separator": True,
            "spacing": "Medium",
        })

        # SAP code + allocated qty
        body.append({
            "type": "TextBlock",
            "text": f"SAP: {line.sap_code}  |  Allocated: {alloc_display}",
            "size": "Small",
            "isSubtle": True,
            "wrap": True,
            "spacing": "None",
        })

        # Two input fields side by side
        body.append({
            "type": "ColumnSet",
            "spacing": "Small",
            "columns": [
                {
                    "type": "Column",
                    "width": "stretch",
                    "items": [
                        {
                            "type": "Input.Number",
                            "id": f"actual_{alloc_id}",
                            "label": f"Actual Qty ({line.raw_uom})",
                            "value": line.default_actual_raw_qty,
                            "min": 0,
                        },
                    ],
                },
                {
                    "type": "Column",
                    "width": "stretch",
                    "items": [
                        {
                            "type": "Input.Number",
                            "id": f"accessories_{alloc_id}",
                            "label": f"Accessories ({line.raw_uom})",
                            "value": 0,
                            "min": 0,
                        },
                    ],
                },
            ],
        })

    # Remarks
    body.append({
        "type": "Input.Text",
        "id": "remarks",
        "label": "Remarks (optional)",
        "isMultiline": True,
        "placeholder": "Any notes...",
        "spacing": "Medium",
    })

    return {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.3",
        "body": body,
        "actions": [
            {
                "type": "Action.Submit",
                "title": "Submit Consumption",
                "style": "positive",
                "data": {
                    "action": "submit_consumption",
                    "tag_id": tag_id,
                },
            }
        ],
    }
