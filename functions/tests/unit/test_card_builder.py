"""
Unit tests for shared.card_builder module.

Tests build_tag_selection_card() and build_consumption_card() adaptive card builders.
"""

import pytest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from shared.card_builder import build_tag_selection_card, build_consumption_card
from shared.flow_models import TagChoice, ConsumptionCardLine


# ============== Helpers ==============

def _find_body_element(card, element_type, element_id=None):
    """Find a body element by type and optional id."""
    for el in card["body"]:
        if el.get("type") == element_type:
            if element_id is None or el.get("id") == element_id:
                return el
    return None


def _find_all_body_elements(card, element_type):
    """Find all body elements of a given type."""
    return [el for el in card["body"] if el.get("type") == element_type]


def _make_tag_choices(count):
    """Create a list of TagChoice instances."""
    return [TagChoice(title=f"TAG-{i:03d}", value=f"TAG-{i:03d}") for i in range(1, count + 1)]


def _make_card_line(alloc_id="ALLOC-001", sap_code="10003456",
                    description="Aluminium Tape 50mm", sap_uom="ROL",
                    raw_uom="m", allocated_raw=100.0, default_raw=100.0,
                    allocated_sap=4.0, default_sap=4.0):
    """Create a ConsumptionCardLine with defaults."""
    return ConsumptionCardLine(
        allocation_id=alloc_id,
        sap_code=sap_code,
        nesting_description=description,
        sap_uom=sap_uom,
        raw_uom=raw_uom,
        allocated_raw_qty=allocated_raw,
        default_actual_raw_qty=default_raw,
        allocated_sap_qty=allocated_sap,
        default_actual_sap_qty=default_sap,
    )


# ============== build_tag_selection_card ==============

