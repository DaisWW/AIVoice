export function voiceOwnerLabel(state, voice) {
  if (state.isAdmin) return voice.owner_id;
  if (voice.source_kind === "legacy") return "服务器素材";
  return voice.can_edit ? "我的声音" : "共享只读";
}
