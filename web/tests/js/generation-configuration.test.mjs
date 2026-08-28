import assert from "node:assert/strict";
import test from "node:test";

import { GenerationConfigurationController } from "../../static/js/generation/configuration-controller.js";
import { generationSeedPlan } from "../../static/js/generation/seed-plan.js";


test("generation configuration restore filters stale choices and clamps counts", () => {
  const state = { generationConfigurations: [], voices: [], config: { models: [] } };
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

test("generation configuration restore and add cap unique combinations at four", () => {
  const state = { generationConfigurations: [], voices: [], config: { models: [] } };
  const controller = new GenerationConfigurationController(state, {
    storePosition() {},
    toast() {},
  });
  const voices = [{ id: "voice-1", enabled_file_count: 1 }, { id: "voice-2", enabled_file_count: 1 }];
  const models = [{ id: "model-1", available: true }, { id: "model-2", available: true }];
  state.voices = voices;
  state.config.models = models;

  controller.restore([
    { voiceId: "voice-1", modelId: "model-1" },
    { voiceId: "voice-1", modelId: "model-1" },
    { voiceId: "voice-2", modelId: "model-2" },
    { voiceId: "voice-1", modelId: "model-2" },
    { voiceId: "voice-2", modelId: "model-1" },
    { voiceId: "voice-2", modelId: "model-1" },
  ], voices, models);

  assert.equal(state.generationConfigurations.length, 4);
  assert.equal(new Set(state.generationConfigurations.map((item) => `${item.voiceId}:${item.modelId}`)).size, 4);
});

test("generation seed plan shares base seed and stride across candidate counts", () => {
  const plan = generationSeedPlan([
    { candidateCount: 1 },
    { candidateCount: 3 },
  ], () => 0.25);

  assert.deepEqual(plan, { baseSeed: 500_000_000, seedStride: 3 });
});
