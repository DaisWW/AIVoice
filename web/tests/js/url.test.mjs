import assert from "node:assert/strict";
import test from "node:test";

import { safeResourceUrl } from "../../static/js/core/url.js";


globalThis.window = { location: { origin: "https://voice.test" } };


test("safeResourceUrl accepts only same-origin HTTP resources", () => {
  assert.equal(safeResourceUrl("/api/audio/example"), "https://voice.test/api/audio/example");
  assert.equal(
    safeResourceUrl("https://voice.test/api/download"),
    "https://voice.test/api/download",
  );
  assert.equal(safeResourceUrl("javascript:alert(1)"), "");
  assert.equal(safeResourceUrl("https://example.com/audio.wav"), "");
  assert.equal(safeResourceUrl("data:audio/wav;base64,AAAA"), "");
});
