import assert from "node:assert/strict";
import test from "node:test";

import { GenerationConfigurationController } from "../../static/js/generation/configuration-controller.js";


test("generation configuration restore filters stale choices and clamps counts", () => {
  const state = { generationConfigurations: [] };
  const controller = new GenerationConfigurationController(state, {
    storePosition() {},
    toast() {},
  });

  controller.restore(
    [
      { voiceId: "voice-1", modelId: "model-1", candidateCount: 9 },
      { voiceId: "missing", modelId: "model-1", candidateCount: 2 },
      { voiceId: "voice-1", modelId: "missing", candidateCount: 2 },
    ],
    [{ id: "voice-1" }],
    [{ id: "model-1" }],
  );

  assert.deepEqual(state.generationConfigurations, [
    { id: "1", voiceId: "voice-1", modelId: "model-1", candidateCount: 4 },
  ]);
});
