import assert from "node:assert/strict";
import { after, before, test } from "node:test";

import React from "react";
import { act, create } from "react-test-renderer";
import { createServer } from "vite";

let server, Verify, api;
before(async () => {
  server = await createServer({
    server: { middlewareMode: true, hmr: false, ws: false, watch: null },
    appType: "custom",
  });
  Verify = (await server.ssrLoadModule("/src/screens/Verify.tsx")).default;
  api = (await server.ssrLoadModule("/src/api.ts")).api;
});
after(async () => { await server?.close(); });

// A length-gated response has no detection statistic or invented p-value.
// These tests exercise UI state, not the watermark detection pipeline.
const result = {
  result_id: "short-input", requested_at: "2026-11-17T09:00:00Z",
  token_count: 3, scored_token_count: 0, fpr_target: 0.01,
  band: "insufficient_evidence", band_reason: "The text is too short to score.",
  best_match: null, candidates: [], caveats: ["short_text"],
  engine: { markwright_version: "0.1.0", mode: "online" },
};

async function mount(t) {
  api.samples = async () => ({ samples: [{
    id: "short", label: "Short example", kind: "text", text: "A short example",
    why: "Below the minimum length.", expect: "insufficient evidence",
  }], notes: [] });
  api.verifyText = async () => result;
  api.verifyIds = async () => result;
  let view;
  await act(async () => { view = create(React.createElement(Verify)); });
  t.after(() => act(() => view.unmount()));
  await act(async () => {
    view.root.findByType("textarea").props.onChange({ target: { value: "A short example" } });
  });
  return view.root;
}

const verifyButton = (root) => root.findAllByType("button")
  .find((button) => button.props.children === "Verify");
const cards = (root) => root.findAllByProps({ "data-testid": "result-card" });
const errors = (root) => root.findAllByProps({ className: "error" });

const changes = {
  text: (root) => root.findByType("textarea").props.onChange({ target: { value: "Different text" } }),
  mode: (root) => root.findAllByProps({ type: "radio" })[1].props.onChange(),
  target: (root) => root.findByProps({ placeholder: "deployment value" })
    .props.onChange({ target: { value: "0.001" } }),
};

for (const [name, change] of Object.entries(changes)) {
  test(`changing ${name} clears evidence from the previous verification`, async (t) => {
    const root = await mount(t);
    await act(async () => { await verifyButton(root).props.onClick(); });
    assert.equal(cards(root).length, 1);
    await act(async () => { change(root); });
    assert.equal(cards(root).length, 0);
  });

  test(`changing ${name} clears the previous request's error`, async (t) => {
    const root = await mount(t);
    api.verifyText = async () => { throw new Error("Request failed"); };
    await act(async () => { await verifyButton(root).props.onClick(); });
    assert.equal(errors(root).length, 1);
    await act(async () => { change(root); });
    assert.equal(errors(root).length, 0);
  });
}

test("verification locks every input and example until its response arrives", async (t) => {
  const root = await mount(t);
  let resolve;
  let submitted;
  api.verifyText = (text) => {
    submitted = text;
    return new Promise((done) => { resolve = done; });
  };
  await act(async () => { void verifyButton(root).props.onClick(); });
  const controls = () => root.findAll((node) => ["textarea", "input", "button"].includes(node.type));
  assert.equal(submitted, "A short example");
  assert.ok(controls().every((node) => node.props.disabled === true));
  assert.equal(cards(root).length, 0);
  await act(async () => { resolve(result); });
  assert.ok(controls().every((node) => !node.props.disabled));
  assert.equal(cards(root).length, 1);
});

test("a failed verification unlocks the controls for retry", async (t) => {
  const root = await mount(t);
  api.verifyText = async () => { throw new Error("Request failed"); };
  await act(async () => { await verifyButton(root).props.onClick(); });
  assert.equal(root.findByType("textarea").props.disabled, false);
  assert.equal(verifyButton(root).props.disabled, false);
  assert.equal(errors(root).length, 1);
  api.verifyText = async () => result;
  await act(async () => { await verifyButton(root).props.onClick(); });
  assert.equal(errors(root).length, 0);
  assert.equal(cards(root).length, 1);
});
