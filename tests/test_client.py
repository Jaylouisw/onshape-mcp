"""Tests for OnshapeClient — all HTTP layer is mocked."""

import importlib
import os
import sys
import pytest
from unittest.mock import MagicMock, patch

from tests.conftest import MockResponse


# ── Documents ───────────────────────────────────────────────────────

def test_list_documents(mock_client, mock_http, sample_documents):
    mock_http.set_route("GET", "/documents", MockResponse(200, json_data=sample_documents))
    docs = mock_client.list_documents(query="test", limit=10)
    assert len(docs) == 2
    assert docs[0]["id"] == "did_111"
    assert docs[0]["owner"] == "alice"


def test_get_document_info(mock_client, mock_http, sample_document_info, sample_elements):
    # First the document fetch, then the elements fetch
    mock_http.set_route("GET", "/documents/did_111", MockResponse(200, json_data=sample_document_info))
    mock_http.set_route("GET", "/elements", MockResponse(200, json_data=sample_elements))
    info = mock_client.get_document_info("did_111")
    assert info["id"] == "did_111"
    assert info["workspace"]["id"] == "wid_aaa"
    assert len(info["elements"]) == 2
    assert info["elements"][0]["id"] == "eid_ps1"


def test_resolve_document_by_id_bypasses_list_documents(mock_client, mock_http):
    """_resolve_document_by_id must fetch /documents/{did} directly.

    onpy's get_document() resolves via list_documents(), which only returns the
    most recent 20 documents — so documents older than the top-20 can't be
    resolved by ID even though the ID is valid. This method fetches the document
    through the direct REST endpoint instead and wraps it as an onpy Document.
    """
    try:
        import onpy  # noqa: F401
    except ImportError:
        pytest.skip("onpy not installed")

    did = "330e877d8489b677df8a35d1"
    doc_payload = {
        "id": did,
        "name": "multi axis wire grantry",
        "owner": {"name": "michael justesen", "id": "user_1", "href": "https://cad.onshape.com/api/v6/users/user_1"},
        "createdBy": {"name": "michael justesen", "id": "user_1", "href": "https://cad.onshape.com/api/v6/users/user_1"},
        "createdAt": "2025-11-17T18:34:27.088+00:00",
        "href": f"https://cad.onshape.com/api/v6/documents/{did}",
        "defaultWorkspace": {"id": "wid_aaa", "name": "Main"},
    }
    mock_http.set_route("GET", f"/documents/{did}", MockResponse(200, json_data=doc_payload))

    client_mock = MagicMock()
    client_mock.id = did

    with patch("onpy.document.Document") as FakeDocument:
        resolved = mock_client._resolve_document_by_id(client_mock, did)

    doc_gets = [c for c in mock_http.calls if c[0] == "GET" and "/documents" in c[1]]
    assert len(doc_gets) == 1
    assert f"/documents/{did}" in doc_gets[0][1]

    FakeDocument.assert_called_once()
    model = FakeDocument.call_args.args[1]
    assert model.id == did
    assert model.name == "multi axis wire grantry"
    assert model.defaultWorkspace.id == "wid_aaa"


# ── Features ────────────────────────────────────────────────────────

def test_list_features(mock_client, mock_http, sample_features):
    mock_http.set_route("GET", "/features", MockResponse(200, json_data=sample_features))
    feats = mock_client.list_features("did", "wid", "eid")
    assert len(feats) == 2
    assert feats[0]["featureId"] == "FID_sketch1"
    assert feats[0]["name"] == "Sketch 1"
    assert feats[1]["featureType"] == "extrude"


# ── Parts ───────────────────────────────────────────────────────────

def test_list_parts(mock_client, mock_http, sample_parts):
    mock_http.set_route("GET", "/parts/d/", MockResponse(200, json_data=sample_parts))
    parts = mock_client.list_parts("did", "wid", "eid")
    assert len(parts) == 2
    assert parts[0]["partId"] == "PID_1"
    assert parts[0]["material"] == "Steel"
    assert parts[0]["mass"] == 0.123
    assert parts[1]["material"] is None


# ── Sketch (onpy-backed) ────────────────────────────────────────────

