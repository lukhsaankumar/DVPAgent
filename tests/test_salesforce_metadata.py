from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from dvp_meeting_prep.salesforce import metadata as sf_metadata
from dvp_meeting_prep.salesforce.errors import SalesforceMetadataError


def _described(field_names: list[str]) -> dict:
    return {"queryable": True, "fields": [{"name": name, "label": name} for name in field_names]}


def test_select_available_fields_intersects_with_describe():
    described = _described(["Id", "Name", "Advisor_Number__c"])
    selection = sf_metadata.select_available_fields(described, ["Id", "Name", "Nonexistent__c"])
    assert selection.available == ["Id", "Name"]
    assert selection.omitted[0][0] == "Nonexistent__c"


def test_select_available_fields_reports_why_each_field_was_omitted():
    described = _described(["Id"])
    selection = sf_metadata.select_available_fields(described, ["Missing_Field__c"])
    assert selection.omitted[0][1] == "field not found on object"


def test_select_available_fields_suggests_close_match():
    described = _described(["Advisor_Number__c"])
    selection = sf_metadata.select_available_fields(described, ["Advisor_Numbr__c"])
    assert "did you mean 'Advisor_Number__c'" in selection.omitted[0][1]


def test_select_available_fields_dedupes_repeated_preferred_names():
    described = _described(["Id"])
    selection = sf_metadata.select_available_fields(described, ["Id", "Id"])
    assert selection.available == ["Id"]


def test_relationship_projection_valid_when_root_field_present():
    described = _described(["OwnerId"])
    selection = sf_metadata.select_available_fields(described, ["Owner.Name"])
    assert selection.available == ["Owner.Name"]


def test_relationship_projection_invalid_when_root_field_missing():
    described = _described(["Id"])
    selection = sf_metadata.select_available_fields(described, ["Owner.Name"])
    assert selection.omitted[0][0] == "Owner.Name"


def test_relationship_projection_invalid_for_unknown_relationship():
    described = _described(["Some_Custom__c"])
    selection = sf_metadata.select_available_fields(described, ["MadeUpRelationship.Name"])
    assert selection.available == []


def test_describe_object_raises_metadata_error_when_missing():
    from simple_salesforce.exceptions import SalesforceResourceNotFound

    client = MagicMock()
    sf_type = MagicMock()
    sf_type.describe.side_effect = SalesforceResourceNotFound("url", 404, "NotAnObject", b"")
    client.NotAnObject = sf_type

    with pytest.raises(SalesforceMetadataError):
        sf_metadata.describe_object(client, "NotAnObject")


def test_describe_object_raises_when_not_queryable():
    client = MagicMock()
    client.Account.describe.return_value = {"queryable": False, "fields": []}
    with pytest.raises(SalesforceMetadataError):
        sf_metadata.describe_object(client, "Account")


def test_suggest_field_name_returns_none_when_nothing_close():
    assert sf_metadata.suggest_field_name("Completely_Different__c", ["Advisor_Number__c"]) is None