class TestBuildTagSelectionCard:
    """Tests for build_tag_selection_card."""

    @pytest.mark.unit
    def test_card_schema_and_type(self):
        """Card has correct $schema, type, and version."""
        card = build_tag_selection_card([], 0)
        assert card["$schema"] == "http://adaptivecards.io/schemas/adaptive-card.json"
        assert card["type"] == "AdaptiveCard"
        assert card["version"] == "1.4"

    @pytest.mark.unit
    def test_header_text(self):
        """Body contains header TextBlock with 'Consumption Submission'."""
        card = build_tag_selection_card([], 0)
        header = card["body"][0]
        assert header["type"] == "TextBlock"
        assert "Consumption Submission" in header["text"]
        assert header["weight"] == "Bolder"
        assert header["size"] == "Large"

    @pytest.mark.unit
    def test_subtitle_shows_pending_count(self):
        """Subtitle TextBlock includes the pending_count value."""
        card = build_tag_selection_card([], 7)
        subtitle = card["body"][1]
        assert subtitle["type"] == "TextBlock"
        assert "7" in subtitle["text"]
        assert "pending" in subtitle["text"].lower()
        assert subtitle["isSubtle"] is True

    @pytest.mark.unit
    def test_pending_count_zero(self):
        """Subtitle works with zero pending count."""
        card = build_tag_selection_card([], 0)
        subtitle = card["body"][1]
        assert "0" in subtitle["text"]

    @pytest.mark.unit
    def test_tag_choice_set_properties(self):
        """Tag Input.ChoiceSet has correct id and isRequired."""
        choices = _make_tag_choices(2)
        card = build_tag_selection_card(choices, 2)
        tag_input = _find_body_element(card, "Input.ChoiceSet", "selectedTagId")
        assert tag_input is not None
        assert tag_input["isRequired"] is True
        assert tag_input["label"] == "Tag Sheet"

    @pytest.mark.unit
    def test_tag_choices_mapped_correctly(self):
        """TagChoice title/value pairs are mapped into the choices list."""
        choices = [
            TagChoice(title="TAG-001 - Project Alpha", value="TAG-001"),
            TagChoice(title="TAG-002 - Project Beta", value="TAG-002"),
        ]
        card = build_tag_selection_card(choices, 2)
        tag_input = _find_body_element(card, "Input.ChoiceSet", "selectedTagId")
        assert len(tag_input["choices"]) == 2
        assert tag_input["choices"][0] == {"title": "TAG-001 - Project Alpha", "value": "TAG-001"}
        assert tag_input["choices"][1] == {"title": "TAG-002 - Project Beta", "value": "TAG-002"}

    @pytest.mark.unit
    def test_shift_choice_set(self):
        """Shift Input.ChoiceSet has Morning and Evening options."""
        card = build_tag_selection_card([], 0)
        shift_input = _find_body_element(card, "Input.ChoiceSet", "shift")
        assert shift_input is not None
        assert shift_input["isRequired"] is True
        shift_values = [c["value"] for c in shift_input["choices"]]
        assert "Morning" in shift_values
        assert "Evening" in shift_values
        assert len(shift_input["choices"]) == 2

    @pytest.mark.unit
    def test_submit_action(self):
        """Actions contain a Submit button titled 'Load Materials'."""
        card = build_tag_selection_card([], 0)
        assert len(card["actions"]) == 1
        action = card["actions"][0]
        assert action["type"] == "Action.Submit"
        assert "Load Materials" in action["title"]
        assert action["style"] == "positive"

    @pytest.mark.unit
    def test_style_expanded_when_5_or_fewer_choices(self):
        """Style is 'expanded' when choices count is <= 5."""
        for count in [1, 3, 5]:
            choices = _make_tag_choices(count)
            card = build_tag_selection_card(choices, count)
            tag_input = _find_body_element(card, "Input.ChoiceSet", "selectedTagId")
            assert tag_input["style"] == "expanded", f"Expected expanded for {count} choices"

    @pytest.mark.unit
    def test_style_compact_when_more_than_5_choices(self):
        """Style is 'compact' when choices count is > 5."""
        for count in [6, 10, 20]:
            choices = _make_tag_choices(count)
            card = build_tag_selection_card(choices, count)
            tag_input = _find_body_element(card, "Input.ChoiceSet", "selectedTagId")
            assert tag_input["style"] == "compact", f"Expected compact for {count} choices"

    @pytest.mark.unit
    def test_style_boundary_exactly_5(self):
        """Exactly 5 choices uses 'expanded' style."""
        choices = _make_tag_choices(5)
        card = build_tag_selection_card(choices, 5)
        tag_input = _find_body_element(card, "Input.ChoiceSet", "selectedTagId")
        assert tag_input["style"] == "expanded"

    @pytest.mark.unit
    def test_style_boundary_exactly_6(self):
        """Exactly 6 choices uses 'compact' style."""
        choices = _make_tag_choices(6)
        card = build_tag_selection_card(choices, 6)
        tag_input = _find_body_element(card, "Input.ChoiceSet", "selectedTagId")
        assert tag_input["style"] == "compact"

    @pytest.mark.unit
    def test_empty_choices_list(self):
        """Empty tag choices list produces a valid card with zero choices."""
        card = build_tag_selection_card([], 0)
        tag_input = _find_body_element(card, "Input.ChoiceSet", "selectedTagId")
        assert tag_input["choices"] == []
        # Style for 0 choices: 0 <= 5, so expanded
        assert tag_input["style"] == "expanded"

    @pytest.mark.unit
    def test_body_element_order(self):
        """Body elements appear in expected order: header, subtitle, tag chooser, shift."""
        choices = _make_tag_choices(2)
        card = build_tag_selection_card(choices, 2)
        body = card["body"]
        assert len(body) == 4
        assert body[0]["type"] == "TextBlock"  # header
        assert body[1]["type"] == "TextBlock"  # subtitle
        assert body[2]["type"] == "Input.ChoiceSet"  # tag chooser
        assert body[2]["id"] == "selectedTagId"
        assert body[3]["type"] == "Input.ChoiceSet"  # shift
        assert body[3]["id"] == "shift"

    @pytest.mark.unit
    def test_shift_style_always_expanded(self):
        """Shift ChoiceSet always uses 'expanded' style regardless of tag count."""
        # Even with many tag choices, shift stays expanded
        choices = _make_tag_choices(10)
        card = build_tag_selection_card(choices, 10)
        shift_input = _find_body_element(card, "Input.ChoiceSet", "shift")
        assert shift_input["style"] == "expanded"

    @pytest.mark.unit
    def test_large_pending_count(self):
        """Large pending count renders correctly in subtitle."""
        card = build_tag_selection_card([], 999)
        subtitle = card["body"][1]
        assert "999" in subtitle["text"]