def test_create_sketch_calls_onpy(mock_client):
    """create_sketch should delegate to onpy. Skip cleanly if onpy is unavailable."""
    try:
        import onpy  # noqa: F401
    except ImportError:
        pytest.skip("onpy not installed")

    fake_sketch = MagicMock()
    fake_sketch.id = "FID_sketch_new"

    fake_doc = MagicMock()
    fake_el = MagicMock()
    fake_el.id = "eid"
    fake_doc.elements = [fake_el]

    fake_client_cls = MagicMock()
    # v4: create_sketch resolves the doc via _resolve_document_by_id (direct REST),
    # not onpy's get_document() (which only sees the most recent 20 documents).
    fake_client = MagicMock()

    fake_partstudio_cls = MagicMock()
    fake_sketch_cls = MagicMock(return_value=fake_sketch)
    fake_plane_cls = MagicMock()
    fake_orient = MagicMock()
    fake_orient.TOP = "TOP"
    fake_orient.FRONT = "FRONT"
    fake_orient.RIGHT = "RIGHT"
    fake_offset_cls = MagicMock()

    with patch.object(
        mock_client, "_get_onpy_client",
        return_value=(
            fake_client_cls, fake_partstudio_cls, fake_plane_cls,
            fake_orient, fake_offset_cls, fake_sketch_cls,
        ),
    ), patch.object(mock_client, "_resolve_document_by_id", return_value=fake_doc):
        result = mock_client.create_sketch(
            "did", "wid", "eid", name="MySketch", plane="TOP",
        )
    assert result["sketch_id"] == "FID_sketch_new"
    assert result["plane"] == "TOP"
    expected_onpy_kwargs = {
        "units": "metric",
        "onshape_access_token": mock_client.access_key,
        "onshape_secret_token": mock_client.secret_key,
    }
    fake_client_cls.assert_called_once_with(**expected_onpy_kwargs)
    fake_sketch_cls.assert_called_once()


def test_add_rectangle_draws_four_connected_lines(mock_client):
    """onpy has no add_rectangle, so the adapter must use four lines."""
    sketch = MagicMock()
    mock_client._sketch_cache[("did", "eid", "sketch")] = (sketch, MagicMock())

    result = mock_client.add_rectangle(
        "did", "wid", "eid", "sketch",
        corner1_x=0.0,
        corner1_y=0.0,
        corner2_x=0.04,
        corner2_y=0.02,
    )

    assert result["added"] == "rectangle"
    assert sketch.add_line.call_args_list == [
        (((0.0, 0.0), (0.04, 0.0)),),
        (((0.04, 0.0), (0.04, 0.02)),),
        (((0.04, 0.02), (0.0, 0.02)),),
        (((0.0, 0.02), (0.0, 0.0)),),
    ]


# ── Revolve (FeatureScript) ─────────────────────────────────────────

def test_revolve_feature_script(mock_client, mock_http):
    mock_http.set_route(
        "POST", "/featurescript",
        MockResponse(200, json_data={"result": {"value": "ok"}}),
    )
    result = mock_client.revolve(
        "did", "wid", "eid", "sketch_id",
        angle_deg=180,
    )
    assert result["operation"] == "NEW"
    assert result["angle_deg"] == 180
    # Verify the FS script was actually sent
    calls = [c for c in mock_http.calls if c[0] == "POST" and "featurescript" in c[1]]
    assert len(calls) == 1
    script = calls[0][3]["script"]
    assert "revolve(" in script
    assert "sketch_id" in script


# ── STL export ──────────────────────────────────────────────────────

def test_export_stl(mock_client, mock_http, tmp_path):
    binary = b"solid binary stl data" + b"\x00" * 100
    mock_http.set_route(
        "GET", "/stl",
        MockResponse(200, content=binary, json_data={}),
    )
    out = tmp_path / "out.stl"
    result = mock_client.export_stl("did", "wid", "eid", str(out))
    assert out.exists()
    assert out.read_bytes() == binary
    assert result["size_bytes"] == len(binary)


# ── Rate-limit / 429 retry ──────────────────────────────────────────

