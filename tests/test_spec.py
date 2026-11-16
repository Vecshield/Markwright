"""OWM 0.1 conformance tests: the schemas are normative, descriptors must
validate against them, installed adapters must agree with their descriptors
(code and spec cannot drift apart), the modality extension must hold at the
data-model level, and the repo declaration must be public-safe and signed."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import jsonschema
import pytest

from .conftest import OP, VERIFIER, bootstrap_parity_config

REPO = Path(__file__).parent.parent
SCHEMAS = REPO / "schemas"
DESCRIPTOR_SCHEMA = json.loads((SCHEMAS / "scheme-descriptor.json").read_text())
DECLARATION_SCHEMA = json.loads((SCHEMAS / "repo-declaration.json").read_text())

INSTALLED = {
    "synthid-text": REPO / "src/markwright/schemes/descriptors/synthid-text.owm.json",
    "kgw": REPO / "packages/markwright-scheme-kgw/src/markwright_scheme_kgw/kgw.owm.json",
}
IMAGE_EXEMPLARS = sorted((REPO / "docs/spec/examples").glob("*.owm.json"))


# ---- the schemas themselves are valid JSON Schema ------------------------------------------
@pytest.mark.parametrize("schema", [DESCRIPTOR_SCHEMA, DECLARATION_SCHEMA],
                         ids=["scheme-descriptor", "repo-declaration"])
def test_normative_schemas_are_valid_2020_12(schema):
    jsonschema.Draft202012Validator.check_schema(schema)


# ---- every shipped descriptor conforms ------------------------------------------------------
ALL_DESCRIPTORS = {**{k: v for k, v in INSTALLED.items()},
                   **{p.stem: p for p in IMAGE_EXEMPLARS}}


@pytest.mark.parametrize("path", ALL_DESCRIPTORS.values(), ids=list(ALL_DESCRIPTORS.keys()))
def test_descriptor_conforms_to_owm(path):
    doc = json.loads(path.read_text())
    jsonschema.validate(doc, DESCRIPTOR_SCHEMA)
    # a descriptor's own params schema must itself be valid JSON Schema
    jsonschema.Draft202012Validator.check_schema(doc["parameters"]["schema"])


# ---- code and spec cannot drift: adapters match their descriptors ---------------------------
def test_installed_adapters_agree_with_their_descriptors():
    from markwright import schemes
    from markwright.schemes.descriptors import descriptor_for

    for scheme_id in ("synthid-text", "kgw"):
        adapter = schemes.get(scheme_id)
        desc = descriptor_for(scheme_id)
        assert desc is not None, f"{scheme_id}: descriptor must be discoverable"
        assert desc["version"] == adapter.scheme_version
        # the registry validates configs against the ADAPTER schema; the spec
        # publishes the DESCRIPTOR schema; they must be identical
        assert desc["parameters"]["schema"] == adapter.params_schema()
        assert desc["parameters"]["defaults"] == adapter.default_params()
        caps = adapter.capabilities
        assert desc["key_model"]["publishable_detector"] == caps.publishable_detector
        assert desc["embedding"]["distortion_free"] == caps.distortion_free
        assert desc["detection"]["cost"] == caps.detect_cost
        assert desc["modality"] == "text" and desc["carrier"]["unit"] == "token"
        assert desc["status"] == "implemented"


# ---- modality extension holds at the data-model level ---------------------------------------
def test_image_exemplars_exercise_every_image_stage():
    docs = [json.loads(p.read_text()) for p in IMAGE_EXEMPLARS]
    assert len(docs) >= 3
    assert all(d["modality"] == "image" for d in docs)
    stages = {d["embedding"]["stage"] for d in docs}
    # the three ways an image mark can exist: in the weights, in the initial
    # latent, or stamped on afterwards
    assert {"component_weights", "latent_init", "posthoc"} <= stages
    units = {d["carrier"]["unit"] for d in docs}
    assert {"pixel", "latent"} <= units
    # none of them is claimed as implemented
    assert all(d["status"] == "specification-only" for d in docs)
    # a posthoc image scheme must bind the universal posthoc surface
    posthoc = next(d for d in docs if d["embedding"]["stage"] == "posthoc")
    assert any(i["surface"] == "posthoc.transform" for i in posthoc["integrations"])


def test_vocabulary_is_closed_but_extensible():
    base = json.loads(INSTALLED["synthid-text"].read_text())
    # x- extensions pass for modality, unit, stage and surface
    doc = copy.deepcopy(base)
    doc["modality"] = "x-3d-mesh"
    doc["carrier"]["unit"] = "x-vertex"
    doc["embedding"]["stage"] = "x-mesh_deform"
    doc["integrations"] = [{"surface": "x-blender.plugin", "stage": "x-mesh_deform",
                            "status": "planned", "adapter": None}]
    jsonschema.validate(doc, DESCRIPTOR_SCHEMA)
    # unregistered values without the x- prefix fail
    for mutate in (
        lambda d: d.__setitem__("modality", "hologram"),
        lambda d: d["carrier"].__setitem__("unit", "voxel"),
        lambda d: d["embedding"].__setitem__("stage", "weights"),
        lambda d: d["integrations"][0].__setitem__("surface", "my-framework.hook"),
    ):
        bad = copy.deepcopy(doc)
        mutate(bad)
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(bad, DESCRIPTOR_SCHEMA)


# ---- the serving-framework mapping is computable from data ----------------------------------
def test_binding_rule_frameworks_expose_stages_schemes_require_them():
    """A surface can run a scheme iff the binding's stage equals the scheme's
    embedding stage. Every shipped descriptor must satisfy this internally."""
    for name, path in ALL_DESCRIPTORS.items():
        doc = json.loads(path.read_text())
        stage = doc["embedding"]["stage"]
        for binding in doc["integrations"]:
            if binding["status"] == "supported":
                assert binding["stage"] == stage, (
                    f"{name}: supported binding on {binding['surface']} declares stage "
                    f"{binding['stage']} but the scheme embeds at {stage}")


# ---- API surface ----------------------------------------------------------------------------
def test_descriptor_endpoint_serves_conformant_documents(api):
    client, _ = api
    r = client.get("/v1/schemes/synthid-text/descriptor", headers=VERIFIER)
    assert r.status_code == 200
    jsonschema.validate(r.json(), DESCRIPTOR_SCHEMA)
    assert client.get("/v1/schemes/no-such/descriptor", headers=VERIFIER).status_code == 404
    # the test-only parity scheme has no descriptor; the reason is stated
    r2 = client.get("/v1/schemes/parity-test/descriptor", headers=VERIFIER)
    assert r2.status_code == 404 and "parity-test" in r2.text


def test_catalogue_carries_modality(api):
    client, _ = api
    entries = client.get("/v1/schemes/catalogue", headers=VERIFIER).json()
    by_id = {e["scheme_id"]: e for e in entries}
    assert by_id["synthid-text"]["modality"] == "text"
    assert by_id["synthid-text"]["descriptor_available"] is True


# ---- repo declaration: public-safe, fingerprinted, signed -----------------------------------
def test_repo_declaration_is_conformant_safe_and_signed(api):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    client, _ = api
    config_id = bootstrap_parity_config(client)
    r = client.get(f"/v1/configs/{config_id}/declaration", headers=VERIFIER)
    assert r.status_code == 200
    doc = r.json()
    jsonschema.validate(doc, DECLARATION_SCHEMA)

    m = doc["marking"][0]
    assert m["config_fingerprint"].startswith("sha256:")
    # public-safe: no key references, no sealed material, params withheld by default
    flat = json.dumps(doc)
    assert "key_ref" not in flat and "sealed" not in flat and "lineage" not in flat
    assert m["params"] is None and m["spoofing_not_excluded"] is False
    assert m["active_from"] is not None     # the config is active

    # the signature verifies with the embedded deployment key
    unsigned = {k: v for k, v in doc.items() if k not in ("signature", "public_key_hex")}
    payload = json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode()
    pub = Ed25519PublicKey.from_public_bytes(bytes.fromhex(doc["public_key_hex"]))
    pub.verify(bytes.fromhex(doc["signature"]), payload)   # raises on mismatch

    # a tampered declaration no longer verifies
    tampered = dict(unsigned)
    tampered["marking"] = copy.deepcopy(unsigned["marking"])
    tampered["marking"][0]["config_fingerprint"] = "sha256:" + "0" * 64
    bad = json.dumps(tampered, sort_keys=True, separators=(",", ":")).encode()
    with pytest.raises(Exception):
        pub.verify(bytes.fromhex(doc["signature"]), bad)


def test_declaration_discloses_params_only_when_detector_is_public(api):
    client, _ = api
    config_id = bootstrap_parity_config(client)
    client.post(f"/v1/configs/{config_id}/detector:publish", headers=OP)
    doc = client.get(f"/v1/configs/{config_id}/declaration", headers=VERIFIER).json()
    m = doc["marking"][0]
    assert m["params"] is not None                      # disclosure is now public
    assert m["spoofing_not_excluded"] is True           # and the flag says so
    assert m["verification"]["detector"]["sha256"]
    jsonschema.validate(doc, DECLARATION_SCHEMA)