# ============== build_consumption_card ==============

class TestBuildConsumptionCard:
    """Tests for build_consumption_card."""

    @pytest.mark.unit
    def test_card_schema_and_type(self):
        """Card has correct type and version 1.3."""
        card = build_consumption_card("TAG-001", [])
        assert card["$schema"] == "http://adaptivecards.io/schemas/adaptive-card.json"
        assert card["type"] == "AdaptiveCard"
        assert card["version"] == "1.3"

    @pytest.mark.unit
    def test_header_shows_tag_id(self):
        """Header TextBlock shows 'Consumption: {tag_id}'."""
        card = build_consumption_card("TAG-XYZ", [])
        header = card["body"][0]
        assert header["type"] == "TextBlock"
        assert header["text"] == "Consumption: TAG-XYZ"
        assert header["weight"] == "Bolder"
        assert header["size"] == "Large"

    @pytest.mark.unit
    def test_subtitle_shows_material_count(self):
        """Subtitle shows the count of card_lines materials."""
        lines = [_make_card_line(alloc_id=f"A-{i}") for i in range(3)]
        card = build_consumption_card("TAG-001", lines)
        subtitle = card["body"][1]
        assert "3 material(s)" in subtitle["text"]
        assert subtitle["isSubtle"] is True

    @pytest.mark.unit
    def test_subtitle_zero_materials(self):
        """Subtitle shows 0 materials when card_lines is empty."""
        card = build_consumption_card("TAG-001", [])
        subtitle = card["body"][1]
        assert "0 material(s)" in subtitle["text"]

    @pytest.mark.unit
    def test_single_line_produces_three_body_elements(self):
        """Each card_line produces: material name, SAP info, ColumnSet."""
        line = _make_card_line()
        card = build_consumption_card("TAG-001", [line])
        # body: header, subtitle, material_name, sap_info, column_set, remarks
        body = card["body"]
        assert len(body) == 6
        assert body[2]["type"] == "TextBlock"   # material name
        assert body[3]["type"] == "TextBlock"   # sap info
        assert body[4]["type"] == "ColumnSet"   # input columns
        assert body[5]["type"] == "Input.Text"  # remarks

    @pytest.mark.unit
    def test_material_name_bold_and_numbered(self):
        """Material name is bold, numbered starting at 1, with separator."""
        line = _make_card_line(description="Copper Wire 2mm")
        card = build_consumption_card("TAG-001", [line])
        name_el = card["body"][2]
        assert name_el["weight"] == "Bolder"
        assert name_el["text"] == "1. Copper Wire 2mm"
        assert name_el["separator"] is True

    @pytest.mark.unit
    def test_multiple_lines_numbered_sequentially(self):
        """Multiple lines are numbered 1, 2, 3, etc."""
        lines = [
            _make_card_line(alloc_id="A1", description="Material A"),
            _make_card_line(alloc_id="A2", description="Material B"),
            _make_card_line(alloc_id="A3", description="Material C"),
        ]
        card = build_consumption_card("TAG-001", lines)
        # Each line produces 3 body elements after header+subtitle
        assert card["body"][2]["text"] == "1. Material A"
        assert card["body"][5]["text"] == "2. Material B"
        assert card["body"][8]["text"] == "3. Material C"

    @pytest.mark.unit
    def test_sap_info_line_content(self):
        """SAP info line shows sap_code and allocated quantity."""
        line = _make_card_line(sap_code="20005678", allocated_raw=250.5, raw_uom="kg")
        card = build_consumption_card("TAG-001", [line])
        sap_el = card["body"][3]
        assert "SAP: 20005678" in sap_el["text"]
        assert "Allocated:" in sap_el["text"]
        assert "kg" in sap_el["text"]
        assert sap_el["isSubtle"] is True

    @pytest.mark.unit
    def test_allocated_display_formatting(self):
        """Allocated display uses :g format for clean number rendering."""
        # Whole number: 100.0 renders as "100"
        line_whole = _make_card_line(allocated_raw=100.0, raw_uom="m")
        card = build_consumption_card("TAG-001", [line_whole])
        sap_el = card["body"][3]
        assert "100 m" in sap_el["text"]

        # Decimal: 150.5 renders as "150.5"
        line_decimal = _make_card_line(alloc_id="A2", allocated_raw=150.5, raw_uom="m")
        card2 = build_consumption_card("TAG-002", [line_decimal])
        sap_el2 = card2["body"][3]
        assert "150.5 m" in sap_el2["text"]

    @pytest.mark.unit
    def test_input_ids_use_allocation_id(self):
        """Input.Number IDs follow actual_{alloc_id} and accessories_{alloc_id} pattern."""
        line = _make_card_line(alloc_id="ALLOC-XYZ")
        card = build_consumption_card("TAG-001", [line])
        column_set = card["body"][4]
        columns = column_set["columns"]

        # First column: actual qty
        actual_input = columns[0]["items"][0]
        assert actual_input["type"] == "Input.Number"
        assert actual_input["id"] == "actual_ALLOC-XYZ"

        # Second column: accessories qty
        acc_input = columns[1]["items"][0]
        assert acc_input["type"] == "Input.Number"
        assert acc_input["id"] == "accessories_ALLOC-XYZ"

    @pytest.mark.unit
    def test_actual_qty_default_value(self):
        """Actual qty input is pre-filled with default_actual_raw_qty."""
        line = _make_card_line(default_raw=75.0)
        card = build_consumption_card("TAG-001", [line])
        column_set = card["body"][4]
        actual_input = column_set["columns"][0]["items"][0]
        assert actual_input["value"] == 75.0

    @pytest.mark.unit
    def test_accessories_default_zero(self):
        """Accessories input defaults to 0."""
        line = _make_card_line()
        card = build_consumption_card("TAG-001", [line])
        column_set = card["body"][4]
        acc_input = column_set["columns"][1]["items"][0]
        assert acc_input["value"] == 0

    @pytest.mark.unit
    def test_input_min_zero(self):
        """Both numeric inputs have min=0."""
        line = _make_card_line()
        card = build_consumption_card("TAG-001", [line])
        column_set = card["body"][4]
        actual_input = column_set["columns"][0]["items"][0]
        acc_input = column_set["columns"][1]["items"][0]
        assert actual_input["min"] == 0
        assert acc_input["min"] == 0

    @pytest.mark.unit
    def test_input_labels_include_uom(self):
        """Input labels include the raw_uom in parentheses."""
        line = _make_card_line(raw_uom="ft")
        card = build_consumption_card("TAG-001", [line])
        column_set = card["body"][4]
        actual_label = column_set["columns"][0]["items"][0]["label"]
        acc_label = column_set["columns"][1]["items"][0]["label"]
        assert "(ft)" in actual_label
        assert "(ft)" in acc_label

    @pytest.mark.unit
    def test_remarks_field(self):
        """Card has a remarks Input.Text field."""
        card = build_consumption_card("TAG-001", [])
        # Last body element before actions should be remarks
        remarks = card["body"][-1]
        assert remarks["type"] == "Input.Text"
        assert remarks["id"] == "remarks"
        assert remarks["isMultiline"] is True

    @pytest.mark.unit
    def test_submit_action_data(self):
        """Submit action has correct data with action and tag_id."""
        card = build_consumption_card("TAG-999", [])
        assert len(card["actions"]) == 1
        action = card["actions"][0]
        assert action["type"] == "Action.Submit"
        assert action["title"] == "Submit Consumption"
        assert action["style"] == "positive"
        assert action["data"]["action"] == "submit_consumption"
        assert action["data"]["tag_id"] == "TAG-999"

    @pytest.mark.unit
    def test_empty_card_lines(self):
        """Empty card_lines produces header, subtitle, remarks, and submit."""
        card = build_consumption_card("TAG-001", [])
        body = card["body"]
        # header + subtitle + remarks = 3 elements
        assert len(body) == 3
        assert body[0]["type"] == "TextBlock"   # header
        assert body[1]["type"] == "TextBlock"   # subtitle
        assert body[2]["type"] == "Input.Text"  # remarks
        assert card["actions"][0]["data"]["action"] == "submit_consumption"

    @pytest.mark.unit
    def test_multiple_lines_all_have_correct_ids(self):
        """Each line's inputs use the correct allocation_id in their IDs."""
        lines = [
            _make_card_line(alloc_id="A-100"),
            _make_card_line(alloc_id="A-200"),
            _make_card_line(alloc_id="A-300"),
        ]
        card = build_consumption_card("TAG-001", lines)

        # Collect all Input.Number ids from the card
        input_ids = []
        for el in card["body"]:
            if el.get("type") == "ColumnSet":
                for col in el.get("columns", []):
                    for item in col.get("items", []):
                        if item.get("type") == "Input.Number":
                            input_ids.append(item["id"])

        assert "actual_A-100" in input_ids
        assert "accessories_A-100" in input_ids
        assert "actual_A-200" in input_ids
        assert "accessories_A-200" in input_ids
        assert "actual_A-300" in input_ids
        assert "accessories_A-300" in input_ids

    @pytest.mark.unit
    def test_column_set_has_two_stretch_columns(self):
        """ColumnSet for each line has exactly two stretch-width columns."""
        line = _make_card_line()
        card = build_consumption_card("TAG-001", [line])
        column_set = card["body"][4]
        assert column_set["type"] == "ColumnSet"
        columns = column_set["columns"]
        assert len(columns) == 2
        assert columns[0]["width"] == "stretch"
        assert columns[1]["width"] == "stretch"

    @pytest.mark.unit
    def test_material_separator_and_spacing(self):
        """Material name elements have separator and Medium spacing."""
        line = _make_card_line()
        card = build_consumption_card("TAG-001", [line])
        name_el = card["body"][2]
        assert name_el["separator"] is True
        assert name_el["spacing"] == "Medium"

    @pytest.mark.unit
    def test_sap_info_spacing_none(self):
        """SAP info line has spacing 'None' to tuck under the material name."""
        line = _make_card_line()
        card = build_consumption_card("TAG-001", [line])
        sap_el = card["body"][3]
        assert sap_el["spacing"] == "None"

    @pytest.mark.unit
    def test_card_is_dict(self):
        """Both functions return plain dicts (JSON-serializable)."""
        card = build_consumption_card("TAG-001", [_make_card_line()])
        assert isinstance(card, dict)
        # Verify it can be serialized
        import json
        json_str = json.dumps(card)
        assert isinstance(json_str, str)

    @pytest.mark.unit
    def test_tag_id_with_special_characters(self):
        """Tag IDs with special characters render in header and action data."""
        tag = "TAG-001/Rev-2"
        card = build_consumption_card(tag, [])
        assert card["body"][0]["text"] == f"Consumption: {tag}"
        assert card["actions"][0]["data"]["tag_id"] == tag