def test_rate_limit_backoff(mock_client, mock_http):
    """First 429 then 200 → request should retry and succeed."""
    mock_http.set_route(
        "GET", "/documents",
        [
            MockResponse(429, json_data={}, headers={"Retry-After": "0"}),
            MockResponse(200, json_data={"items": []}),
        ],
    )
    docs = mock_client.list_documents()
    assert docs == []
    # Two GETs were made
    gets = [c for c in mock_http.calls if c[0] == "GET"]
    assert len(gets) == 2


# ── New tools: get_regen_errors / validate_featurescript / build_component ──

def test_get_regen_errors_parses_states(mock_client, mock_http):
    "featureStates must be read from GET /features and non-OK features surfaced."
    payload = {
        "sourceMicroversion": "abc123",
        "features": [
            {"featureId": "F1", "name": "Shell", "message": {"featureId": "F1", "name": "Shell"}},
            {"featureId": "F2", "name": "Bad Cut", "message": {"featureId": "F2", "name": "Bad Cut"}},
        ],
        "featureStates": {
            "Origin": {"btType": "BTFeatureState-1688", "featureStatus": "OK", "inactive": False},
            "F1": {"btType": "BTFeatureState-1688", "featureStatus": "OK", "inactive": False},
            "F2": {"btType": "BTFeatureState-1688", "featureStatus": "ERROR", "inactive": False},
        },
    }
    mock_http.set_route("GET", "/features", MockResponse(200, json_data=payload))
    result = mock_client.get_regen_errors("did", "wid", "eid")
    assert result["sourceMicroversion"] == "abc123"
    assert result["featureStates"]["F1"]["status"] == "OK"
    assert result["featureStates"]["F2"]["status"] == "ERROR"
    # Only F2 is a problem; Origin and F1 are OK
    assert len(result["problems"]) == 1
    assert result["problems"][0]["featureId"] == "F2"
    assert result["problems"][0]["name"] == "Bad Cut"


def test_validate_featurescript_flags_broken_code(mock_client, mock_http):
    """validate_featurescript must return valid=False when notices contain errors."""
    payload = {
        "result": None,
        "notices": [
            {
                "btType": "BTNotice-227",
                "type": "PARSE",
                "expressionErrorInfo": {"errorMessageIdentifier": "PARAMETER_EXPRESSION_UNKNOWN_FUNCTION"},
            },
        ],
    }
    mock_http.set_route("POST", "/featurescript", MockResponse(200, json_data=payload))
    result = mock_client.validate_featurescript("did", "wid", "eid", "function(context is Context, id) {}")
    assert result["valid"] is False
    assert result["error_count"] >= 1
    assert any("PARSE" in e or "UNKNOWN_FUNCTION" in e for e in result["errors"])


def test_validate_featurescript_ok_when_no_errors(mock_client, mock_http):
    payload = {"result": {"value": "ok"}, "notices": []}
    mock_http.set_route("POST", "/featurescript", MockResponse(200, json_data=payload))
    result = mock_client.validate_featurescript("did", "wid", "eid", "function(context is Context, id) {}")
    assert result["valid"] is True
    assert result["error_count"] == 0


def test_build_component_validates_before_build(mock_client, mock_http):
    """build_component must refuse to build a broken script."""
    payload = {
        "result": None,
        "notices": [
            {"btType": "BTNotice-227", "type": "EXECUTION",
             "expressionErrorInfo": {"errorMessageIdentifier": "SOME_ERROR"}},
        ],
    }
    mock_http.set_route("POST", "/featurescript", MockResponse(200, json_data=payload))
    result = mock_client.build_component("did", "wid", "eid", "function(context is Context, id) { bad() }")
    assert result["valid"] is False
    assert result["created_bodies"] == []
    assert len(result["errors"]) >= 1


# ── Cache behavior ──────────────────────────────────────────────────

def test_cache_hit(mock_client, mock_http, sample_documents):
    mock_http.set_route("GET", "/documents", MockResponse(200, json_data=sample_documents))
    mock_client.list_documents(limit=20)
    mock_client.list_documents(limit=20)
    # Only one underlying HTTP call thanks to the cache
    gets = [c for c in mock_http.calls if c[0] == "GET"]
    assert len(gets) == 1
