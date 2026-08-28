export function generationSeedPlan(configurations, random = Math.random) {
  const candidateCounts = configurations.map((configuration) => (
    Math.min(4, Math.max(1, Number(configuration?.candidateCount) || 1))
  ));
  return {
    baseSeed: Math.floor(random() * 2_000_000_000),
    seedStride: Math.max(1, ...candidateCounts),
  };
}
